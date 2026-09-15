#!/usr/bin/env python3
"""Focused stdlib-only tests for resource.py; no external network."""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.error

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("resource", HERE / "resource.py")
resource = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resource)


def profile(endpoint="https://registry.internal.example"):
    return {"schema_version": 1, "resources": {"reg": {"kind": "registry", "endpoint": endpoint,
            "evidence": "approved", "credentials": {"token": {"source": "env", "name": "RESOURCE_TEST_TOKEN"}}}},
            "image_routes": {"docker.io": {"registry_resource": "reg", "namespace": "cache", "confirmed_proxy": True, "evidence": "approved"}},
            "push_targets": {"app": {"registry_resource": "reg", "namespace": "apps"}}, "direct_registries": ["quay.io"]}


class Handler(BaseHTTPRequestHandler):
    target_hits = 0
    mode = "ok"
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path == "/target":
            type(self).target_hits += 1; self.send_response(200); self.end_headers(); return
        if self.path == "/slow": time.sleep(2); self.send_response(200); self.end_headers(); return
        if self.path == "/v2/":
            modes = {"ok": (200, True), "unauth": (401, True), "noheader": (401, False), "forbidden": (403, False), "missing": (404, False), "redirect": (302, False)}
            code, header = modes[type(self).mode]
            self.send_response(code)
            if header: self.send_header("Docker-Distribution-API-Version", "registry/2.0")
            if code == 302: self.send_header("Location", "http://127.0.0.1:%d/target" % self.server.server_port)
            self.end_headers(); return
        self.send_response(404); self.end_headers()


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.root = Path(self.temp); (self.root / ".agent").mkdir()
    def tearDown(self):
        shutil.rmtree(self.temp)
    def write(self, name, value):
        path = self.root / ".agent" / name; path.write_text(json.dumps(value), encoding="utf-8"); return path
    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out): code = resource.main(["--root", str(self.root), *args])
        return code, json.loads(out.getvalue())
    def loop_profile(self, probe=None):
        value = profile("http://127.0.0.1:9")
        if probe: value["resources"]["reg"]["probe_path"] = probe
        self.write("resources.json", value)

    def test_duplicate_unknown_and_raw_secret_rejected(self):
        p = self.root / ".agent" / "resources.json"
        p.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        code, out = self.cli("list"); self.assertEqual(2, code); self.assertEqual("error", out["status"])
        bad = profile(); bad["resources"]["reg"]["credentials"]["token"] = {"source": "literal", "name": "secret"}
        self.write("resources.json", bad); code, out = self.cli("list")
        self.assertEqual(2, code); self.assertNotIn("secret", json.dumps(out))

    def test_duplicate_key_does_not_echo_credential_like_text(self):
        p = self.root / ".agent" / "resources.json"
        p.write_text('{"x":"RAW_TOKEN_DO_NOT_ECHO","x":"again"}', encoding="utf-8")
        code, out = self.cli("list")
        self.assertEqual(2, code)
        self.assertNotIn("RAW_TOKEN_DO_NOT_ECHO", json.dumps(out))

    def test_resolve_mapping_shorthand_namespaced_port_digest_and_push(self):
        value = profile("https://registry.internal.example:5443"); self.write("resources.json", value)
        code, out = self.cli("resolve", "--image", "alpine"); self.assertEqual(0, code); self.assertEqual("registry.internal.example:5443/cache/library/alpine:latest", out["image"])
        code, out = self.cli("resolve", "--image", "docker.io/alpine:3"); self.assertEqual(0, code); self.assertEqual("registry.internal.example:5443/cache/library/alpine:3", out["image"])
        code, out = self.cli("resolve", "--image", "docker.io/team/tool:1"); self.assertEqual(0, code); self.assertEqual("registry.internal.example:5443/cache/team/tool:1", out["image"])
        digest = "sha256:" + "a" * 64
        code, out = self.cli("resolve", "--image", "quay.io/a/tool@" + digest); self.assertEqual(0, code); self.assertEqual("quay.io/a/tool@" + digest, out["image"])
        code, out = self.cli("resolve", "--image", "service:3", "--push-target", "app"); self.assertEqual(0, code); self.assertEqual("registry.internal.example:5443/apps/service:3", out["image"])
        code, out = self.cli("resolve", "--image", "quay.io/a/tool", "--push-target", "app"); self.assertEqual(1, code)
        code, out = self.cli("resolve", "--image", "team/service:4", "--push-target", "app"); self.assertEqual(0, code)
        self.assertEqual("registry.internal.example:5443/apps/team/service:4", out["image"])

    def test_source_registry_port_is_parsed_independently_of_repository(self):
        value = profile("https://destination.internal.example:5443")
        value["direct_registries"].append("source.internal.example:5000")
        self.write("resources.json", value)
        code, out = self.cli("resolve", "--image", "source.internal.example:5000/team/tool:1")
        self.assertEqual(0, code)
        self.assertEqual("source.internal.example:5000/team/tool:1", out["image"])

    def test_unknown_has_no_public_fallback_and_credentials_never_values(self):
        self.write("resources.json", profile()); os.environ["RESOURCE_TEST_TOKEN"] = "do-not-print"
        try:
            code, out = self.cli("resolve", "--image", "ghcr.io/a/b:1"); self.assertEqual(1, code)
            code, out = self.cli("list"); self.assertEqual(0, code); self.assertNotIn("do-not-print", json.dumps(out))
        finally: os.environ.pop("RESOURCE_TEST_TOKEN", None)

    def test_analyze_multistage_dynamic_and_dockerfile_check(self):
        (self.root / "Dockerfile").write_text("FROM alpine AS build\nRUN true\nFROM build AS next\nFROM ${BASE}\nFROM scratch\nFROM docker.io/library/alpine:3.20\n")
        self.write("resources.json", profile())
        code, out = self.cli("analyze"); self.assertEqual(1, code); self.assertEqual(2, len(out["requirements"]))
        self.write("resource-plan.json", {"schema_version": 1, "requirements": []})
        code, out = self.cli("check", "--dockerfiles"); self.assertEqual(1, code)
        (self.root / "Dockerfile").write_text("FROM registry.internal.example/cache/library/alpine:3.20\n")
        code, out = self.cli("check", "--dockerfiles"); self.assertEqual(0, code)

    def test_optional_plan_does_not_block_required_does(self):
        self.write("resources.json", profile())
        base = {"schema_version": 1, "requirements": [{"id": "x", "required": False, "kind": "resource", "value": "missing", "reason": "hint", "source": "test"}]}
        self.write("resource-plan.json", base); code, out = self.cli("check"); self.assertEqual(0, code); self.assertEqual("attention", out["status"])
        base["requirements"][0]["required"] = True; self.write("resource-plan.json", base); code, out = self.cli("check"); self.assertEqual(1, code)

    def test_optional_unresolved_id_prefix_does_not_block_distinct_required_id(self):
        self.write("resources.json", profile())
        plan = {"schema_version": 1, "requirements": [
            {"id": "base", "required": False, "kind": "resource", "value": "missing", "reason": "optional", "source": "test"},
            {"id": "base-required", "required": True, "kind": "resource", "value": "reg", "reason": "needed", "source": "test"}]}
        self.write("resource-plan.json", plan)
        code, out = self.cli("check")
        self.assertEqual(0, code)
        self.assertEqual("attention", out["status"])

    def test_strict_schema_paths_and_dockerfile_input_fail_closed(self):
        value = profile(); value["schema_version"] = True; self.write("resources.json", value)
        code, out = self.cli("list"); self.assertEqual(2, code)
        value = profile(); value["schema_version"] = 1.0; self.write("resources.json", value)
        code, out = self.cli("list"); self.assertEqual(2, code)
        value = profile(); value["resources"]["reg"]["probe_path"] = "//other-host/path"; self.write("resources.json", value)
        code, out = self.cli("list"); self.assertEqual(2, code)
        self.write("resources.json", profile())
        (self.root / "Dockerfile").write_bytes(b"FROM alpine\xff\n")
        self.write("resource-plan.json", {"schema_version": 1, "requirements": []})
        code, out = self.cli("check", "--dockerfiles"); self.assertEqual(1, code)
        code, out = self.cli("resolve", "--image", "${BASE}"); self.assertEqual(1, code)

    def test_namespace_validation_and_proxy_push_project_collision(self):
        value = profile(); value["image_routes"]["docker.io"]["namespace"] = "Bad Namespace"
        self.assertRaises(resource.ConfigError, resource.validate_profile, value, "test")
        value = profile(); value["push_targets"]["app"]["namespace"] = "cache"
        self.assertRaises(resource.ConfigError, resource.validate_profile, value, "test")

    def test_non_registry_endpoint_path_is_preserved_and_probe_is_origin_relative(self):
        value = profile()
        item = value["resources"].pop("reg")
        item.update({"kind": "http", "endpoint": "http://127.0.0.1:9/base", "probe_path": "/health"})
        value["resources"] = {"web": item}; value["image_routes"] = {}; value["push_targets"] = {}
        parsed = resource.validate_profile(value, "test")
        self.assertEqual("http://127.0.0.1:9/base", parsed["resources"]["web"]["endpoint"])

    def test_relative_profile_and_plan_paths_are_resolved_from_root(self):
        (self.root / "profiles").mkdir(); (self.root / "plans").mkdir()
        (self.root / "profiles/p.json").write_text(json.dumps(profile()), encoding="utf-8")
        (self.root / "plans/p.json").write_text(json.dumps({"schema_version": 1, "requirements": []}), encoding="utf-8")
        code, out = self.cli("--profile", "profiles/p.json", "list")
        self.assertEqual(0, code); self.assertEqual("configured", out["status"])
        code, out = self.cli("--profile", "profiles/p.json", "--plan", "plans/p.json", "check")
        self.assertEqual(0, code); self.assertEqual("ok", out["status"])

    def test_dockerfile_malformed_trailing_symlink_and_missing_from_are_gaps(self):
        (self.root / "Dockerfile.bad").write_text("FROM --broken image\nFROM alpine \\", encoding="utf-8")
        (self.root / "Dockerfile.empty").write_text("# no build stage\n", encoding="utf-8")
        target = self.root / "outside-dockerfile"; target.write_text("FROM alpine\n", encoding="utf-8")
        (self.root / "Dockerfile.link").symlink_to(target)
        code, out = self.cli("analyze")
        self.assertEqual(1, code)
        rules = {item["rule_id"] for item in out["findings"]}
        self.assertTrue({"invalid-from", "incomplete-dockerfile", "scan-symlink", "missing-from"}.issubset(rules))

    def test_project_without_a_dockerfile_has_no_invented_image_requirement(self):
        code, out = self.cli("analyze")
        self.assertEqual(0, code)
        self.assertEqual([], out["requirements"])

    def test_doctor_classification_uses_observed_status_and_case_insensitive_header(self):
        class Queue:
            def __init__(self, result): self.result = result
            def get(self, timeout=None): return self.result
            def close(self): pass
        class Process:
            def __init__(self, target, args): self.args = args; self.live = False
            def start(self): pass
            def join(self, timeout=None): pass
            def is_alive(self): return self.live
            def terminate(self): self.live = False
        class Context:
            def __init__(self, result): self.result = result
            def Queue(self, size): return Queue(self.result)
            def Process(self, target, args): return Process(target, args)
        reg = resource.validate_profile(profile("http://127.0.0.1:9"), "test")["resources"]["reg"]
        cases = [(("http", 200, {"docker-distribution-api-version": "registry/2.0"}), "ok"),
                 (("http", 401, {"Docker-Distribution-API-Version": "registry/2.0"}), "unverified"),
                 (("http", 401, {}), "unverified"), (("http", 403, {}), "unverified"),
                 (("http", 302, {"Location": "http://bad.invalid"}), "unverified"), (("timeout", None, {}), "unverified")]
        for result, expected in cases:
            with mock.patch.object(resource.multiprocessing, "get_context", return_value=Context(result)):
                status, findings, details = resource.doctor(reg, 1)
            self.assertEqual(expected, status)
            self.assertNotIn("bad.invalid", json.dumps(findings))

    def test_redirect_handler_refuses_redirects_without_requesting_target(self):
        handler = resource.NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "http://127.0.0.1/target"))

    def test_probe_child_classifies_transport_failures_and_keeps_headers_minimal(self):
        class Queue:
            def __init__(self): self.items = []
            def put(self, value): self.items.append(value)
        class Response:
            status = 200
            headers = {"Docker-Distribution-API-Version": "registry/2.0", "Authorization": "Bearer RESPONSE_SECRET"}
            def __enter__(self): return self
            def __exit__(self, *args): pass
        class Opener:
            def __init__(self, outcome): self.outcome = outcome
            def open(self, request, timeout):
                if isinstance(self.outcome, BaseException): raise self.outcome
                return self.outcome
        for error, expected in ((urllib.error.URLError(socket.gaierror()), "dns"),
                                (urllib.error.URLError(ssl.SSLError()), "tls"),
                                (urllib.error.URLError(socket.timeout()), "timeout")):
            queue = Queue()
            with mock.patch.object(resource.urllib.request, "build_opener", return_value=Opener(error)):
                resource._probe_child("http://127.0.0.1:9/v2/", 1, queue)
            self.assertEqual(expected, queue.items[0][0])
        queue = Queue()
        with mock.patch.object(resource.urllib.request, "build_opener", return_value=Opener(Response())):
            resource._probe_child("http://127.0.0.1:9/v2/", 1, queue)
        self.assertEqual(("http", 200, {"docker-distribution-api-version": "registry/2.0"}), queue.items[0])


