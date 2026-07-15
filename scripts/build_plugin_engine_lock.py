from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "archmarshal"
LOCK = ROOT / "plugins" / "archmarshal" / "engine.lock.json"
LOCK_FORMAT = "archmarshal-plugin-engine-lock-v1"
MAX_FILES = 10_000
MAX_BYTES = 512 * 1024 * 1024


def _declared_value(path: Path, name: str) -> str:
    pattern = rf'^{re.escape(name)} = "([^"]+)"$'
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise SystemExit(f"missing {name} in {path.relative_to(ROOT)}")
    return match.group(1)


def _portable_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _source_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    total = 0
    for current, directories, filenames in os.walk(SOURCE, topdown=True, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(directories, key=str.casefold):
            candidate = current_path / name
            if name == "__pycache__":
                continue
            if candidate.is_symlink():
                raise SystemExit(f"linked engine directory is unsupported: {candidate}")
            kept.append(name)
        directories[:] = kept
        for name in sorted(filenames, key=str.casefold):
            if name.endswith((".pyc", ".pyo")):
                continue
            path = current_path / name
            if path.is_symlink():
                raise SystemExit(f"linked engine file is unsupported: {path}")
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode):
                raise SystemExit(f"special engine path is unsupported: {path}")
            content = path.read_bytes()
            after = path.lstat()
            if (
                (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or len(content) != after.st_size
            ):
                raise SystemExit(f"engine source changed while hashing: {path}")
            relative = path.relative_to(SOURCE).as_posix()
            key = _portable_key(relative)
            if key in seen and seen[key] != relative:
                raise SystemExit(
                    f"portable engine path collision: {seen[key]!r} and {relative!r}"
                )
            seen[key] = relative
            total += len(content)
            if len(records) >= MAX_FILES or total > MAX_BYTES:
                raise SystemExit("engine source exceeds lock generation limits")
            records.append(
                {
                    "path": relative,
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
    return sorted(records, key=lambda item: (_portable_key(str(item["path"])), item["path"]))


def build_lock() -> dict[str, Any]:
    records = _source_records()
    canonical = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    init = SOURCE / "__init__.py"
    return {
        "format": LOCK_FORMAT,
        "engine_version": _declared_value(init, "__version__"),
        "engine_api": _declared_value(init, "ENGINE_API_VERSION"),
        "source_root": "src/archmarshal",
        "file_count": len(records),
        "content_bytes": sum(int(record["bytes"]) for record in records),
        "source_tree_sha256": hashlib.sha256(canonical).hexdigest(),
        "files": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or check the Codex plugin engine lock.")
    parser.add_argument("--check", action="store_true", help="Fail if the tracked lock is stale.")
    parser.add_argument("--stdout", action="store_true", help="Print the generated lock.")
    args = parser.parse_args(argv)
    rendered = json.dumps(build_lock(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.stdout:
        print(rendered, end="")
    if args.check:
        if not LOCK.is_file() or LOCK.read_text(encoding="utf-8") != rendered:
            raise SystemExit("plugin engine lock is stale; run scripts/build_plugin_engine_lock.py")
    elif not args.stdout:
        LOCK.write_text(rendered, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
