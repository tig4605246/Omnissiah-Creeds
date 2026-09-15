"""Offline integration tests for the bridge and resource bridge dispatch."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
BRIDGE = HERE / "bridge.py"
RESOURCE = HERE / "resource.py"
INTEGRITY = "sha512-" + ("A" * 86) + "=="


class BridgeCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="bridge-test-")
        self.root = Path(self.temp)
        (self.root / ".agent").mkdir()
        self._write_fixture()

    def tearDown(self):
        shutil.rmtree(self.temp)
        external = getattr(self, "external", None)
        if external is not None:
            shutil.rmtree(external, ignore_errors=True)

    def _write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            path.write_text(value, encoding="utf-8")

    def _write_fixture(self):
        self._write(".agent/resources.json", json.dumps({
            "schema_version": 1,
            "resources": {"registry": {
                "kind": "registry", "endpoint": "https://registry.internal.example",
                "evidence": "approved internal registry",
            }},
            "image_routes": {"docker.io": {
                "registry_resource": "registry", "namespace": "dockerhub",
                "confirmed_proxy": True, "evidence": "approved Docker Hub proxy",
            }},
            "push_targets": {}, "direct_registries": [],
        }, sort_keys=True))
        self._write(".agent/bridge.json", json.dumps({
            "schema_version": 1, "resource_profile": ".agent/resources.json",
            "mappings": [
                {"kind": "npm", "original": "https://registry.npmjs.org/",
                 "resolved": "https://registry.internal.example/npm/", "evidence": "approved npm proxy"},
                {"kind": "artifact", "original": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
                 "resolved": "https://registry.internal.example/npm/left-pad/-/left-pad-1.3.0.tgz",
                 "integrity": INTEGRITY, "evidence": "approved npm artifact mirror"},
                {"kind": "git", "original": "https://github.com/example/tool.git",
                 "resolved": "https://git.internal.example/example/tool.git", "evidence": "approved git mirror"},
            ],
        }, sort_keys=True))
        self._write("Dockerfile", "FROM node:24 AS build\nRUN /bin/true\nFROM build AS final\n")
        self._write("package.json", json.dumps({
            "name": "bridge-fixture", "version": "1.0.0",
            "dependencies": {"left-pad": "1.3.0"},
        }, sort_keys=True) + "\n")
        self._write("package-lock.json", json.dumps({
            "name": "bridge-fixture", "lockfileVersion": 3, "requires": True,
            "packages": {"": {"name": "bridge-fixture", "version": "1.0.0",
                                "dependencies": {"left-pad": "1.3.0"}},
                         "node_modules/left-pad": {"version": "1.3.0",
                            "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
                            "integrity": INTEGRITY}},
        }, sort_keys=True) + "\n")
        self._write(".gitmodules", "[submodule \"tool\"]\n\tpath = tool\n\turl = https://github.com/example/tool.git\n")
        self._write("scripts/install.sh", "#!/bin/sh\ncurl -fsSL https://downloads.example.test/tool-1.0.tgz\n")
        # The download is intentionally unmapped in the normal fixture; tests
        # add it where they need a fully applicable plan.

    def cli(self, script, *args):
        command = [sys.executable, "-I", "-S", str(script), "--root", str(self.root), *args]
        return subprocess.run(command, cwd=self.root, text=True, capture_output=True, timeout=4)

    def output(self, result):
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def add_download_mapping(self):
        config_path = self.root / ".agent/bridge.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["mappings"].append({
            "kind": "artifact", "original": "https://downloads.example.test/tool-1.0.tgz",
            "resolved": "https://registry.internal.example/artifacts/tool-1.0.tgz",
            "integrity": INTEGRITY, "evidence": "approved artifact mirror",
        })
        config_path.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")

    def test_analyze_and_default_plan_are_read_only(self):
        before = self.snapshot()
        analyzed = self.cli(BRIDGE, "analyze")
        self.assertEqual(0, analyzed.returncode)
        data = self.output(analyzed)
        self.assertEqual("analyzed", data["status"])
        planned = self.cli(BRIDGE, "plan")
        self.assertEqual(1, planned.returncode)
        self.assertEqual("blocked", self.output(planned)["status"])
        self.assertEqual(before, self.snapshot())

    def test_saved_plan_apply_preserves_identity_aliases_and_integrity(self):
        self.add_download_mapping()
        original_package = json.loads((self.root / "package.json").read_text())
        original_lock = json.loads((self.root / "package-lock.json").read_text())
        planned = self.cli(BRIDGE, "plan", "--save")
        self.assertEqual(0, planned.returncode, planned.stderr)
        plan = self.output(planned)
        self.assertEqual("ready", plan["status"])
        self.assertEqual("not_run", plan["runtime"])
        applied = self.cli(BRIDGE, "apply")
        self.assertEqual(0, applied.returncode, applied.stderr)
        result = self.output(applied)
        self.assertEqual("not_run", result["runtime"])
        self.assertIn("bridge-backups", result["backup_dir"])
        package = json.loads((self.root / "package.json").read_text())
        lock = json.loads((self.root / "package-lock.json").read_text())
        self.assertEqual(original_package["dependencies"], package["dependencies"])
        self.assertEqual(original_lock["packages"]["node_modules/left-pad"]["version"], lock["packages"]["node_modules/left-pad"]["version"])
        self.assertEqual(INTEGRITY, lock["packages"]["node_modules/left-pad"]["integrity"])
        self.assertIn("FROM registry.internal.example/dockerhub/library/node:24 AS build", (self.root / "Dockerfile").read_text())
        self.assertIn("FROM build AS final", (self.root / "Dockerfile").read_text())
        self.assertIn("https://git.internal.example/example/tool.git", (self.root / ".gitmodules").read_text())

    def test_verify_static_passes_and_runtime_is_not_claimed(self):
        self.add_download_mapping()
        self.assertEqual(0, self.cli(BRIDGE, "plan", "--save").returncode)
        self.assertEqual(0, self.cli(BRIDGE, "apply").returncode)
        static = self.cli(BRIDGE, "verify", "--static")
        self.assertEqual(0, static.returncode, static.stderr)
        self.assertEqual("pass", self.output(static)["static"])
        runtime = self.cli(BRIDGE, "verify")
        self.assertEqual(1, runtime.returncode)
        data = self.output(runtime)
        self.assertEqual("not_run", data["runtime"])
        self.assertNotEqual("verified", data["runtime"])

    def test_unknown_mapping_blocks_entire_apply(self):
        planned = self.cli(BRIDGE, "plan", "--save")
        self.assertEqual(1, planned.returncode)
        before = self.snapshot()
        applied = self.cli(BRIDGE, "apply")
        self.assertEqual(2, applied.returncode)
        self.assertEqual(before, self.snapshot())

    def test_stale_saved_plan_is_rejected_without_writes(self):
        self.add_download_mapping()
        self.assertEqual(0, self.cli(BRIDGE, "plan", "--save").returncode)
        dockerfile = self.root / "Dockerfile"
        dockerfile.write_text(dockerfile.read_text() + "# changed\n")
        before = self.snapshot()
        applied = self.cli(BRIDGE, "apply")
        self.assertEqual(2, applied.returncode)
        self.assertIn("stale", json.dumps(self.output(applied)).lower())
        self.assertEqual(before, self.snapshot())

    def test_fresh_plan_after_apply_is_idempotent(self):
        self.add_download_mapping()
        self.assertEqual(0, self.cli(BRIDGE, "plan", "--save").returncode)
        self.assertEqual(0, self.cli(BRIDGE, "apply").returncode)
        fresh = self.cli(BRIDGE, "plan")
        self.assertEqual(0, fresh.returncode, fresh.stderr)
        data = self.output(fresh)
        self.assertEqual([], data["changes"])
        self.assertEqual("ready", data["status"])

    def test_restore_is_exact_and_retains_backups(self):
        self.add_download_mapping()
        before = self.snapshot()
        self.assertEqual(0, self.cli(BRIDGE, "plan", "--save").returncode)
        applied = self.output(self.cli(BRIDGE, "apply"))
        transaction = applied["transaction_id"]
        restored = self.cli(BRIDGE, "restore", "--transaction", transaction)
        self.assertEqual(0, restored.returncode, restored.stderr)
        after = self.snapshot()
        for relative, content in before.items():
            self.assertEqual(content, after.get(relative), relative)
        self.assertNotIn(".agent/bridge.lock.json", after)
        self.assertNotIn(".agent/bridge.plan.json", before)
        self.assertTrue((self.root / ".agent/bridge-backups" / transaction).is_dir())

    def test_credential_bearing_url_is_not_echoed(self):
        self._write(".gitmodules", "[submodule \"secret\"]\n\tpath = secret\n\turl = https://user:SUPER_SECRET@example.test/repo.git\n")
        result = self.cli(BRIDGE, "analyze")
        self.assertEqual(1, result.returncode)
        self.assertNotIn("SUPER_SECRET", result.stdout + result.stderr)

    def test_source_directory_symlink_is_reported(self):
        self.external = tempfile.mkdtemp(prefix="bridge-external-")
        Path(self.external, "Dockerfile").write_text("FROM node:24\n", encoding="utf-8")
        (self.root / "linked-source").symlink_to(self.external, target_is_directory=True)
        result = self.cli(BRIDGE, "analyze")
        self.assertEqual(1, result.returncode)
        findings = self.output(result)["findings"]
        self.assertIn("BRIDGE_SYMLINK", {item["rule_id"] for item in findings})
        self.assertTrue(any(item["source"] == "linked-source" for item in findings))

    def test_fifo_is_reported_without_blocking_scan(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO is not supported on this platform")
        os.mkfifo(self.root / "source.pipe")
        command = [sys.executable, "-I", "-S", str(BRIDGE), "--root", str(self.root), "analyze"]
        try:
            result = subprocess.run(command, cwd=self.root, text=True, capture_output=True, timeout=1)
        except subprocess.TimeoutExpired:
            self.fail("bridge analyze blocked while inspecting a FIFO")
        self.assertEqual(1, result.returncode)
        findings = self.output(result)["findings"]
        self.assertTrue(any(item["source"] == "source.pipe" for item in findings))

    def test_duplicate_npm_json_keys_block_plan(self):
        self._write("package.json", '{"name":"bridge-fixture","dependencies":{"a":"1"},"dependencies":{"b":"2"}}\n')
        result = self.cli(BRIDGE, "plan")
        self.assertEqual(1, result.returncode)
        self.assertIn("BRIDGE_JSON", {item["rule_id"] for item in self.output(result)["findings"]})

    def test_artifact_integrity_requires_real_sha512_base64_length(self):
        config_path = self.root / ".agent/bridge.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["mappings"].append({
            "kind": "artifact", "original": "https://artifacts.example.test/a.tgz",
            "resolved": "https://registry.internal.example/artifacts/a.tgz",
            "integrity": "sha512-" + ("A" * 86) + "=", "evidence": "invalid digest encoding",
        })
        config_path.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
        result = self.cli(BRIDGE, "plan")
        self.assertEqual(2, result.returncode)
        self.assertIn("sha512 integrity", json.dumps(self.output(result)).lower())

    def test_mapping_target_shell_metacharacter_is_rejected_without_writes(self):
        config_path = self.root / ".agent/bridge.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["mappings"].append({
            "kind": "git", "original": "https://evil.example.test/source.git",
            "resolved": "https://internal.example/tool;touch", "evidence": "malformed target",
        })
        config_path.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
        before = self.snapshot()
        result = self.cli(BRIDGE, "plan")
        self.assertEqual(2, result.returncode)
        self.assertNotIn("touch", result.stdout + result.stderr)
        self.assertEqual(before, self.snapshot())

    def test_unsupported_ecosystems_and_dynamic_scripts_fail_closed(self):
        self._write("requirements.txt", "demo==1.0\n")
        self._write("build.yaml", "image: node:24\n")
        self._write("go.mod", "module example.test/demo\n")
        self._write("scripts/dynamic.sh", "curl \"https://$HOST/artifact.tgz\"\n")
        result = self.cli(BRIDGE, "analyze")
        self.assertEqual(1, result.returncode)
        rules = {finding["rule_id"] for finding in self.output(result)["findings"]}
        self.assertTrue({"BRIDGE_NATIVE_TRANSPORT", "BRIDGE_STRUCTURED_CONFIG", "BRIDGE_DYNAMIC"}.issubset(rules))

    def test_resource_dispatch_supports_bridge_plan_and_help(self):
        result = self.cli(RESOURCE, "bridge", "plan")
        self.assertEqual(1, result.returncode)
        self.assertEqual("blocked", self.output(result)["status"])
        help_result = self.cli(RESOURCE, "bridge", "--help")
        self.assertEqual(0, help_result.returncode, help_result.stderr)
        self.assertIn("usage:", help_result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
