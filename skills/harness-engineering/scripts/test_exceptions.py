"""Behavior tests; run with python3 -I -S scripts/test_exceptions.py."""

import datetime
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "assets" / "exceptions.py"
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("policy_exceptions", SCRIPT)
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)
TODAY = datetime.date(2026, 9, 15)


class ExceptionTests(unittest.TestCase):
    def policy(self, **updates):
        item = {"rule": "ARCH001", "scope": ["src/domain/legacy.py"],
                "reason": "Migration tracked in issue 42", "owner": "platform",
                "expires": "2026-09-15"}
        item.update(updates)
        return {"version": 1, "exceptions": [item]}

    def load(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "exceptions.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return POLICY.load_exceptions(path, {"ARCH001", "ARCH002"}, today=TODAY)

    def test_exact_rule_and_path_only(self):
        entries = self.load(self.policy())
        findings = [
            {"rule_id": "ARCH001", "source": "src/domain/legacy.py", "violation": "import"},
            {"rule_id": "ARCH002", "source": "src/domain/legacy.py", "violation": "cycle"},
            {"rule_id": "ARCH001", "source": "src/domain/new.py", "violation": "import"},
        ]
        active, waived = POLICY.partition_findings(findings, entries)
        self.assertEqual(active, findings[1:])
        self.assertEqual(waived[0]["finding"], findings[0])
        self.assertEqual(waived[0]["exception"]["owner"], "platform")

    def test_expiry_date_inclusive_and_expired_policy_fails(self):
        self.assertEqual(len(self.load(self.policy())), 1)
        with self.assertRaisesRegex(POLICY.PolicyError, "expired"):
            self.load(self.policy(expires="2026-09-14"))

    def test_invalid_metadata_and_dates(self):
        for update in ({"owner": " "}, {"reason": ""}, {"rule": "TYPO"},
                       {"expires": "2026-02-30"}, {"expires": "20260916"},
                       {"expires": "tomorrow"}, {"scope": []}, {"scope": "src/a.py"}):
            with self.subTest(update=update), self.assertRaises(POLICY.PolicyError):
                self.load(self.policy(**update))

    def test_broad_and_ambiguous_scope_rejected(self):
        for path in ("/src/a.py", "../a.py", "src/*", "src/**", "src/a?.py", "src/[a].py",
                     "src//a.py", "src/./a.py", "src/a.py:1", "src\\a.py", "src/", ""):
            with self.subTest(path=path), self.assertRaises(POLICY.PolicyError):
                self.load(self.policy(scope=[path]))

    def test_duplicates_unknown_fields_and_empty_policy(self):
        duplicate = self.policy()
        duplicate["exceptions"].append(duplicate["exceptions"][0])
        unknown = self.policy()
        unknown["exceptions"][0]["approved"] = True
        for data in (duplicate, unknown, {"version": True, "exceptions": []}):
            with self.subTest(data=data), self.assertRaises(POLICY.PolicyError):
                self.load(data)
        self.assertEqual(self.load({"version": 1, "exceptions": []}), [])

    def test_cli_corrupt_missing_duplicate_and_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            command = [sys.executable, "-I", "-S", str(SCRIPT), "--file", str(path), "--rules", "ARCH001"]
            missing = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2)
            for content in ("{", '{"version":1,"version":1,"exceptions":[]}'):
                path.write_text(content, encoding="utf-8")
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["findings"][0]["rule_id"], "EXCEPTION_CONFIG")
            path.write_text('{"version":1,"exceptions":[]}', encoding="utf-8")
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["status"], "pass")


if __name__ == "__main__":
    unittest.main()
