import json
import os
import subprocess
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERIFY = os.path.join(ROOT, "assets", "verify.py")


class VerifyRunnerTests(unittest.TestCase):
    def run_verify(self, config, *, files=None, extra_args=(), raw_config=None):
        with tempfile.TemporaryDirectory() as directory:
            if files:
                for name, content in files.items():
                    path = os.path.join(directory, name)
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.write(content)
            config_path = os.path.join(directory, "harness.json")
            with open(config_path, "w", encoding="utf-8") as handle:
                if raw_config is None:
                    json.dump(config, handle)
                else:
                    handle.write(raw_config)
            command = [sys.executable, "-I", "-S", VERIFY, "--config", config_path, "--format", "json"]
            command.extend(extra_args)
            return subprocess.run(command, cwd=directory, capture_output=True, text=True)

    def base(self, argv):
        return {"schema_version": 1, "owner": "tests", "checks": [{
            "id": "check", "argv": argv, "source": "test", "expected": "success", "suggested_fix": "fix it"
        }]}

    def report(self, completed):
        return json.loads(completed.stdout)

    def test_pass(self):
        result = self.run_verify(self.base([sys.executable, "-c", "print('ok')"]))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.report(result)["status"], "pass")
        self.assertEqual(self.report(result)["checks"][0]["status"], "pass")

    def test_passing_check_keeps_audit_output_in_text(self):
        config = self.base([sys.executable, "-c", "print('waived: ARCH001 legacy.py owner=team')"])
        result = self.run_verify(config, extra_args=("--format", "text"))
        self.assertEqual(result.returncode, 0)
        self.assertIn("waived: ARCH001 legacy.py owner=team", result.stdout)

    def test_invalid_config_runs_no_commands(self):
        config = self.base([sys.executable, "-c", "print('must not execute')"])
        config["checks"].append(dict(config["checks"][0], id="invalid", cwd="bad\x00path"))
        result = self.run_verify(config)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.report(result)["checks"], [])
        self.assertNotIn("must not execute", result.stdout)

    def test_failure_has_structured_feedback(self):
        result = self.run_verify(self.base([sys.executable, "-c", "print('bad'); raise SystemExit(3)"]))
        report = self.report(result)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["checks"][0]["exit_code"], 3)
        self.assertEqual(report["findings"][0]["rule_id"], "check")

    def test_text_report_is_actionable(self):
        config = self.base([sys.executable, "-c", "print('command output'); raise SystemExit(4)"])
        result = self.run_verify(config, extra_args=("--format", "text"))
        self.assertEqual(result.returncode, 1)
        for value in ("rule_id: check", "source: test", "violation:", "expected: success",
                      "suggested_fix: fix it", "output: command output"):
            self.assertIn(value, result.stdout)

    def test_empty_checks_and_malformed_json(self):
        result = self.run_verify({"schema_version": 1, "owner": "x", "checks": []})
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.report(result)["status"], "error")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "harness.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{")
            result = subprocess.run([sys.executable, "-I", "-S", VERIFY, "--config", path, "--format", "json"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["status"], "error")

    def test_duplicate_and_unknown_fields(self):
        config = self.base([sys.executable, "-c", "pass"])
        config["checks"].append(dict(config["checks"][0]))
        result = self.run_verify(config)
        self.assertEqual(result.returncode, 2)
        config = self.base([sys.executable, "-c", "pass"])
        config["extra"] = True
        self.assertEqual(self.run_verify(config).returncode, 2)
        duplicate = '{"schema_version":1,"owner":"x","owner":"y","checks":[]}'
        result = self.run_verify({}, raw_config=duplicate)
        self.assertEqual(result.returncode, 2)

    def test_missing_executable_and_timeout(self):
        result = self.run_verify(self.base(["definitely-not-an-executable"]))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.report(result)["checks"][0]["status"], "error")
        config = self.base([sys.executable, "-c", "import time; time.sleep(10)"])
        config["checks"][0]["timeout_seconds"] = 0.05
        result = self.run_verify(config)
        self.assertEqual(result.returncode, 2)
        self.assertIn("timed out", self.report(result)["findings"][0]["violation"])

    def test_missing_cwd_and_invalid_timeout_values(self):
        config = self.base([sys.executable, "-c", "pass"])
        config["checks"][0]["cwd"] = "does-not-exist"
        result = self.run_verify(config)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid working directory", self.report(result)["findings"][0]["violation"])
        for value in (float("nan"), float("inf"), True, 10 ** 10000):
            config = self.base([sys.executable, "-c", "pass"])
            config["checks"][0]["timeout_seconds"] = value
            if isinstance(value, float):
                raw = json.dumps(config, allow_nan=True)
                result = self.run_verify({}, raw_config=raw)
            elif isinstance(value, int) and value > sys.maxsize:
                prefix = '{"schema_version":1,"owner":"tests","checks":[{"id":"check","argv":["pass"],"source":"test","expected":"success","suggested_fix":"fix it","timeout_seconds":'
                raw = prefix + ("9" * 5000) + "}]}"
                result = self.run_verify({}, raw_config=raw)
            else:
                result = self.run_verify(config)
            self.assertEqual(result.returncode, 2)

    def test_all_checks_run_in_order_and_mixed_error_is_exit_two(self):
        config = self.base([sys.executable, "-c", "print('first'); raise SystemExit(1)"])
        config["checks"].append({
            "id": "missing", "argv": ["not-a-real-command"], "source": "second",
            "expected": "success", "suggested_fix": "fix it"
        })
        result = self.run_verify(config)
        report = self.report(result)
        self.assertEqual(result.returncode, 2)
        self.assertEqual([check["id"] for check in report["checks"]], ["check", "missing"])
        self.assertEqual([check["status"] for check in report["checks"]], ["fail", "error"])

    def test_non_utf8_output_is_safe(self):
        config = self.base([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff'); raise SystemExit(1)"])
        result = self.run_verify(config)
        self.assertEqual(result.returncode, 1)
        self.assertIn("�", self.report(result)["checks"][0]["output"])

    def test_relative_cwd_and_bounded_output(self):
        config = self.base([sys.executable, "-c", "print(open('marker').read())"])
        config["checks"][0]["cwd"] = "nested"
        result = self.run_verify(config, files={"nested/marker": "relative"})
        self.assertEqual(result.returncode, 0)
        self.assertIn("relative", self.report(result)["checks"][0]["output"])
        config = self.base([sys.executable, "-c", "print('x' * 100000)"])
        output = self.report(self.run_verify(config))["checks"][0]["output"]
        self.assertLessEqual(len(output.encode("utf-8")), 65536 + len("\n[output truncated]"))
        self.assertIn("output truncated", output)


if __name__ == "__main__":
    unittest.main()
