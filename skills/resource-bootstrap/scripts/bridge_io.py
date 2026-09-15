"""Safe, small transactions for controller-owned repository rewrites.

This module deliberately has no command-line interface.  Its callers supply a
fresh transaction id and complete desired file contents; the module validates
the complete batch before creating its backup directory or changing a file.
"""

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple


MAX_CONTENT_BYTES = 4 * 1024 * 1024
_ID_RE = re.compile(r"[a-z0-9-]{1,64}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_PROTECTED_PARTS = {".git", ".agents", ".codex", ".opencode", ".ssh", ".aws", ".kube", ".agent"}
_PROTECTED_NAMES = {"AGENTS.md", "CLAUDE.md"}
_BACKUP_IGNORE = b"*\n"


class BridgeIOError(Exception):
    """A deliberately non-sensitive error from a bridge file transaction."""


def _fail(message: str) -> None:
    raise BridgeIOError(message)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lstat(path: Path) -> Optional[os.stat_result]:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        _fail("cannot inspect filesystem")


def _root(root: Path) -> Path:
    root = Path(root)
    info = _lstat(root)
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        _fail("invalid repository root")
    try:
        return root.resolve()
    except OSError:
        _fail("invalid repository root")


def _relative_path(value: Any) -> Tuple[str, Tuple[str, ...]]:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        _fail("invalid change path")
    path = PurePosixPath(value)
    parts = tuple(value.split("/"))
    if path.is_absolute() or not parts or any(part in ("", ".", "..") for part in parts):
        _fail("invalid change path")
    if path.as_posix() != value or any(part in _PROTECTED_PARTS for part in parts):
        _fail("protected or non-normalized path")
    if any(part in _PROTECTED_NAMES for part in parts):
        _fail("protected path")
    return value, parts


def _check_ancestors(root: Path, parts: Tuple[str, ...]) -> Path:
    """Return the target after rejecting every existing unsafe ancestor."""
    current = root
    for part in parts[:-1]:
        current = current / part
        info = _lstat(current)
        if info is None:
            break
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            _fail("unsafe parent path")
    return root.joinpath(*parts)


def _read_regular(path: Path, maximum: Optional[int] = None) -> Tuple[bytes, int]:
    info = _lstat(path)
    if info is None:
        _fail("file changed during validation")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        _fail("unsafe file target")
    try:
        with path.open("rb") as handle:
            data = handle.read(maximum + 1) if maximum is not None else handle.read()
            if maximum is not None and len(data) > maximum:
                _fail("file is too large")
            return data, stat.S_IMODE(info.st_mode)
    except OSError:
        _fail("cannot read file")


def _validate_changes(root: Path, changes: Any) -> List[Dict[str, Any]]:
    if not isinstance(changes, list):
        _fail("changes must be a list")
    seen = set()
    plans: List[Dict[str, Any]] = []
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"path", "before_sha256", "after"}:
            _fail("invalid change")
        relative, parts = _relative_path(change["path"])
        if relative in seen:
            _fail("duplicate change path")
        seen.add(relative)
        expected = change["before_sha256"]
        if expected is not None and (not isinstance(expected, str) or not _HASH_RE.fullmatch(expected)):
            _fail("invalid before checksum")
        if not isinstance(change["after"], str):
            _fail("after content must be text")
        try:
            after = change["after"].encode("utf-8")
        except UnicodeError:
            _fail("after content is not utf-8")
        if len(after) > MAX_CONTENT_BYTES:
            _fail("after content is too large")
        target = _check_ancestors(root, parts)
        info = _lstat(target)
        if info is None:
            if expected is not None:
                _fail("stale file checksum")
            before, mode = None, None
        else:
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                _fail("unsafe file target")
            if expected is None:
                _fail("file unexpectedly exists")
            before, mode = _read_regular(target, MAX_CONTENT_BYTES)
            if _sha256(before) != expected:
                _fail("stale file checksum")
        plans.append({
            "path": relative, "parts": parts, "target": target, "before": before,
            "mode": mode, "after": after, "before_sha256": expected,
            "after_sha256": _sha256(after),
        })
    return plans


def _ensure_directory(path: Path, create: bool) -> None:
    info = _lstat(path)
    if info is None:
        if not create:
            _fail("missing transaction backup")
        try:
            path.mkdir()
        except OSError:
            _fail("cannot create transaction backup")
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        _fail("unsafe backup path")


def _backup_dir(root: Path, transaction_id: str, create: bool) -> Path:
    if not isinstance(transaction_id, str) or not _ID_RE.fullmatch(transaction_id):
        _fail("invalid transaction id")
    agent = root / ".agent"
    backups = agent / "bridge-backups"
    _ensure_directory(agent, create)
    _ensure_directory(backups, create)
    _ensure_backup_ignore(backups, create)
    destination = backups / transaction_id
    info = _lstat(destination)
    if create:
        if info is not None:
            _fail("transaction already exists")
        try:
            destination.mkdir()
        except OSError:
            _fail("cannot create transaction backup")
    elif info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        _fail("missing transaction backup")
    return destination


