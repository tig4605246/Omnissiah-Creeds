#!/usr/bin/env python3
"""Offline-first resource configuration helper (Python 3.9+; stdlib only)."""
import argparse
import ipaddress
import json
import multiprocessing
import os
import re
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCHEMA = 1
KINDS = {"registry", "git", "kubernetes", "package-index", "http"}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SHA256 = re.compile(r"^sha256:[0-9a-fA-F]{64}$")


class ConfigError(Exception):
    pass


class ResolutionError(Exception):
    pass


def finding(rule_id, source, violation, expected, suggested_fix):
    return {"rule_id": rule_id, "source": source, "violation": violation,
            "expected": expected, "suggested_fix": suggested_fix}


def emit(status, findings=None, **extra):
    data = {"schema_version": SCHEMA, "status": status, "findings": findings or []}
    data.update(extra)
    print(json.dumps(data, sort_keys=True, separators=(",", ":")))


def no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ConfigError("duplicate JSON key")
        out[key] = value
    return out


def load_json(path, label):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=no_duplicates)
    except FileNotFoundError:
        raise ConfigError("%s not found: %s" % (label, path))
    except (OSError, UnicodeError, ValueError):
        raise ConfigError("invalid or unreadable %s" % label)


def exact_keys(value, keys, source):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ConfigError("%s must contain exactly: %s" % (source, ", ".join(keys)))


def clean_string(value, source, nonempty=True):
    if not isinstance(value, str) or (nonempty and not value):
        raise ConfigError("%s must be a %sstring" % (source, "non-empty " if nonempty else ""))
    if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConfigError("%s must not contain surrounding whitespace or control characters" % source)
    return value


def registry_host(value):
    clean_string(value, "registry host")
    if any(char in value for char in "/\\@?#%"):
        raise ConfigError("registry must be a bare host with optional port")
    try:
        parts = urllib.parse.urlsplit("//" + value)
        host, port = parts.hostname, parts.port
        if not host or value.endswith(":") or (port is not None and not 1 <= port <= 65535):
            raise ValueError()
        if ":" in host:
            ipaddress.IPv6Address(host)
            result = "[" + host.lower() + "]"
        else:
            if any(not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?", label)
                   for label in host.split(".")):
                raise ValueError()
            result = host.lower()
        return result + (":" + str(port) if port is not None else "")
    except ValueError:
        raise ConfigError("invalid registry hostname or port")


def endpoint_info(value, kind, source):
    value = clean_string(value, source)
    if "\\" in value:
        raise ConfigError("%s must not contain backslashes" % source)
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
    except ValueError:
        raise ConfigError("%s has an invalid URL port" % source)
    host = parts.hostname
    if not host or parts.username is not None or parts.password is not None:
        raise ConfigError("%s must not include URL userinfo and needs a host" % source)
    host = host.lower()
    allowed_http = parts.scheme == "http" and host in LOOPBACK
    if parts.scheme != "https" and not allowed_http:
        raise ConfigError("%s must use HTTPS (HTTP is only allowed for literal loopback)" % source)
    if "?" in value or "#" in value:
        raise ConfigError("%s must not include query or fragment" % source)
    if kind == "registry" and parts.path not in ("", "/"):
        raise ConfigError("%s registry endpoint must be an HTTPS origin" % source)
    authority = registry_host(parts.netloc)
    origin = "%s://%s" % (parts.scheme, authority)
    return origin, host


