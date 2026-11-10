"""Immutable storage interface. GitHub is the default; no remote code is loaded."""
from __future__ import annotations

import base64
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Protocol

from .portable import (
    MAX_OBJECT_BYTES,
    MAX_OBJECTS,
    canonical,
    decode,
    fail,
    is_digest,
    publish,
    read,
    strict_files,
)
from .safety import ensure_unlinked_path

PREFIX = "archmarshal-skills/v1/"


class Transport(Protocol):
    def describe(self) -> dict[str, Any]: ...
    def keys(self) -> list[str]: ...
    def get(self, key: str) -> bytes: ...
    def put(self, key: str, data: bytes) -> bool: ...


def check_key(key: str) -> str:
    if not is_digest(key):
        fail("Remote object key must be a SHA-256 digest.")
    return key


class FolderTransport:
    """An explicit existing folder, including a user-mounted cloud drive."""
    def __init__(self, remote: str, **_: Any):
        self.root = ensure_unlinked_path(Path(remote), purpose="Sync folder")
        if not self.root.is_dir():
            fail("Folder remote must be an existing directory.")

    def describe(self) -> dict[str, Any]:
        return {"backend": "folder", "remote": str(self.root), "public": False}

    def keys(self) -> list[str]:
        directory = self.root / PREFIX
        ensure_unlinked_path(directory, purpose="Sync folder objects")
        if not directory.exists():
            return []
        files = strict_files(directory, limit=MAX_OBJECTS)
        result = []
        for path in files:
            if path.parent != directory or path.suffix != ".json":
                fail("Unexpected file in sync object namespace.")
            result.append(check_key(path.stem))
        return sorted(result)

    def get(self, key: str) -> bytes:
        return read(self.root / PREFIX / (check_key(key) + ".json"))

    def put(self, key: str, data: bytes) -> bool:
        if len(data) > MAX_OBJECT_BYTES:
            fail("Sync object exceeds its byte budget.")
        return publish(self.root / PREFIX / (check_key(key) + ".json"), data)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        # Never forward a GitHub credential to another host, even after a repo move.
        return None


