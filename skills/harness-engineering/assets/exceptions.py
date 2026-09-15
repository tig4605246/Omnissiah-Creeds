#!/usr/bin/env python3
"""Validate narrowly scoped policy exceptions using only the standard library."""

import argparse
import datetime
import json
from pathlib import Path, PurePosixPath
import re
import sys


class PolicyError(ValueError):
    """The exception policy cannot be safely applied."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(label + " must be a non-empty string")
    return value


def _source(value):
    _text(value, "source")
    if (PurePosixPath(value).is_absolute() or "\\" in value
            or any(part in ("", ".", "..") for part in value.split("/"))
            or any(char in value for char in "*?[]:\x00\n\r")):
        raise PolicyError("source must be one exact repo-relative POSIX file path: " + repr(value))
    return value


def load_exceptions(path, known_rules, today=None):
    """Validate the full policy before any waiver. Expiry uses the UTC date.

    `today` is a date injection for deterministic tests. Production callers omit it.
    The rule checker must supply its own known rule IDs, not read them from policy.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_object)
    except (OSError, UnicodeError, ValueError) as exc:
        raise PolicyError("cannot read exception policy: " + str(exc)) from exc
    if not isinstance(data, dict) or set(data) != {"version", "exceptions"}:
        raise PolicyError("policy requires exactly version and exceptions")
    if type(data["version"]) is not int or data["version"] != 1:
        raise PolicyError("policy version must be 1")
    if not isinstance(data["exceptions"], list):
        raise PolicyError("exceptions must be an array")
    rules = set(known_rules)
    if not rules or any(not isinstance(rule, str) or not rule.strip() for rule in rules):
        raise PolicyError("checker must supply non-empty known rule IDs")
    today = today if today is not None else datetime.datetime.now(datetime.timezone.utc).date()
    seen = set()
    for item in data["exceptions"]:
        if not isinstance(item, dict) or set(item) != {"rule", "scope", "reason", "owner", "expires"}:
            raise PolicyError("exception requires exactly rule, scope, reason, owner, expires")
        for field in ("rule", "reason", "owner", "expires"):
            _text(item[field], field)
        if item["rule"] not in rules:
            raise PolicyError("unknown rule: " + item["rule"])
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["expires"]):
            raise PolicyError("expires must use YYYY-MM-DD")
        try:
            expiry = datetime.date.fromisoformat(item["expires"])
        except ValueError as exc:
            raise PolicyError("invalid expires date: " + item["expires"]) from exc
        if expiry < today:
            raise PolicyError("expired exception: " + item["rule"] + " / " + item["expires"])
        if not isinstance(item["scope"], list) or not item["scope"]:
            raise PolicyError("scope must be a non-empty array of exact file paths")
        for path in item["scope"]:
            key = (item["rule"], _source(path))
            if key in seen:
                raise PolicyError("duplicate exception scope: " + repr(key))
            seen.add(key)
    return data["exceptions"]


def partition_findings(findings, validated_exceptions):
    """Return (active, waived) findings; accepts output of load_exceptions only.

    Pass policy violations here, never parser/configuration/runtime errors.
    No whole-check suppression and no prefix/glob matching are performed.
    """
    lookup = {(item["rule"], source): item
              for item in validated_exceptions for source in item["scope"]}
    active, waived = [], []
    for finding in findings:
        if not isinstance(finding, dict):
            raise PolicyError("finding must be an object")
        rule = _text(finding.get("rule_id"), "rule_id")
        source = _source(finding.get("source"))
        waiver = lookup.get((rule, source))
        if waiver is None:
            active.append(finding)
        else:
            waived.append({"finding": finding, "exception": waiver})
    return active, waived


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="exception policy JSON path")
    parser.add_argument("--rules", nargs="+", required=True, help="known IDs from the checker")
    args = parser.parse_args()
    try:
        entries = load_exceptions(args.file, args.rules)
    except PolicyError as exc:
        print(json.dumps({"schema_version": 1, "status": "error", "findings": [{
            "rule_id": "EXCEPTION_CONFIG", "source": args.file,
            "violation": str(exc), "expected": "Valid, unexpired, narrowly scoped exceptions",
            "suggested_fix": "Correct the policy using approved scope and ownership; rerun verification"
        }]}, ensure_ascii=False))
        return 2
    print(json.dumps({"schema_version": 1, "status": "pass", "exceptions": len(entries)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
