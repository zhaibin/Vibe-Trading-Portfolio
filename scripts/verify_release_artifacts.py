#!/usr/bin/env python3
"""Fail closed when release archives contain local artifacts or omit the SPA."""

from __future__ import annotations

import argparse
import re
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SPA_INDEX = "vibe_portfolio/web/dist/index.html"
SPA_CSS = re.compile(r"^vibe_portfolio/web/dist/assets/[^/]+-[0-9a-f]{8,}\.css$")
SPA_JAVASCRIPT = re.compile(r"^vibe_portfolio/web/dist/assets/[^/]+-[0-9a-f]{8,}\.js$")

_FORBIDDEN_ANYWHERE = {
    ".DS_Store",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
}
_FORBIDDEN_ROOTS = {".agents", ".codegraph", ".codex", ".coverage", ".superpowers", "dist", "var"}
_FORBIDDEN_FRONTEND = {"coverage", "node_modules", "playwright-report", "test-results"}
_REQUIRED_SDIST_FILES = {
    "LICENSE",
    "README.md",
    "alembic.ini",
    "frontend/index.html",
    "frontend/package-lock.json",
    "frontend/package.json",
    "frontend/playwright.config.ts",
    "frontend/tooling/eslint.config.js",
    "frontend/tooling/package.json",
    "frontend/tsconfig.json",
    "frontend/vite.config.ts",
    "pyproject.toml",
    "uv.lock",
}
_REQUIRED_SDIST_PREFIXES = (
    "docs/runbooks/",
    "frontend/e2e/",
    "frontend/src/",
    "scripts/",
    "src/vibe_portfolio/",
    "src/vibe_portfolio/portfolio/migrations/",
    "tests/",
)
_SENSITIVE_PAYLOADS = (
    ("personal home path", re.compile(rb"(?:/" + b"Users" + rb"/|/" + b"home" + rb"/)[^/\s]+")),
    (
        "Windows personal home path",
        re.compile(rb"[A-Za-z]:\\" + b"Users" + rb"\\[^\\\s]+", re.IGNORECASE),
    ),
    ("private key", re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("OpenAI-style API key", re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("GitHub token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("Google API key", re.compile(rb"\bAIza[A-Za-z0-9_-]{30,}\b")),
    ("Slack token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("bearer token", re.compile(rb"\bBearer\s+[A-Za-z0-9._~+/=-]{24,}", re.IGNORECASE)),
)


@dataclass(frozen=True)
class ArchiveReport:
    member_count: int
    payload_bytes: int


def _validated_parts(name: str, *, root: str | None = None) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"unsafe archive member: {name}")
    parts = path.parts
    if root is not None:
        if not parts or parts[0] != root:
            raise ValueError(f"sdist member is outside its single root directory: {name}")
        parts = parts[1:]
    if not parts:
        return ()
    if parts[0] in _FORBIDDEN_ROOTS or any(part in _FORBIDDEN_ANYWHERE for part in parts):
        raise ValueError(f"forbidden archive member: {name}")
    if len(parts) >= 2 and parts[0] == "frontend" and parts[1] in _FORBIDDEN_FRONTEND:
        raise ValueError(f"forbidden archive member: {name}")
    if parts[-1].endswith((".log", ".pyc", ".pyo")):
        raise ValueError(f"forbidden archive member: {name}")
    return parts


def _scan_payload(name: str, payload: bytes) -> None:
    for description, pattern in _SENSITIVE_PAYLOADS:
        if pattern.search(payload):
            raise ValueError(f"sensitive payload ({description}) in archive member: {name}")


def verify_sdist(path: Path) -> ArchiveReport:
    member_count = 0
    payload_bytes = 0
    relative_files: set[str] = set()
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        roots = {PurePosixPath(member.name).parts[0] for member in members if PurePosixPath(member.name).parts}
        if len(roots) != 1:
            raise ValueError("sdist must contain exactly one root directory")
        root = next(iter(roots))
        for member in members:
            member_count += 1
            parts = _validated_parts(member.name, root=root)
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError(f"unsupported archive member type: {member.name}")
            if not member.isfile():
                continue
            relative_files.add("/".join(parts))
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(f"could not read archive member: {member.name}")
            payload = extracted.read()
            payload_bytes += len(payload)
            _scan_payload(member.name, payload)
    for required in sorted(_REQUIRED_SDIST_FILES):
        if required not in relative_files:
            raise ValueError(f"sdist is missing required file: {required}")
    for prefix in _REQUIRED_SDIST_PREFIXES:
        if not any(name.startswith(prefix) for name in relative_files):
            raise ValueError(f"sdist is missing required prefix: {prefix}")
    return ArchiveReport(member_count=member_count, payload_bytes=payload_bytes)


def _validate_wheel_mode(member: zipfile.ZipInfo) -> None:
    if member.create_system != 3:
        return
    file_type = stat.S_IFMT(member.external_attr >> 16)
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise ValueError(f"unsupported wheel member type: {member.filename}")
    if member.is_dir() and file_type == stat.S_IFREG:
        raise ValueError(f"unsupported wheel member type: {member.filename}")
    if not member.is_dir() and file_type == stat.S_IFDIR:
        raise ValueError(f"unsupported wheel member type: {member.filename}")


def verify_wheel(path: Path) -> ArchiveReport:
    member_count = 0
    payload_bytes = 0
    names: set[str] = set()
    normalized_names: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            member_count += 1
            parts = _validated_parts(member.filename)
            normalized_name = "/".join(parts)
            if normalized_name in normalized_names:
                raise ValueError(f"duplicate normalized wheel member: {normalized_name}")
            normalized_names.add(normalized_name)
            _validate_wheel_mode(member)
            names.add(member.filename)
            if member.is_dir():
                continue
            payload = archive.read(member)
            payload_bytes += len(payload)
            _scan_payload(member.filename, payload)
    if SPA_INDEX not in names:
        raise ValueError(f"wheel is missing exact SPA index.html: {SPA_INDEX}")
    if not any(SPA_CSS.fullmatch(name) for name in names):
        raise ValueError("wheel is missing hashed SPA CSS")
    if not any(SPA_JAVASCRIPT.fullmatch(name) for name in names):
        raise ValueError("wheel is missing hashed SPA JavaScript")
    return ArchiveReport(member_count=member_count, payload_bytes=payload_bytes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sdist", type=Path)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()

    sdist = verify_sdist(args.sdist)
    wheel = verify_wheel(args.wheel)
    print(f"sdist: {sdist.member_count} members, {sdist.payload_bytes} payload bytes")
    print(f"wheel: {wheel.member_count} members, {wheel.payload_bytes} payload bytes")


if __name__ == "__main__":
    main()