def validate_profile(data, source):
    exact_keys(data, ("schema_version", "resources", "image_routes", "push_targets", "direct_registries"), source)
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA:
        raise ConfigError("%s schema_version must be 1" % source)
    for field in ("resources", "image_routes", "push_targets"):
        if not isinstance(data[field], dict):
            raise ConfigError("%s.%s must be an object" % (source, field))
    if not isinstance(data["direct_registries"], list):
        raise ConfigError("%s.direct_registries must be an array" % source)
    resources = {}
    for rid, raw in data["resources"].items():
        clean_string(rid, "resources key")
        if not isinstance(raw, dict):
            raise ConfigError("resource %s must be an object" % rid)
        allowed = {"kind", "endpoint", "evidence", "probe_path", "context", "namespace", "credentials"}
        if not {"kind", "endpoint", "evidence"}.issubset(raw) or set(raw) - allowed:
            raise ConfigError("resource %s has missing or unknown fields" % rid)
        kind = raw["kind"]
        if not isinstance(kind, str) or kind not in KINDS:
            raise ConfigError("resource %s has unsupported kind" % rid)
        origin, host = endpoint_info(raw["endpoint"], kind, "resource %s endpoint" % rid)
        clean_string(raw["evidence"], "resource evidence")
        for optional in ("probe_path", "context", "namespace"):
            if optional in raw:
                clean_string(raw[optional], "resource %s %s" % (rid, optional))
        if "probe_path" in raw and (not raw["probe_path"].startswith("/")
                or raw["probe_path"].startswith("//")
                or any(char in raw["probe_path"] for char in "?#\\")):
            raise ConfigError("resource %s probe_path must be an absolute path without query/fragment" % rid)
        credentials = raw.get("credentials", {})
        if not isinstance(credentials, dict):
            raise ConfigError("resource %s credentials must be an object" % rid)
        for key, cref in credentials.items():
            clean_string(key, "credential key")
            exact_keys(cref, ("source", "name"), "credential %s" % key)
            if cref["source"] != "env" or not isinstance(cref["name"], str) or not ENV_NAME.match(cref["name"]):
                raise ConfigError("credential %s must be an environment-variable reference" % key)
        endpoint = origin if kind == "registry" else origin + urllib.parse.urlsplit(raw["endpoint"]).path
        resources[rid] = dict(raw, endpoint=endpoint, _host=host, _origin=origin)
    direct = [registry_host(host) for host in data["direct_registries"]]
    if len(set(direct)) != len(direct):
        raise ConfigError("direct_registries has duplicate hosts")
    project_uses = {}
    normalized_routes = {}
    for field, namespace_name in (("image_routes", "namespace"), ("push_targets", "namespace")):
        for name, route in data[field].items():
            clean_string(name, "%s key" % field)
            expected = ("registry_resource", namespace_name, "confirmed_proxy", "evidence") if field == "image_routes" else ("registry_resource", namespace_name)
            exact_keys(route, expected, "%s %s" % (field, name))
            if field == "image_routes":
                normalized = registry_host(name)
                if normalized in normalized_routes:
                    raise ConfigError("duplicate normalized image route")
                normalized_routes[normalized] = route
                if route.get("confirmed_proxy") is not True:
                    raise ConfigError("image route %s must have confirmed_proxy true" % name)
            rid = route["registry_resource"]
            if not isinstance(rid, str) or rid not in resources or resources[rid]["kind"] != "registry":
                raise ConfigError("%s %s must reference a registry resource" % (field, name))
            clean_string(route[namespace_name], "%s %s namespace" % (field, name))
            if not re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", route[namespace_name]):
                raise ConfigError("namespace must be one lowercase project name")
            project = (resources[rid]["endpoint"], route[namespace_name])
            previous = project_uses.get(project)
            if previous and (previous != field or field == "image_routes"):
                raise ConfigError("a proxy project cannot serve multiple upstreams or be a push target")
            project_uses[project] = field
            if field == "image_routes": clean_string(route["evidence"], "image route %s evidence" % name)
    return {"resources": resources, "image_routes": normalized_routes,
            "push_targets": data["push_targets"], "direct_registries": direct}


def validate_plan(data, source):
    exact_keys(data, ("schema_version", "requirements"), source)
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA or not isinstance(data["requirements"], list):
        raise ConfigError("%s has invalid schema_version or requirements" % source)
    seen = set()
    for item in data["requirements"]:
        exact_keys(item, ("id", "required", "kind", "value", "reason", "source"), "plan requirement")
        for key in ("id", "value", "reason", "source"):
            clean_string(item[key], "plan requirement %s" % key)
        if item["id"] in seen: raise ConfigError("plan requirement ids must be unique")
        seen.add(item["id"])
        if not isinstance(item["required"], bool) or not isinstance(item["kind"], str) or item["kind"] not in {"resource", "image", "push"}:
            raise ConfigError("plan requirement has invalid required or kind")
    return data["requirements"]


