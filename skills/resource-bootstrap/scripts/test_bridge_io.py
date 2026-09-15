import hashlib
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
import bridge_io  # noqa: E402


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class BridgeIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def change(self, path, before, after):
        return {"path": path, "before_sha256": digest(before) if before is not None else None, "after": after}

    def test_apply_edit_create_preserves_mode_and_restore_removes_new_file(self):
        old = self.root / "old.txt"
        old.write_text("old", encoding="utf-8")
        os.chmod(old, 0o751)
        result = bridge_io.apply_changes(self.root, [self.change("old.txt", "old", "changed"),
                                                     self.change("new/added.txt", None, "new")], "t-1")
        self.assertEqual("applied", result["status"])
        self.assertEqual("changed", old.read_text(encoding="utf-8"))
        self.assertEqual(0o751, stat.S_IMODE(old.stat().st_mode))
        self.assertEqual("new", (self.root / "new/added.txt").read_text(encoding="utf-8"))
        restored = bridge_io.restore_changes(self.root, "t-1")
        self.assertEqual("restored", restored["status"])
        self.assertEqual("old", old.read_text(encoding="utf-8"))
        self.assertFalse((self.root / "new/added.txt").exists())
        self.assertTrue((self.root / ".agent/bridge-backups/t-1/manifest.json").exists())
        with self.assertRaisesRegex(bridge_io.BridgeIOError, "already restored"):
            bridge_io.restore_changes(self.root, "t-1")

    def test_empty_batch_is_a_no_write_noop(self):
        result = bridge_io.apply_changes(self.root, [], "t-empty")
        self.assertEqual("applied", result["status"])
        self.assertEqual([], result["files"])
        self.assertFalse((self.root / ".agent").exists())

    def test_private_backup_ignore_survives_root_ignore_restore(self):
        root_ignore = self.root / ".gitignore"
        root_ignore.write_text("*.tmp\n", encoding="utf-8")
        bridge_io.apply_changes(self.root, [self.change(".gitignore", "*.tmp\n", "*.log\n")], "t-ignore")
        backup_ignore = self.root / ".agent/bridge-backups/.gitignore"
        self.assertEqual("*\n", backup_ignore.read_text(encoding="utf-8"))
        self.assertEqual(0o600, stat.S_IMODE(backup_ignore.stat().st_mode))
        bridge_io.restore_changes(self.root, "t-ignore")
        self.assertEqual("*.tmp\n", root_ignore.read_text(encoding="utf-8"))
        self.assertEqual("*\n", backup_ignore.read_text(encoding="utf-8"))

    def test_conflicting_or_symlinked_backup_ignore_is_rejected(self):
        backups = self.root / ".agent/bridge-backups"
        backups.mkdir(parents=True)
        (backups / ".gitignore").write_text("not-owned\n", encoding="utf-8")
        with self.assertRaisesRegex(bridge_io.BridgeIOError, "conflicting backup ignore"):
            bridge_io.apply_changes(self.root, [self.change("a.txt", None, "x")], "t-ignore-conflict")
        (backups / ".gitignore").unlink()
        target = self.root / "ignore-target"
        target.write_text("*\n", encoding="utf-8")
        (backups / ".gitignore").symlink_to(target)
        with self.assertRaisesRegex(bridge_io.BridgeIOError, "unsafe backup ignore"):
            bridge_io.apply_changes(self.root, [self.change("a.txt", None, "x")], "t-ignore-link")

    def test_restore_drift_is_all_or_nothing(self):
        (self.root / "a.txt").write_text("a", encoding="utf-8")
        (self.root / "b.txt").write_text("b", encoding="utf-8")
        bridge_io.apply_changes(self.root, [self.change("a.txt", "a", "A"), self.change("b.txt", "b", "B")], "t-2")
        (self.root / "b.txt").write_text("user", encoding="utf-8")
        with self.assertRaisesRegex(bridge_io.BridgeIOError, "target has changed"):
            bridge_io.restore_changes(self.root, "t-2")
        self.assertEqual("A", (self.root / "a.txt").read_text(encoding="utf-8"))
        self.assertEqual("user", (self.root / "b.txt").read_text(encoding="utf-8"))

    def test_rejects_unsafe_and_duplicate_paths(self):
        for path in ("/absolute", "a/../b", "a//b", "a\\b", ".git/config", "docs/AGENTS.md"):
            with self.assertRaises(bridge_io.BridgeIOError):
                bridge_io.apply_changes(self.root, [self.change(path, None, "x")], "t-3")
        change = self.change("same.txt", None, "x")
        with self.assertRaisesRegex(bridge_io.BridgeIOError, "duplicate"):
            bridge_io.apply_changes(self.root, [change, change], "t-4")
        real = self.root / "real.txt"
        real.write_text("x", encoding="utf-8")
        (self.root / "link.txt").symlink_to(real)
        with self.assertRaises(bridge_io.BridgeIOError):
            bridge_io.apply_changes(self.root, [self.change("link.txt", digest("x"), "y")], "t-5")
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "linked-dir").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(bridge_io.BridgeIOError):
            bridge_io.apply_changes(self.root, [self.change("linked-dir/file.txt", None, "y")], "t-5b")

    def test_backup_tampering_and_current_drift_refuse_restore(self):
        file = self.root / "a.txt"
        file.write_text("before", encoding="utf-8")
        bridge_io.apply_changes(self.root, [self.change("a.txt", "before", "after")], "t-6")
        backup = self.root / ".agent/bridge-backups/t-6/files/0000.orig"
        backup.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(bridge_io.BridgeIOError, "backup checksum"):
            bridge_io.restore_changes(self.root, "t-6")
        self.assertEqual("after", file.read_text(encoding="utf-8"))
        # A separate transaction demonstrates target drift independently.
        bridge_io.apply_changes(self.root, [self.change("a.txt", "after", "new-after")], "t-7")
        file.write_text("concurrent", encoding="utf-8")
        with self.assertRaisesRegex(bridge_io.BridgeIOError, "target has changed"):
            bridge_io.restore_changes(self.root, "t-7")

    def test_manifest_tampering_is_rejected(self):
        file = self.root / "a.txt"
        file.write_text("before", encoding="utf-8")
        bridge_io.apply_changes(self.root, [self.change("a.txt", "before", "after")], "t-7b")
        manifest = self.root / ".agent/bridge-backups/t-7b/manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(bridge_io.BridgeIOError, "manifest checksum"):
            bridge_io.restore_changes(self.root, "t-7b")
        self.assertEqual("after", file.read_text(encoding="utf-8"))

    def test_mid_apply_failure_rolls_back_and_keeps_metadata(self):
        (self.root / "a.txt").write_text("a", encoding="utf-8")
        (self.root / "b.txt").write_text("b", encoding="utf-8")
        original = bridge_io._atomic_write

        def fail_second_target(path, data, mode=None):
            if Path(path).name == "b.txt":
                raise bridge_io.BridgeIOError("injected")
            return original(path, data, mode)

        with patch.object(bridge_io, "_atomic_write", side_effect=fail_second_target):
            with self.assertRaisesRegex(bridge_io.BridgeIOError, "rolled back"):
                bridge_io.apply_changes(self.root, [self.change("a.txt", "a", "A"), self.change("b.txt", "b", "B")], "t-8")
        self.assertEqual("a", (self.root / "a.txt").read_text(encoding="utf-8"))
        self.assertEqual("b", (self.root / "b.txt").read_text(encoding="utf-8"))
        self.assertTrue((self.root / ".agent/bridge-backups/t-8/manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
