#!/usr/bin/env python3
"""Run a small, deterministic verification harness from a JSON configuration."""

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = 1
MAX_OUTPUT_BYTES = 64 * 1024
TOP_LEVEL_FIELDS = {"schema_version", "owner", "checks"}
CHECK_FIELDS = {"id", "argv", "source", "expected", "suggested_fix", "timeout_seconds", "cwd"}


def _finding(rule_id: str, source: str, violation: str, expected: str, suggested_fix: str) -> Dict[str, str]:
    return {"rule_id": rule_id, "source": source, "violation": violation,
            "expected": expected, "suggested_fix": suggested_fix}


def _report(status: str, findings: List[Dict[str, str]], checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "status": status, "findings": findings, "checks": checks}


def _error_report(message: str, source: str = "configuration") -> Dict[str, Any]:
    return _report("error", [_finding("CONFIG", source, message, "A valid harness configuration", "Fix the configuration and run verification again")], [])


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s must be a non-empty string" % name)
    return value


def _reject_duplicate_keys(pairs: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key: %s" % key)
        result[key] = value
    return result


def load_config(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle, object_pairs_hook=_reject_duplicate_keys)
    except FileNotFoundError as exc:
        raise ValueError("configuration file not found: %s" % exc.filename)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("cannot read configuration: %s" % exc)
    if not isinstance(value, dict):
        raise ValueError("configuration must be a JSON object")
    if set(value) != TOP_LEVEL_FIELDS:
        unknown = sorted(set(value) - TOP_LEVEL_FIELDS)
        missing = sorted(TOP_LEVEL_FIELDS - set(value))
        detail = []
        if unknown:
            detail.append("unknown field(s): " + ", ".join(unknown))
        if missing:
            detail.append("missing field(s): " + ", ".join(missing))
        raise ValueError("invalid top-level fields (" + "; ".join(detail) + ")")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("schema_version must be 1")
    _string(value["owner"], "owner")
    checks = value["checks"]
    if not isinstance(checks, list) or not checks:
        raise ValueError("checks must be a non-empty array")
    seen = set()
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise ValueError("checks[%d] must be an object" % index)
        unknown = sorted(set(check) - CHECK_FIELDS)
        if unknown:
            raise ValueError("checks[%d] has unknown field(s): %s" % (index, ", ".join(unknown)))
        for field in ("id", "source", "expected", "suggested_fix"):
            _string(check.get(field), "checks[%d].%s" % (index, field))
        if check["id"] in seen:
            raise ValueError("duplicate check id: %s" % check["id"])
        seen.add(check["id"])
        argv = check.get("argv")
        if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv):
            raise ValueError("checks[%d].argv must be a non-empty array of non-empty strings" % index)
        timeout = check.get("timeout_seconds", 120)
        try:
            finite_timeout = math.isfinite(timeout)
        except (OverflowError, TypeError):
            finite_timeout = False
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not finite_timeout or timeout <= 0:
            raise ValueError("checks[%d].timeout_seconds must be a positive finite number" % index)
        cwd = check.get("cwd", ".")
        if not isinstance(cwd, str) or not cwd or "\x00" in cwd or os.path.isabs(cwd):
            raise ValueError("checks[%d].cwd must be a non-empty relative directory" % index)
    return value


def _read_output(handle: Any) -> str:
    handle.seek(0)
    data = handle.read(MAX_OUTPUT_BYTES + 1)
    truncated = len(data) > MAX_OUTPUT_BYTES
    if truncated:
        data = data[:MAX_OUTPUT_BYTES]
    text = data.decode("utf-8", errors="replace")
    return text + ("\n[output truncated]" if truncated else "")


def run_config(config: Dict[str, Any], config_path: str) -> Tuple[Dict[str, Any], int]:
    config_dir = os.path.dirname(os.path.abspath(config_path))
    findings: List[Dict[str, str]] = []
    results: List[Dict[str, Any]] = []
    had_error = False
    for check in config["checks"]:
        check_id = check["id"]
        workdir = os.path.abspath(os.path.join(config_dir, check.get("cwd", ".")))
        result: Dict[str, Any] = {"id": check_id, "status": "error", "exit_code": None, "output": ""}
        try:
            with tempfile.TemporaryFile() as output_file:
                try:
                    process = subprocess.Popen(
                        check["argv"], cwd=workdir, stdout=output_file, stderr=subprocess.STDOUT,
                        shell=False, start_new_session=(os.name == "posix"),
                    )
                except (FileNotFoundError, PermissionError, OSError) as exc:
                    had_error = True
                    result["output"] = str(exc)
                    violation = ("invalid working directory: %s" % exc
                                 if not os.path.isdir(workdir)
                                 else "could not execute command: %s" % exc)
                    findings.append(_finding(check_id, check["source"], violation,
                                             check["expected"], check["suggested_fix"]))
                else:
                    try:
                        process.wait(timeout=check.get("timeout_seconds", 120))
                        result["exit_code"] = process.returncode
                        result["output"] = _read_output(output_file)
                        if process.returncode == 0:
                            result["status"] = "pass"
                        else:
                            result["status"] = "fail"
                            findings.append(_finding(check_id, check["source"], "command exited with code %s" % process.returncode,
                                                     check["expected"], check["suggested_fix"]))
                    except subprocess.TimeoutExpired:
                        had_error = True
                        if os.name == "posix":
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        else:
                            process.kill()
                        process.wait()
                        result["exit_code"] = process.returncode
                        result["status"] = "error"
                        result["output"] = _read_output(output_file)
                        findings.append(_finding(check_id, check["source"], "command timed out",
                                                 check["expected"], check["suggested_fix"]))
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
            had_error = True
            result["output"] = str(exc)
            findings.append(_finding(check_id, check["source"], "invalid working directory: %s" % exc,
                                     check["expected"], check["suggested_fix"]))
        results.append(result)
    status = "error" if had_error else ("fail" if findings else "pass")
    return _report(status, findings, results), (2 if had_error else (1 if findings else 0))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic checks from a harness JSON configuration")
    parser.add_argument("--config", default="harness.json", help="configuration path (default: harness.json)")
    parser.add_argument("--format", choices=("json", "text"), default="text", help="report format (default: text)")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        report, code = run_config(config, args.config)
    except ValueError as exc:
        report, code = _error_report(str(exc), args.config), 2
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print("status: %s" % report["status"])
        for finding in report["findings"]:
            print("rule_id: %s" % finding["rule_id"])
            print("source: %s" % finding["source"])
            print("violation: %s" % finding["violation"])
            print("expected: %s" % finding["expected"])
            print("suggested_fix: %s" % finding["suggested_fix"])
        for check in report["checks"]:
            print("%s: %s" % (check["id"], check["status"]))
            if check["output"]:
                print("output: %s" % check["output"])
    return code


if __name__ == "__main__":
    sys.exit(main())