def _github_token() -> str:
    value = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if value:
        return value
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}
    try:
        completed = subprocess.run(
            ["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
            text=True, capture_output=True, timeout=30, env=environment, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        fail("Could not read GitHub authentication from Git.", "sync_authentication_failed")
    if completed.returncode == 0:
        for line in completed.stdout.splitlines():
            if line.startswith("password=") and line[9:]:
                return line[9:]
    fail("Authenticate Git for github.com or set GH_TOKEN before syncing.",
         "sync_authentication_failed")
    return ""  # unreachable


class GitHubTransport:
    def __init__(self, remote: str, *, branch: str | None = None, **_: Any):
        if (re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", remote) is None
                or any(part in {".", ".."} for part in remote.split("/"))):
            fail("GitHub remote must be owner/repository, without credentials or a URL.")
        self.remote = remote
        self.branch = branch
        self._token: str | None = None
        self._blobs: dict[str, str] = {}

    def _request(self, path: str, *, method: str = "GET", payload: Any = None,
                 missing_ok: bool = False) -> dict[str, Any] | None:
        if self._token is None:
            self._token = _github_token()
        url = f"https://api.github.com/repos/{self.remote}{path}"
        request = urllib.request.Request(url, method=method,
            data=canonical(payload) if payload is not None else None,
            headers={"Authorization": f"Bearer {self._token}",
                     "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json", "User-Agent": "ArchMarshal",
                     "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=30) as response:
                raw = response.read(MAX_OBJECT_BYTES + 1)
            return decode(raw)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and missing_ok:
                return None
            fail(f"GitHub returned HTTP {exc.code}; no credentials or response body recorded.",
                 f"sync_http_{exc.code}")
        except (urllib.error.URLError, TimeoutError):
            fail("GitHub transport failed; retry from a fresh preview.", "sync_network_error")
        return None

    def describe(self) -> dict[str, Any]:
        # Visibility is an authorization input, so each preview/apply must refresh it.
        info = self._request("") or {}
        if (not isinstance(info.get("private"), bool) or not info.get("default_branch")
                or type(info.get("id")) is not int):
            fail("GitHub repository metadata is incomplete.")
        branch = self.branch or info["default_branch"]
        if not isinstance(branch, str) or not branch or len(branch) > 255:
            fail("Invalid sync branch.")
        self.branch = branch
        return {"backend": "github", "remote": self.remote, "branch": branch,
                "repository_id": info["id"], "public": not info["private"]}

    def keys(self) -> list[str]:
        self.describe()
        ref = urllib.parse.quote(str(self.branch), safe="")
        result = self._request(f"/git/trees/{ref}?recursive=1") or {}
        if result.get("truncated") or not isinstance(result.get("tree"), list):
            fail("GitHub tree is incomplete; use a dedicated sync repository.")
        blobs = {}
        for entry in result["tree"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                fail("Invalid GitHub tree entry.")
            path = entry.get("path", "")
            if not path.startswith(PREFIX) or entry.get("type") == "tree":
                continue
            key = path[len(PREFIX):].removesuffix(".json")
            if (path != PREFIX + key + ".json" or entry.get("mode") != "100644"
                    or entry.get("type") != "blob"):
                fail("Unexpected GitHub sync object type or path.")
            check_key(key)
            if not re.fullmatch(r"[0-9a-f]{40,64}", str(entry.get("sha", ""))):
                fail("Invalid Git blob identity.")
            if (type(entry.get("size")) is not int or entry["size"] < 0
                    or entry["size"] > MAX_OBJECT_BYTES):
                fail("Remote object exceeds its byte budget.")
            blobs[key] = entry["sha"]
        if len(blobs) > MAX_OBJECTS:
            fail("Remote contains too many sync objects.")
        self._blobs = blobs
        return sorted(blobs)

    def get(self, key: str) -> bytes:
        check_key(key)
        if key not in self._blobs:
            self.keys()
        if key not in self._blobs:
            fail("Selected remote object does not exist.")
        result = self._request(f"/git/blobs/{self._blobs[key]}") or {}
        if result.get("encoding") != "base64" or not isinstance(result.get("content"), str):
            fail("Unsupported GitHub blob encoding.")
        try:
            raw = base64.b64decode("".join(result["content"].split()), validate=True)
        except (KeyError, ValueError, TypeError):
            fail("Invalid GitHub blob content.")
        if len(raw) > MAX_OBJECT_BYTES:
            fail("Remote object exceeds its byte budget.")
        return raw

    def put(self, key: str, data: bytes) -> bool:
        check_key(key)
        self.describe()
        if key in self.keys():
            if self.get(key) != data:
                fail("Remote immutable object differs.", "sync_collision")
            return False
        if len(data) > MAX_OBJECT_BYTES // 2:
            fail("GitHub envelope exceeds the 8 MiB upload budget.")
        path = "/contents/" + PREFIX + key + ".json"
        from .errors import ArchMarshalError
        try:
            self._request(path, method="PUT", payload={
                "message": f"ArchMarshal Skill snapshot {key[:12]}", "branch": self.branch,
                "content": base64.b64encode(data).decode("ascii")})
        except ArchMarshalError as exc:
            # A concurrent create is a no-op only after verifying identical bytes.
            if exc.code not in {"sync_http_409", "sync_http_422"}:
                raise
            if key not in self.keys() or self.get(key) != data:
                raise
            return False
        self.keys()
        if self.get(key) != data:
            fail("Uploaded object failed read-back verification.")
        return True


BACKENDS: dict[str, Callable[..., Transport]] = {"github": GitHubTransport, "folder": FolderTransport}


def register_backend(name: str, factory: Callable[..., Transport]) -> None:
    """Host extensions explicitly register a backend; remote data cannot register code."""
    if name in BACKENDS or not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", name):
        fail("Backend name is invalid or already registered.")
    BACKENDS[name] = factory


def transport(remote: str, *, backend: str = "github", branch: str | None = None) -> Transport:
    if backend not in BACKENDS:
        fail("Unknown sync backend.")
    return BACKENDS[backend](remote, branch=branch)