@unittest.skipUnless(os.environ.get("RESOURCE_TEST_NETWORK") == "1", "set RESOURCE_TEST_NETWORK=1 for loopback integration tests")
class LoopbackDoctorTests(unittest.TestCase):
    """Opt-in only: validates urllib/process behavior against a local server."""
    def setUp(self):
        self.temp = tempfile.mkdtemp(); self.root = Path(self.temp); (self.root / ".agent").mkdir()
        Handler.target_hits = 0; self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2); shutil.rmtree(self.temp)
    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out): code = resource.main(["--root", str(self.root), *args])
        return code, json.loads(out.getvalue())
    def write_profile(self):
        value = profile("http://127.0.0.1:%d" % self.server.server_port)
        (self.root / ".agent/resources.json").write_text(json.dumps(value), encoding="utf-8")
    def test_registry_http_statuses_redirect_and_timeout(self):
        self.write_profile()
        for mode, expected, exit_code in (("ok", "ok", 0), ("unauth", "unverified", 1), ("noheader", "unverified", 1), ("forbidden", "unverified", 1), ("missing", "unverified", 1), ("redirect", "unverified", 1)):
            Handler.mode = mode; Handler.target_hits = 0
            got, out = self.cli("doctor", "--resource", "reg", "--timeout", "1")
            self.assertEqual(exit_code, got); self.assertEqual(expected, out["status"]); self.assertEqual(0, Handler.target_hits)


if __name__ == "__main__": unittest.main(verbosity=2)