def _atomic_write(path: Path, data: bytes, mode: Optional[int] = None) -> None:
    """Write a complete replacement in the destination directory."""
    try:
        with tempfile.NamedTemporaryFile(dir=str(path.parent), prefix=".bridge-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(str(temporary), str(path))
    except OSError:
        try:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)
        except OSError:
            pass
        _fail("atomic file write failed")


def _ensure_backup_ignore(backups: Path, create: bool) -> None:
    """Keep retained controller backups private even if root .gitignore changes."""
    ignore = backups / ".gitignore"
    info = _lstat(ignore)
    if info is None:
        if not create:
            _fail("missing backup ignore file")
        _atomic_write(ignore, _BACKUP_IGNORE, 0o600)
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        _fail("unsafe backup ignore file")
    data, mode = _read_regular(ignore, 4096)
    if data != _BACKUP_IGNORE or mode != 0o600:
        _fail("conflicting backup ignore file")


def _manifest_bytes(manifest: Dict[str, Any]) -> bytes:
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _no_duplicate_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _write_manifest(backup: Path, manifest: Dict[str, Any]) -> None:
    data = _manifest_bytes(manifest)
    _atomic_write(backup / "manifest.json", data, 0o600)
    _atomic_write(backup / "manifest.sha256", (_sha256(data) + "\n").encode("ascii"), 0o600)


def _backup_name(index: int) -> str:
    return "files/{:04d}.orig".format(index)


def _rollback(plans: List[Dict[str, Any]]) -> bool:
    complete = True
    for plan in reversed(plans):
        target = plan["target"]
        info = _lstat(target)
        if info is None:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            complete = False
            continue
        try:
            current, _ = _read_regular(target, MAX_CONTENT_BYTES)
        except BridgeIOError:
            complete = False
            continue
        if _sha256(current) != plan["after_sha256"]:
            complete = False
            continue
        try:
            if plan["before"] is None:
                target.unlink()
            else:
                _atomic_write(target, plan["before"], plan["mode"])
        except (BridgeIOError, OSError):
            complete = False
    return complete


def _verify_unchanged_before(root: Path, plan: Dict[str, Any]) -> None:
    """Avoid overwriting a file created or changed after batch validation."""
    target = _check_ancestors(root, plan["parts"])
    info = _lstat(target)
    if plan["before"] is None:
        if info is not None:
            _fail("file changed during apply")
        return
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        _fail("file changed during apply")
    data, mode = _read_regular(target, MAX_CONTENT_BYTES)
    if _sha256(data) != plan["before_sha256"] or mode != plan["mode"]:
        _fail("file changed during apply")


def apply_changes(root: Path, changes: List[Dict[str, Any]], transaction_id: str) -> Dict[str, Any]:
    """Atomically replace individual files and retain data needed to restore them."""
    root = _root(root)
    if not isinstance(transaction_id, str) or not _ID_RE.fullmatch(transaction_id):
        _fail("invalid transaction id")
    if not isinstance(changes, list):
        _fail("changes must be a list")
    if not changes:
        return {"transaction_id": transaction_id, "backup_dir": ".agent/bridge-backups/" + transaction_id,
                "files": [], "status": "applied"}
    plans = _validate_changes(root, changes)
    backup = _backup_dir(root, transaction_id, create=True)
    files_dir = backup / "files"
    _ensure_directory(files_dir, create=True)
    entries = []
    try:
        for index, plan in enumerate(plans):
            backup_file = None
            backup_sha = None
            if plan["before"] is not None:
                backup_file = _backup_name(index)
                backup_sha = _sha256(plan["before"])
                _atomic_write(backup / backup_file, plan["before"], 0o600)
            entries.append({
                "path": plan["path"], "before_sha256": plan["before_sha256"],
                "after_sha256": plan["after_sha256"], "mode": plan["mode"],
                "backup_file": backup_file, "backup_sha256": backup_sha,
            })
        manifest = {"version": 1, "transaction_id": transaction_id, "state": "prepared", "files": entries}
        _write_manifest(backup, manifest)
    except BridgeIOError:
        _fail("cannot prepare transaction backup")
    written: List[Dict[str, Any]] = []
    try:
        for plan in plans:
            target = plan["target"]
            _verify_unchanged_before(root, plan)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                _fail("cannot create parent directory")
            _check_ancestors(root, plan["parts"])
            _verify_unchanged_before(root, plan)
            _atomic_write(target, plan["after"], plan["mode"])
            written.append(plan)
        manifest["state"] = "applied"
        _write_manifest(backup, manifest)
    except BridgeIOError:
        manifest["state"] = "rolled_back" if _rollback(written) else "rollback_incomplete"
        try:
            _write_manifest(backup, manifest)
        except BridgeIOError:
            pass
        _fail("apply failed; changes rolled back" if manifest["state"] == "rolled_back" else "apply failed; rollback incomplete")
    return {"transaction_id": transaction_id, "backup_dir": str(backup.relative_to(root)),
            "files": [plan["path"] for plan in plans], "status": "applied"}