def split_image(ref, allow_unqualified=True):
    if (not isinstance(ref, str) or not ref or any(char.isspace() for char in ref)
            or any(char in ref for char in "$\\?#") or ref.count("@") > 1):
        raise ResolutionError("image reference is unsupported or dynamic")
    if ref == "scratch":
        raise ResolutionError("scratch is a build stage, not a pullable image")
    base, separator, digest = ref.partition("@")
    digest = digest if separator else None
    if digest and not SHA256.fullmatch(digest):
        raise ResolutionError("only a complete sha256 digest is supported")
    if separator and not digest:
        raise ResolutionError("digest cannot be empty")
    last = base.rsplit("/", 1)[-1]
    tag = last.rsplit(":", 1)[1] if ":" in last else None
    if tag is not None:
        base = base[:-(len(tag) + 1)]
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
            raise ResolutionError("image tag has unsupported syntax")
    parts = base.split("/")
    first = parts[0]
    explicit = len(parts) > 1 and ("." in first or ":" in first or first == "localhost" or first.startswith("["))
    if explicit:
        try:
            host = registry_host(first)
        except ConfigError:
            raise ResolutionError("image registry host or port is invalid")
        repo = "/".join(parts[1:])
    else:
        host, repo = "docker.io", base
        if not allow_unqualified:
            raise ResolutionError("image must specify its registry")
    if any(not re.fullmatch(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*", part) for part in repo.split("/")):
        raise ResolutionError("image repository must use supported lowercase path components")
    if host == "docker.io" and "/" not in repo:
        repo = "library/" + repo
    if not digest and tag is None:
        tag = "latest"
    return host, repo, tag, digest


def rendered(repo, tag, digest):
    return repo + ((":" + tag) if tag else "") + (("@" + digest) if digest else "")


def resolve_image(profile, ref):
    host, repo, tag, digest = split_image(ref)
    # A Dockerfile may already name a configured proxy/push namespace.  Treat
    # that spelling as resolved so `check --dockerfiles` is safely idempotent.
    for routes, label in ((profile["image_routes"], "proxy"), (profile["push_targets"], "push-target")):
        for name, configured in routes.items():
            endpoint_host = profile["resources"][configured["registry_resource"]]["endpoint"].split("://", 1)[1].lower()
            prefix = configured["namespace"] + "/"
            if host == endpoint_host and repo.startswith(prefix):
                return {"input": ref, "resolution": "configured-" + label,
                        "image": host + "/" + rendered(repo, tag, digest),
                        "registry_resource": configured["registry_resource"]}
    route = profile["image_routes"].get(host)
    if route:
        resource = profile["resources"][route["registry_resource"]]
        destination = resource["endpoint"].split("://", 1)[1] + "/" + route["namespace"] + "/" + rendered(repo, tag, digest)
        return {"input": ref, "resolution": "proxy", "image": destination,
                "registry_resource": route["registry_resource"]}
    if host in profile["direct_registries"]:
        return {"input": ref, "resolution": "direct-allowlisted", "image": host + "/" + rendered(repo, tag, digest)}
    raise ResolutionError("no confirmed image route or direct-registry allowlist entry for host %s" % host)


def resolve_push(profile, target, image):
    if target not in profile["push_targets"]: raise ResolutionError("unknown push target")
    host, repo, tag, digest = split_image(image)
    # Pull normalization adds library/; private push preserves the user's path.
    original_first = image.split("/", 1)[0]
    if "/" in image and ("." in original_first or ":" in original_first or original_first == "localhost" or original_first.startswith("[")):
        raise ResolutionError("push image must be relative, without a registry host")
    if host != "docker.io": raise ResolutionError("push image must be relative")
    if "/" not in image:
        repo = repo.removeprefix("library/")
    route = profile["push_targets"][target]
    endpoint = profile["resources"][route["registry_resource"]]["endpoint"].split("://", 1)[1]
    return {"input": image, "push_target": target, "resolution": "push-target",
            "image": endpoint + "/" + route["namespace"] + "/" + rendered(repo, tag, digest),
            "registry_resource": route["registry_resource"]}


def analyze(root, require_dockerfile=False):
    if not Path(root).is_dir():
        raise ConfigError("scan root must be an existing directory")
    excluded = {".git", "node_modules", "vendor", ".agents", ".codex", ".opencode"}
    candidates, hints, findings = [], [], []
    count, entries = 0, 0
    def scan_error(error):
        findings.append(finding("scan-read", "scan root", "a directory could not be scanned", "complete scan", "check directory permissions"))
    for base, dirs, files in os.walk(root, followlinks=False, onerror=scan_error):
        dirs[:] = sorted(d for d in dirs if d not in excluded and not os.path.islink(os.path.join(base, d)))
        entries += len(dirs) + len(files)
        if entries > 20000:
            findings.append(finding("scan-limit", "scan root", "scan exceeds 20000 entries", "bounded complete scan", "narrow the scan root"))
            break
        for name in sorted(files):
            rel = Path(os.path.relpath(os.path.join(base, name), root)).as_posix()
            if name in {"go.mod", "package.json", "pyproject.toml", "requirements.txt", "pom.xml", ".gitlab-ci.yml"}:
                hints.append({"kind": "manifest-marker", "value": name, "source": rel, "required": False})
            is_docker = name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith(".Dockerfile")
            if not is_docker: continue
            count += 1
            if count > 200:
                findings.append(finding("scan-limit", root, "Dockerfile scan limit reached", "at most 200 Dockerfiles", "narrow the scan root")); break
            path = os.path.join(base, name)
            if os.path.islink(path):
                findings.append(finding("scan-symlink", rel, "Dockerfile is a symlink and was not read", "known file within scan scope", "inspect and validate its target explicitly"))
                continue
            try:
                with open(path, "rb") as f:
                    data = f.read(1024 * 1024 + 1)
                if len(data) > 1024 * 1024:
                    findings.append(finding("scan-size", rel, "Dockerfile exceeds 1 MiB", "readable supported Dockerfile", "reduce file size or inspect it manually")); continue
                raw = data.decode("utf-8").splitlines()
            except (OSError, UnicodeError):
                findings.append(finding("scan-read", rel, "Dockerfile could not be read", "readable file", "inspect permissions")); continue
            logical, start, buf = [], 0, ""
            for n, line in enumerate(raw, 1):
                if re.match(r"^\s*#\s*escape\s*=\s*`", line, re.I) or (not line.lstrip().startswith("#") and "<<" in line):
                    findings.append(finding("unsupported-dockerfile", "%s:%d" % (rel, n), "escape directive or heredoc requires a fuller Dockerfile parser", "supported syntax or project-specific validation", "use a parser that preserves this file's build semantics"))
                if line.lstrip().startswith("#"):
                    continue
                if not buf: start = n
                buf += line
                if re.search(r"\\\s*$", buf): buf = re.sub(r"\\\s*$", " ", buf); continue
                logical.append((start, buf)); buf = ""
            if buf:
                findings.append(finding("incomplete-dockerfile", "%s:%d" % (rel, start), "unfinished line continuation", "complete Dockerfile instruction", "complete the instruction before scanning"))
            stages = set()
            from_count = 0
            for line_no, line in logical:
                if not re.match(r"^\s*FROM(?:\s|$)", line, re.I):
                    continue
                from_count += 1
                match = re.fullmatch(r"\s*FROM\s+(?:--platform=[^\s]+\s+)?([^\s]+)(?:\s+AS\s+([a-zA-Z0-9][a-zA-Z0-9_.-]*))?\s*", line, re.I)
                if not match or match.group(1).startswith("--"):
                    findings.append(finding("invalid-from", "%s:%d" % (rel, line_no), "FROM syntax is invalid or unsupported", "complete supported FROM instruction", "check the image, flags and stage alias"))
                    continue
                image, alias = match.group(1), match.group(2)
                source = "%s:%d" % (rel, line_no)
                if "$" in image or "${" in image:
                    findings.append(finding("dynamic-image", source, "image contains an unresolved ARG or variable", "concrete image reference", "resolve the build argument explicitly"))
                elif image.lower() != "scratch" and image.lower() not in stages:
                    try:
                        split_image(image)
                    except ResolutionError:
                        findings.append(finding("invalid-image", source, "image reference syntax is unsupported", "supported concrete image reference", "inspect this FROM locally; preserve its intended version"))
                    else:
                        candidates.append({"id": "image:%s" % source, "kind": "image", "value": image, "source": source, "required": False})
                if alias: stages.add(alias.lower())
            if not from_count:
                findings.append(finding("missing-from", rel, "Dockerfile has no FROM instruction", "at least one build stage", "check that the intended Dockerfile was selected"))
        if count > 200: break
    if require_dockerfile and count == 0:
        findings.append(finding("missing-dockerfile", "scan root", "no Dockerfile was found for the requested gate", "at least one in-scope Dockerfile", "select the correct root or restore the expected Dockerfile"))
    return candidates, hints, findings


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _probe_child(url, timeout, queue):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(urllib.request.Request(url, method="GET"), timeout=timeout) as response:
            queue.put(("http", response.status, {"docker-distribution-api-version": response.headers.get("Docker-Distribution-API-Version", "")}))
    except urllib.error.HTTPError as exc:
        queue.put(("http", exc.code, {"docker-distribution-api-version": (exc.headers or {}).get("Docker-Distribution-API-Version", "")}))
        exc.close()
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.gaierror): label = "dns"
        elif isinstance(reason, ssl.SSLError): label = "tls"
        elif isinstance(reason, (socket.timeout, TimeoutError)): label = "timeout"
        else: label = "tcp"
        queue.put((label, None, {}))
    except (socket.timeout, TimeoutError): queue.put(("timeout", None, {}))
    except ssl.SSLError: queue.put(("tls", None, {}))
    except Exception: queue.put(("tcp", None, {}))


def doctor(resource, timeout):
    kind = resource["kind"]
    if kind == "registry": path = "/v2/"
    elif "probe_path" in resource: path = resource["probe_path"]
    else:
        return "unsupported", [finding("probe-unsupported", resource["endpoint"], "no safe probe path configured", "explicit probe_path for this resource kind", "add probe_path or use its native tool")], {}
    parts = urllib.parse.urlsplit(resource["endpoint"])
    url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    ctx = multiprocessing.get_context("fork") if "fork" in multiprocessing.get_all_start_methods() else multiprocessing.get_context()
    queue = ctx.Queue(1); proc = ctx.Process(target=_probe_child, args=(url, timeout, queue)); proc.start()
    proc.join(timeout + 1)
    if proc.is_alive():
        proc.terminate(); proc.join(1)
        if proc.is_alive():
            proc.kill(); proc.join(1)
        queue.close()
        return "unverified", [finding("timeout", resource["endpoint"], "probe did not finish within bounded time", "reachable endpoint", "check DNS and transport routing")], {"transport": "timeout"}
    try: outcome, code, headers = queue.get(timeout=0.2)
    except Exception: outcome, code, headers = "tcp", None, {}
    finally: queue.close()
    if outcome != "http":
        return "unverified", [finding(outcome, resource["endpoint"], "transport probe failed", "reachable endpoint", "check endpoint and network routing")], {"transport": outcome}
    if code in (301, 302, 303, 307, 308):
        return "unverified", [finding("redirect", resource["endpoint"], "endpoint redirected; redirect was not followed", "direct endpoint", "configure the final HTTPS endpoint explicitly")], {"http_status": code, "transport": "http"}
    if code == 404:
        return "unverified", [finding("not-found", resource["endpoint"], "probe path returned 404", "valid probe path", "check endpoint or probe_path")], {"http_status": code, "transport": "http"}
    if code == 403:
        return "unverified", [finding("forbidden", resource["endpoint"], "endpoint returned 403; authentication was not attempted", "authorized probe", "verify authorization with the appropriate tool")], {"http_status": code, "transport": "http", "authorization": "not_checked"}
    if code not in (200, 401):
        return "unverified", [finding("http-status", resource["endpoint"], "unexpected HTTP status %s" % code, "200 or an expected auth challenge", "check endpoint health")], {"http_status": code, "transport": "http"}
    if kind == "registry":
        header = {key.lower(): value for key, value in headers.items()}.get("docker-distribution-api-version", "")
        confirmed = header.lower() == "registry/2.0"
        details = {"http_status": code, "transport": "http", "protocol": "registry-v2" if confirmed else "unverified", "authorization": "not_checked"}
        if code == 401: details["auth"] = "required"
        if not confirmed:
            return "unverified", [finding("protocol-unverified", resource["endpoint"], "registry API version header was absent or unexpected", "Docker Registry v2 confirmation", "verify with the registry-specific tool")], details
        if code == 401:
            return "unverified", [finding("auth-required", resource["endpoint"], "registry requires authentication; no credentials were sent", "authorized registry access", "use the authorized registry tool")], details
        return "ok", [], details
    if code == 401:
        return "unverified", [finding("auth-required", resource["endpoint"], "endpoint requires authentication; no credentials were sent", "authorized access", "use the appropriate tool")], {"http_status": code, "transport": "http", "protocol": "http", "auth": "required", "authorization": "not_checked"}
    return "ok", [], {"http_status": code, "transport": "http", "protocol": "http", "authorization": "not_checked"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--profile")
    parser.add_argument("--plan")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("analyze"); sub.add_parser("list")
    rs = sub.add_parser("resolve"); rs.add_argument("--image", required=True); rs.add_argument("--push-target")
    cs = sub.add_parser("check"); cs.add_argument("--dockerfiles", action="store_true")
    ds = sub.add_parser("doctor"); ds.add_argument("--resource", required=True); ds.add_argument("--timeout", type=float, default=5)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    profile_path = root / (args.profile or ".agent/resources.json")
    plan_path = root / (args.plan or ".agent/resource-plan.json")
    try:
        if not root.is_dir():
            raise ConfigError("root must be an existing directory")
        if args.command == "analyze":
            candidates, hints, findings = analyze(str(root))
            emit("attention" if findings else "ok", findings, requirements=candidates, hints=hints, validation="offline Dockerfile and manifest-marker scan")
            return 1 if findings else 0
        profile = validate_profile(load_json(profile_path, "profile"), str(profile_path))
        if args.command == "list":
            resources = []
            for rid, resource in profile["resources"].items():
                creds = [{"key": key, "source": "env", "name": c["name"], "present": c["name"] in os.environ} for key, c in resource.get("credentials", {}).items()]
                entry = {"id": rid, "kind": resource["kind"], "endpoint": resource["endpoint"], "credentials": creds,
                         "evidence": resource["evidence"]}
                entry.update({key: resource[key] for key in ("context", "namespace", "probe_path") if key in resource})
                resources.append(entry)
            emit("configured", [], resources=resources, image_routes=profile["image_routes"],
                 push_targets=profile["push_targets"], direct_registries=profile["direct_registries"], validation="configuration only")
            return 0
        if args.command == "resolve":
            result = resolve_push(profile, args.push_target, args.image) if args.push_target else resolve_image(profile, args.image)
            emit("ok", [], **result); return 0
        if args.command == "check":
            requirements = validate_plan(load_json(plan_path, "plan"), str(plan_path))
            findings, results, is_blocking = [], [], False
            for item in requirements:
                try:
                    if item["kind"] == "resource":
                        if item["value"] not in profile["resources"]: raise ResolutionError("unknown resource")
                    elif item["kind"] == "image": resolve_image(profile, item["value"])
                    else:
                        if item["value"] not in profile["push_targets"]: raise ResolutionError("unknown push target")
                except ResolutionError as exc:
                    findings.append(finding("unresolved-" + item["kind"], item["source"], "requirement %s: %s" % (item["id"], str(exc)), "configured resolvable target", "update the selected profile or plan"))
                    is_blocking = is_blocking or item["required"]
                    state = "unresolved"
                else:
                    state = "resolved"
                results.append({"id": item["id"], "required": item["required"], "status": state})
            if args.dockerfiles:
                images, unused_hints, scan_findings = analyze(str(root), require_dockerfile=True)
                findings.extend(scan_findings)
                # Dynamic FROM values are deliberately unresolved rather than
                # guessed. All scan limitations are blocking in this explicit
                # artifact-enforcement mode.
                if scan_findings: is_blocking = True
                for image in images:
                    try:
                        resolved = resolve_image(profile, image["value"])["image"]
                        if image["value"] != resolved:
                            findings.append(finding("dockerfile-not-materialized", image["source"], "FROM %s is not the configured target" % image["value"], resolved, "rewrite this FROM reference to the resolved image"))
                            is_blocking = True
                    except ResolutionError as exc:
                        findings.append(finding("dockerfile-unresolved", image["source"], str(exc), "configured resolved image", "rewrite this FROM reference to a configured target"))
                        is_blocking = True
            emit("unresolved" if is_blocking else ("attention" if findings else "ok"), findings,
                 requirements=results, validation="configuration-only", dockerfiles_checked=args.dockerfiles)
            return 1 if is_blocking else 0
        if not 0 < args.timeout <= 30: raise ConfigError("doctor timeout must be greater than 0 and at most 30 seconds")
        if args.resource not in profile["resources"]: raise ResolutionError("unknown resource")
        status, findings, details = doctor(profile["resources"][args.resource], args.timeout)
        emit(status, findings, resource=args.resource, **details)
        return 0 if status == "ok" else 1
    except ResolutionError as exc:
        emit("unresolved", [finding("unresolved-resource", "request", str(exc), "configured resolvable target", "resolve this resource using confirmed configuration")])
        return 1
    except ConfigError as exc:
        emit("error", [finding("invalid-configuration", "configuration", str(exc), "valid schema_version 1 configuration", "correct the configuration")])
        return 2
    except Exception:
        emit("error", [finding("runtime-error", "runtime", "operation failed safely", "successful operation", "inspect local configuration and retry")])
        return 2


if __name__ == "__main__": sys.exit(main())
