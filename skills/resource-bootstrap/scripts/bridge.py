#!/usr/bin/env python3
"""Plan and apply scoped dependency-source rewrites, without network access."""
import argparse
import base64
import binascii
import configparser
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile
import urllib.parse
import uuid

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SKIP = {".git", ".agent", ".agents", ".codex", ".opencode", ".ssh", ".aws", ".kube",
        "node_modules", "vendor", "__pycache__", ".venv", "venv"}
URL = re.compile(r"https?://[^\s\"'<>\\)]+")
NETWORK_COMMAND = re.compile(r"\b(?:pip3?\s+install|go\s+(?:mod\s+download|build|test)|cargo\s+(?:fetch|build|test)|mvn\b|gradle\b|apt(?:-get)?\s+(?:update|install)|apk\s+add|npm\s+(?:ci|install))")
DEFAULT_NPM = "https://registry.npmjs.org/"


class BridgeError(Exception):
    pass


def sibling(name):
    filename = "bridge_io" if name == "io" else name
    spec = importlib.util.spec_from_file_location("bridge_" + name, HERE / (filename + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def problem(code, source, message, fix):
    return {"rule_id": code, "source": source, "violation": message,
            "expected": "explicit, supported internal source mapping", "suggested_fix": fix}


def relative(value):
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or any(ord(c) < 32 for c in value) or value.startswith("/")
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise BridgeError("path must be a normalized repo-relative path")
    return value


def safe_path(root, value):
    path = root
    for part in relative(value).split("/"):
        path = path / part
        if path.is_symlink():
            raise BridgeError("symlink paths are unsupported")
    return path


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BridgeError("duplicate JSON key")
        result[key] = value
    return result


def read_json(path):
    try:
        data = path.read_bytes()
        if len(data) > 4 * 1024 * 1024:
            raise BridgeError("JSON file exceeds 4 MiB")
        return json.loads(data, object_pairs_hook=unique_keys), digest(data)
    except (OSError, UnicodeError, ValueError):
        raise BridgeError("required JSON file is missing or invalid")


def valid_url(value, target=False):
    if not isinstance(value, str) or any(c.isspace() for c in value) or any(c in value for c in "\\$?#\"'`;|&()<>{}"):
        raise BridgeError("URL must be literal and contain no credentials, query or fragment")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
        if (parsed.scheme not in ({"https"} if target else {"http", "https"}) or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError()
    except ValueError:
        raise BridgeError("source URL is unsupported or target is not credential-free HTTPS")
    return value


def load_config(root, name):
    config, config_hash = read_json(safe_path(root, name))
    if not isinstance(config, dict) or set(config) != {"schema_version", "resource_profile", "mappings"}:
        raise BridgeError("bridge config requires schema_version, resource_profile and mappings")
    if type(config["schema_version"]) is not int or config["schema_version"] != 1 or not isinstance(config["mappings"], list):
        raise BridgeError("invalid bridge schema version or mappings")
    routes = {}
    for item in config["mappings"]:
        if not isinstance(item, dict) or not {"kind", "original", "resolved", "evidence"}.issubset(item) or set(item) - {"kind", "original", "resolved", "evidence", "integrity"}:
            raise BridgeError("invalid mapping fields")
        if item["kind"] not in ("npm", "git", "artifact") or not isinstance(item["evidence"], str) or not item["evidence"].strip():
            raise BridgeError("mapping kind or evidence is missing")
        valid_url(item["original"])
        valid_url(item["resolved"], target=True)
        if item["original"] == item["resolved"]:
            raise BridgeError("mapping must name an explicit replacement")
        if item["kind"] == "artifact":
            if not isinstance(item.get("integrity"), str) or not re.fullmatch(r"(?:sha256:[0-9a-f]{64}|sha512-[A-Za-z0-9+/]+={0,2})", item["integrity"]):
                raise BridgeError("artifact mapping requires sha256 or npm sha512 integrity")
            if item["integrity"].startswith("sha512-"):
                try:
                    payload = item["integrity"][7:]
                    raw_digest = base64.b64decode(payload, validate=True)
                    if len(raw_digest) != 64 or base64.b64encode(raw_digest).decode("ascii") != payload:
                        raise ValueError()
                except (ValueError, binascii.Error):
                    raise BridgeError("sha512 integrity must encode exactly 64 digest bytes")
        elif "integrity" in item:
            raise BridgeError("integrity applies only to exact artifact mappings")
        key = (item["kind"], item["original"])
        if key in routes:
            raise BridgeError("duplicate source mapping")
        routes[key] = item
    for item in routes.values():
        if (item["kind"], item["resolved"]) in routes:
            raise BridgeError("mapping chains are unsupported; supply the final internal target")
        if item["kind"] == "artifact" and any(other["kind"] == "artifact" and other["resolved"] == item["resolved"]
                and other["integrity"] != item["integrity"] for other in routes.values()):
            raise BridgeError("one artifact target has conflicting integrity declarations")
    resolver = sibling("resource")
    profile, profile_hash = None, None
    if config["resource_profile"] is not None:
        raw, profile_hash = read_json(safe_path(root, config["resource_profile"]))
        try:
            profile = resolver.validate_profile(raw, "resource profile")
        except resolver.ConfigError:
            raise BridgeError("resource profile is invalid")
        if profile["direct_registries"]:
            raise BridgeError("bridge profile must have no direct-registry fallbacks; use confirmed internal projects")
    return routes, profile, resolver, config_hash, profile_hash


def inventory(root):
    files, findings, hashes = {}, [], {}
    count = 0
    def failure(error):
        findings.append(problem("BRIDGE_SCAN", "repository", "directory scan failed", "check scan permissions"))
    for base, dirs, names in os.walk(root, followlinks=False, onerror=failure):
        for directory in dirs:
            path = Path(base, directory)
            if directory not in SKIP and path.is_symlink():
                findings.append(problem("BRIDGE_SYMLINK", path.relative_to(root).as_posix(), "symlink directory was not inspected", "inspect its target and provide supported in-root sources"))
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not Path(base, d).is_symlink())
        for name in sorted(names):
            path = Path(base, name)
            rel = path.relative_to(root).as_posix()
            if name in {"AGENTS.md", "CLAUDE.md"} or name.startswith(".env"):
                continue
            count += 1
            if count > 10000:
                raise BridgeError("scan exceeds 10000 files; use a bounded project root")
            if path.is_symlink():
                findings.append(problem("BRIDGE_SYMLINK", rel, "symlink file was not inspected", "inspect its target and provide a supported in-root file"))
                continue
            try:
                if not stat.S_ISREG(path.stat().st_mode):
                    findings.append(problem("BRIDGE_FILE_TYPE", rel, "non-regular file was not inspected", "use regular source files in the project root"))
                    continue
                with path.open("rb") as handle:
                    raw = handle.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise BridgeError("file exceeds 4 MiB")
                hashes[rel] = digest(raw)
                files[rel] = raw.decode("utf-8")
            except UnicodeError:
                # Binary files are hashed for drift, but not treated as source text.
                continue
            except OSError:
                findings.append(problem("BRIDGE_SCAN", rel, "file could not be read", "restore readable source before planning"))
    return files, hashes, findings


def scan(root, files, resolver):
    sources, findings = [], []
    seen = set()
    def add(kind, value, path, start=None, end=None, mechanism="literal", integrity=None):
        location = path + (":" + str(files[path].count("\n", 0, start) + 1) if start is not None else "")
        if kind != "container":
            try:
                valid_url(value)
            except BridgeError:
                findings.append(problem("BRIDGE_DYNAMIC", location, "source is dynamic, credential-bearing or unsupported", "resolve the literal non-secret source before rewriting"))
                return
        key = (kind, value, path, start, mechanism)
        if key in seen:
            return
        seen.add(key)
        sources.append({"id": digest(encoded(key))[:16], "kind": kind, "original": value,
                        "path": path, "location": location, "start": start, "end": end,
                        "mechanism": mechanism, "integrity": integrity})
    images, unused, docker_findings = resolver.analyze(str(root))
    findings.extend(docker_findings)
    for image in images:
        path, number = image["source"].rsplit(":", 1)
        if path not in files:
            continue
        text = files[path]
        offset = sum(len(line) for line in text.splitlines(keepends=True)[:int(number) - 1])
        # The resource analyzer has already validated FROM grammar and stage identity.
        index = text.find(image["value"], offset)
        if index < 0:
            findings.append(problem("BRIDGE_SPAN", image["source"], "cannot locate image token", "use a supported Dockerfile instruction"))
        else:
            add("container", image["value"], path, index, index + len(image["value"]))
    for path, text in files.items():
        name = Path(path).name
        is_docker = name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith(".Dockerfile")
        if name == ".npmrc":
            for match in re.finditer(r"(?m)^([ \t]*(?:@[^\s:=]+:)?registry[ \t]*=[ \t]*)([^\r\n]*)", text):
                value = match[2].strip()
                add("npm", value, path, match.start(2), match.start(2) + len(match[2]), "npmrc")
            for credential in re.finditer(r"(?im)(?:_auth\w*|password)\s*=([^\r\n]*)", text):
                if not re.fullmatch(r"\s*\$\{[A-Za-z_][A-Za-z0-9_]*\}\s*", credential[1]):
                    findings.append(problem("BRIDGE_SECRET", path, "credential-bearing npm config requires local handling", "use credential references; do not save secret values in plans"))
        if name == ".gitmodules":
            try:
                git_config = configparser.ConfigParser(interpolation=None, strict=True)
                git_config.read_string(text)
                if any(not section.startswith("submodule ") for section in git_config.sections()):
                    raise configparser.Error()
            except configparser.Error:
                findings.append(problem("BRIDGE_GITMODULES", path, "submodule configuration syntax is unsupported", "validate the native Git submodule config before rewriting"))
                continue
            for match in re.finditer(r"(?m)^[ \t]*url[ \t]*=[ \t]*([^\r\n]+)", text):
                add("git", match[1].strip(), path, match.start(1), match.end(1), "gitmodules")
        if name in {"package.json", "package-lock.json", "npm-shrinkwrap.json"}:
            try:
                parsed = json.loads(text, object_pairs_hook=unique_keys)
            except (ValueError, BridgeError):
                findings.append(problem("BRIDGE_JSON", path, "invalid npm JSON", "repair the manifest or lockfile"))
                continue
            if not isinstance(parsed, dict):
                findings.append(problem("BRIDGE_JSON", path, "npm manifest must be a JSON object", "repair the manifest structure"))
                continue
            if name == "package.json":
                config_path = (Path(path).parent / ".npmrc").as_posix()
                if not re.search(r"(?m)^\s*registry\s*=", files.get(config_path, "")):
                    add("npm", DEFAULT_NPM, path, mechanism="npm-default:" + config_path)
                for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies", "overrides"):
                    values = parsed.get(field, {})
                    serialized = json.dumps(values)
                    direct = re.search(r"https?://|git\+|ssh://|github:|gitlab:|bitbucket:", serialized)
                    shorthand = isinstance(values, dict) and any(isinstance(value, str) and re.fullmatch(r"[\w.-]+/[\w.-]+(?:#.*)?", value) for value in values.values())
                    if direct or shorthand:
                        findings.append(problem("BRIDGE_MANIFEST_SOURCE", path + ":" + field, "dependency has a direct source requiring a native adapter", "preserve dependency identity while resolving its explicit transport"))
                scripts = parsed.get("scripts", {})
                if isinstance(scripts, dict) and any(isinstance(command, str) and (NETWORK_COMMAND.search(command) or re.search(r"\b(?:curl|wget|npx)\b", command)) for command in scripts.values()):
                    findings.append(problem("BRIDGE_LIFECYCLE", path + ":scripts", "lifecycle script may fetch additional sources", "inspect the command's execution context and add a targeted adapter"))
            else:
                integrities = {}
                def walk(value):
                    if isinstance(value, dict):
                        if isinstance(value.get("resolved"), str):
                            if value["resolved"] in integrities and integrities[value["resolved"]] != value.get("integrity"):
                                findings.append(problem("BRIDGE_INTEGRITY", path, "one artifact URL has conflicting integrity values", "resolve the lockfile inconsistency without changing dependency identity"))
                            integrities[value["resolved"]] = value.get("integrity")
                        for child in value.values(): walk(child)
                    elif isinstance(value, list):
                        for child in value: walk(child)
                walk(parsed)
                tokens = list(re.finditer(r'"(?:\\.|[^"\\])*"', text))
                for token in tokens:
                    if json.loads(token[0]) != "resolved": continue
                    tail = re.match(r"\s*:\s*", text[token.end():])
                    if not tail: continue
                    start = token.end() + tail.end()
                    value, length = json.JSONDecoder().raw_decode(text[start:])
                    if isinstance(value, str) and value.startswith(("http://", "https://")):
                        add("artifact", value, path, start, start + length, "json-string", integrities.get(value))
                    elif isinstance(value, str) and not value.startswith("file:"):
                        findings.append(problem("BRIDGE_LOCK_SOURCE", path, "lockfile has a non-HTTP source", "use a native adapter for git or other lock source types"))
        if name in {"go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts", "pyproject.toml", "Pipfile", "uv.lock", "NuGet.config"} or re.fullmatch(r"requirements.*\.txt", name):
            findings.append(problem("BRIDGE_NATIVE_TRANSPORT", path, "this ecosystem needs a native transport adapter", "read bridge-ecosystems.md; implement and test the relevant adapter before full verification"))
        if name.endswith((".yml", ".yaml")) or name in {"config.toml", "settings.xml"}:
            findings.append(problem("BRIDGE_STRUCTURED_CONFIG", path, "structured build configuration is outside this adapter's scope", "inspect images, services, repositories and templates with the native parser"))
        if path.endswith(".sh") or is_docker:
            offset = 0
            for line in text.splitlines(keepends=True):
                if line.lstrip().startswith("#"):
                    offset += len(line); continue
                if NETWORK_COMMAND.search(line):
                    findings.append(problem("BRIDGE_IMPLICIT_SOURCE", path + ":" + str(text.count("\n", 0, offset) + 1), "command may use an implicit package or OS source", "configure its effective transport in this execution context and add a native adapter"))
                if re.search(r"\b(?:curl|wget)\b", line):
                    matches = list(URL.finditer(line))
                    simple = re.match(r"^\s*(?:RUN\s+)?(?:curl|wget)\b", line)
                    try:
                        tokens = shlex.split(line)
                    except ValueError:
                        tokens = []
                    unsafe_options = {"--proxy", "-x", "--config", "-K", "--data", "-d", "--header", "-H", "--url", "--post-data", "--input-file", "-i"}
                    if (not simple or len(matches) != 1 or line.rstrip().endswith("\\") or not tokens
                            or any(char in line for char in "|;&`")
                            or any(token.split("=", 1)[0] in unsafe_options for token in tokens)):
                        findings.append(problem("BRIDGE_DOWNLOAD_SYNTAX", path, "download command is dynamic or multiline", "resolve the exact artifact and add a syntax-aware adapter"))
                    else:
                        match = matches[0]
                        add("artifact", match[0], path, offset + match.start(), offset + match.end(), "download")
                offset += len(line)
    return sources, findings


def make_plan(root, config_name):
    routes, profile, resolver, config_hash, profile_hash = load_config(root, config_name)
    files, hashes, findings = inventory(root)
    sources, detected = scan(root, files, resolver)
    findings.extend(detected)
    edits, creates = {}, {}
    for source in sources:
        kind, original = source["kind"], source["original"]
        mapping = routes.get((kind, original))
        resolved = None
        try:
            if kind == "container":
                if profile is None: raise BridgeError("container source requires a resource profile")
                resolved = resolver.resolve_image(profile, original)["image"]
            elif mapping:
                resolved = mapping["resolved"]
                if kind == "artifact" and source["integrity"] and source["integrity"] != mapping["integrity"]:
                    raise BridgeError("artifact mapping differs from lockfile integrity")
            elif any(item["kind"] == kind and item["resolved"] == original for item in routes.values()):
                if kind == "artifact" and source["integrity"] and not any(item["kind"] == kind and item["resolved"] == original
                        and item["integrity"] == source["integrity"] for item in routes.values()):
                    raise BridgeError("internal lockfile artifact differs from declared integrity")
                resolved = original
            else:
                raise BridgeError("source has no exact mapping")
        except (BridgeError, resolver.ResolutionError) as exc:
            findings.append(problem("BRIDGE_UNMAPPED", source["location"], str(exc), "provide an evidenced exact mapping; no hostname guessing"))
        source["resolved"] = resolved
        source["status"] = "unresolved" if resolved is None else ("internal" if original == resolved else "rewrite")
        if resolved is None or resolved == original: continue
        if source["mechanism"].startswith("npm-default:"):
            path = source["mechanism"].split(":", 1)[1]
            existing = files.get(path, "")
            suffix = ("\n" if existing and not existing.endswith("\n") else "") + "registry=" + resolved + "\n"
            creates[path] = {"start": len(existing), "end": len(existing), "original": "", "replacement": suffix}
        else:
            replacement = json.dumps(resolved) if source["mechanism"] == "json-string" else resolved
            start, end = source["start"], source["end"]
            edits.setdefault(source["path"], []).append({"start": start, "end": end,
                "original": files[source["path"]][start:end], "replacement": replacement})
    for path, edit in creates.items(): edits.setdefault(path, []).append(edit)
    if edits:
        original = files.get(".gitignore", "")
        line = ".agent/bridge-backups/"
        if line not in original.splitlines():
            edits.setdefault(".gitignore", []).append({"start": len(original), "end": len(original), "original": "",
                "replacement": ("\n" if original and not original.endswith("\n") else "") + line + "\n"})
    changes = [{"path": path, "before_sha256": hashes.get(path), "edits": sorted(items, key=lambda item: item["start"])} for path, items in sorted(edits.items())]
    for change in changes:
        previous_end = -1
        for edit in change["edits"]:
            if edit["start"] < previous_end: raise BridgeError("overlapping edits need a native adapter")
            previous_end = edit["end"]
    return {"schema_version": 1, "kind": "bridge-plan", "repo_root": str(root), "config": config_name,
            "config_sha256": config_hash, "resource_profile_sha256": profile_hash,
            "inventory_sha256": digest(encoded(hashes)), "status": "blocked" if findings else "ready",
            "findings": findings, "sources": sources, "changes": changes, "runtime": "not_run"}


def save_metadata(root, name, data):
    path = safe_path(root, name)
    if path.exists() and not path.is_file(): raise BridgeError("metadata path is not a regular file")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".bridge-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded(data) + b"\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--config", default=".agent/bridge.json")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("analyze")
    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--save", action="store_true", help="also save .agent/bridge.plan.json; default only prints JSON")
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("--plan", default=".agent/bridge.plan.json")
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--static", action="store_true", help="explicitly verify only supported static source forms")
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("--transaction", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)
    try:
        if root.is_symlink() or not root.is_dir(): raise BridgeError("root must be an existing real directory")
        root = root.resolve()
        if args.command == "restore":
            io = sibling("io")
            result = io.restore_changes(root, args.transaction)
            # A restored source tree has no current bridge lock.
            lock = safe_path(root, ".agent/bridge.lock.json")
            if lock.exists():
                old, unused = read_json(lock)
                if old.get("transaction_id") == args.transaction: lock.unlink()
            print(json.dumps(dict(result, schema_version=1)))
            return 0
        if args.command == "analyze":
            files, hashes, findings = inventory(root)
            sources, extra = scan(root, files, sibling("resource"))
            print(json.dumps({"schema_version": 1, "status": "attention" if findings or extra else "analyzed",
                "sources": sources, "findings": findings + extra, "runtime": "not_run"}, sort_keys=True))
            return 1 if findings or extra else 0
        plan = make_plan(root, relative(args.config))
        if args.command == "plan":
            if args.save: save_metadata(root, ".agent/bridge.plan.json", plan)
            print(json.dumps(plan, sort_keys=True))
            return 1 if plan["findings"] else 0
        if args.command == "verify":
            findings = list(plan["findings"])
            if plan["changes"]:
                findings.append(problem("BRIDGE_PENDING_REWRITE", "repository", "some sources still need rewriting", "review and apply a current bridge plan"))
            static = "fail" if findings else "pass"
            if not args.static:
                findings.append(problem("BRIDGE_RUNTIME_UNVERIFIED", "runtime", "isolated runtime verification is not performed by this offline CLI", "run native restore/build/test under enforced egress policy; retain real witness logs"))
            print(json.dumps({"schema_version": 1, "status": "static_pass" if not findings else "incomplete",
                "static": static, "runtime": "not_run", "findings": findings, "inventory_sha256": plan["inventory_sha256"]}, sort_keys=True))
            return 1 if findings else 0
        stored, unused = read_json(safe_path(root, relative(args.plan)))
        if stored != plan: raise BridgeError("plan is stale or changed; regenerate and review it")
        if plan["findings"]: raise BridgeError("blocked plan cannot be applied")
        changes = []
        for change in plan["changes"]:
            path = safe_path(root, change["path"])
            text = path.read_bytes().decode("utf-8") if path.exists() else ""
            for edit in reversed(change["edits"]):
                if text[edit["start"]:edit["end"]] != edit["original"]: raise BridgeError("edit anchor changed")
                text = text[:edit["start"]] + edit["replacement"] + text[edit["end"]:]
            changes.append({"path": change["path"], "before_sha256": change["before_sha256"], "after": text})
        if not changes:
            print(json.dumps({"schema_version": 1, "status": "no_changes", "runtime": "not_run"}))
            return 0
        io = sibling("io")
        transaction = "bridge-" + uuid.uuid4().hex
        # Check the lock location before the source transaction begins.
        lock_path = safe_path(root, ".agent/bridge.lock.json")
        if lock_path.exists() and not lock_path.is_file(): raise BridgeError("invalid bridge lock path")
        result = io.apply_changes(root, changes, transaction)
        try:
            save_metadata(root, ".agent/bridge.lock.json", {"schema_version": 1, "transaction_id": transaction,
                "config_sha256": plan["config_sha256"], "sources": plan["sources"], "runtime": "not_run"})
        except (OSError, BridgeError):
            io.restore_changes(root, transaction)
            raise BridgeError("lock write failed; source changes were restored")
        print(json.dumps(dict(result, schema_version=1, runtime="not_run"), sort_keys=True))
        return 0
    except Exception as exc:
        message = str(exc) if isinstance(exc, BridgeError) or type(exc).__name__ == "BridgeIOError" else "bridge operation failed safely"
        print(json.dumps({"schema_version": 1, "status": "error", "findings": [problem("BRIDGE_ERROR", "bridge", message, "inspect local state and regenerate the plan")]}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