def _load_manifest(backup: Path, transaction_id: str) -> Dict[str, Any]:
    raw, _ = _read_regular(backup / "manifest.json", 2 * 1024 * 1024)
    checksum, _ = _read_regular(backup / "manifest.sha256", 65)
    if checksum != (_sha256(raw) + "\n").encode("ascii"):
        _fail("transaction manifest checksum mismatch")
    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeError, ValueError):
        _fail("invalid transaction manifest")
    required = {"version", "transaction_id", "state", "files"}
    if (not isinstance(manifest, dict) or set(manifest) != required
            or type(manifest.get("version")) is not int or manifest["version"] != 1):
        _fail("invalid transaction manifest")
    if manifest["transaction_id"] != transaction_id or manifest["state"] != "applied" or not isinstance(manifest["files"], list):
        _fail("invalid transaction manifest")
    return manifest


def _restore_plans(root: Path, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    seen = set()
    plans = []
    for index, entry in enumerate(manifest["files"]):
        required = {"path", "before_sha256", "after_sha256", "mode", "backup_file", "backup_sha256"}
        if not isinstance(entry, dict) or set(entry) != required:
            _fail("invalid transaction manifest")
        relative, parts = _relative_path(entry["path"])
        if relative in seen or not isinstance(entry["after_sha256"], str) or not _HASH_RE.fullmatch(entry["after_sha256"]):
            _fail("invalid transaction manifest")
        seen.add(relative)
        before = entry["before_sha256"]
        if before is not None and (not isinstance(before, str) or not _HASH_RE.fullmatch(before)):
            _fail("invalid transaction manifest")
        if before is None:
            if entry["backup_file"] is not None or entry["backup_sha256"] is not None or entry["mode"] is not None:
                _fail("invalid transaction manifest")
        elif (entry["backup_file"] != _backup_name(index) or not isinstance(entry["backup_sha256"], str)
              or not _HASH_RE.fullmatch(entry["backup_sha256"]) or type(entry["mode"]) is not int
              or entry["mode"] < 0 or entry["mode"] > 0o7777):
            _fail("invalid transaction manifest")
        plans.append({"path": relative, "parts": parts, "entry": entry})
    return plans


def restore_changes(root: Path, transaction_id: str) -> Dict[str, Any]:
    """Restore a completed transaction only when every target remains unchanged."""
    root = _root(root)
    backup = _backup_dir(root, transaction_id, create=False)
    marker = _lstat(backup / "restored.json")
    if marker is not None and (stat.S_ISLNK(marker.st_mode) or not stat.S_ISREG(marker.st_mode)):
        _fail("unsafe transaction marker")
    if marker is not None:
        _fail("transaction already restored")
    manifest = _load_manifest(backup, transaction_id)
    _ensure_directory(backup / "files", create=False)
    plans = _restore_plans(root, manifest)
    originals: List[Optional[bytes]] = []
    # Complete preflight: backup integrity and every current target are checked first.
    for plan in plans:
        entry = plan["entry"]
        target = _check_ancestors(root, plan["parts"])
        plan["target"] = target
        if entry["before_sha256"] is None:
            originals.append(None)
        else:
            backup_file = backup / entry["backup_file"]
            data, _ = _read_regular(backup_file, MAX_CONTENT_BYTES)
            if _sha256(data) != entry["backup_sha256"] or _sha256(data) != entry["before_sha256"]:
                _fail("transaction backup checksum mismatch")
            originals.append(data)
        current, _ = _read_regular(target, MAX_CONTENT_BYTES)
        if _sha256(current) != entry["after_sha256"]:
            _fail("transaction target has changed")
    try:
        for plan, original in zip(plans, originals):
            target = plan["target"]
            entry = plan["entry"]
            current, _ = _read_regular(target, MAX_CONTENT_BYTES)
            if _sha256(current) != entry["after_sha256"]:
                _fail("transaction target has changed")
            if original is None:
                try:
                    target.unlink()
                except OSError:
                    _fail("cannot remove restored new file")
            else:
                _atomic_write(target, original, entry["mode"])
        _atomic_write(backup / "restored.json", b'{"state":"restored"}\n', 0o600)
    except BridgeIOError:
        _fail("restore failed; partial restore possible")
    return {"transaction_id": transaction_id, "backup_dir": str(backup.relative_to(root)),
            "files": [plan["path"] for plan in plans], "status": "restored"}
