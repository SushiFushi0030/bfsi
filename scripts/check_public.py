#!/usr/bin/env python3
"""Offline publication guard. Prints locations/categories, never matched values.

Scan the working tree and, when this directory is a Git root, every indexed blob.
This deliberately is a conservative heuristic, not proof of anonymisation.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


ENVIRONMENT_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".pnpm-store", "dist", "build", ".cache",
    "var", "runs", "evidence", ".codex", "*.egg-info",
}
PRIVATE_DIRS = {
    "var", "runs", "evidence", "requests", "raw_responses", "execution",
    "provenance", ".codex", ".venv", "venv", "node_modules", ".pnpm-store",
    "__pycache__", "dist", "build", "production",
}
PRIVATE_FILENAMES = {
    "auth.json", "cookies.txt", "cookies.json", "latest.json", ".DS_Store",
    "inference_inputs.json", "full_semantic_dataset_v0.1.json",
}
PRIVATE_SUFFIXES = (".log", ".events.jsonl", ".stderr.txt", ".zip", ".tar", ".tar.gz", ".tgz", ".sqlite", ".db")
PERSONAL_PATH = re.compile(r"(?:/Users/|/home/)[A-Za-z0-9_.-]+(?:/|$)|[A-Za-z]:\\Users\\[^\\\s]+", re.I)
PEM = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----")
TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{24,}|AKIA[A-Z0-9]{16})\b")
SECRET_ASSIGNMENT = re.compile(
    r'''(?ix)(?:["']?)(?:sessdata|bili_jct|api[_-]?key|access[_-]?token|refresh[_-]?token|password|client[_-]?secret)["']?\s*[:=]\s*["']([^"'\n]{10,})["']'''
)
COOKIE_HEADER = re.compile(r'''(?ix)["'](?:cookie|authorization)["']\s*:\s*["']([^"'\n]{15,})["']''')
ENV_SECRET = re.compile(
    r'''(?ix)^\s*(?:export\s+)?[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|REFRESH_TOKEN|PASSWORD|CLIENT_SECRET|SESSDATA|BILI_JCT)\s*=\s*([^\s"'\#]{10,})'''
)
PLACEHOLDER = re.compile(r"example|synthetic|placeholder|redacted|change.?me|your[_ -]|test|dummy|\$\{|\*{3}|getenv|environ", re.I)


def ignored_dir(name: str) -> bool:
    return name in ENVIRONMENT_DIRS or name.endswith(".egg-info")


def path_problem(relative: str) -> str | None:
    path = PurePosixPath(relative)
    if any(part in PRIVATE_DIRS or re.fullmatch(r"task\s*0?[1-7]", part, re.I) for part in path.parts):
        return "private/generated-directory"
    if "raw" in path.parts and not (path.parts[0] == "examples" or path.parts[:2] == ("tests", "fixtures")):
        return "raw-collection-directory"
    if path.name.startswith(".env") and path.name not in {".env.example", ".env.sample", ".env.template"}:
        return "environment-secret-file"
    if path.name in PRIVATE_FILENAMES or path.name.endswith(PRIVATE_SUFFIXES):
        return "private/generated-file"
    if path.suffix.lower() in {".pem", ".p12", ".pfx", ".key", ".har"}:
        return "credential-or-browser-capture-file"
    return None


def scan_bytes(relative: str, data: bytes, label: str, maximum: int) -> list[tuple[str, str, int, str]]:
    findings = []
    if len(data) > maximum:
        findings.append((relative, label, 0, "oversize-file"))
        return findings
    if b"\0" in data:
        return findings
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return findings
    for lineno, line in enumerate(content.splitlines(), 1):
        for kind, pattern in (("personal-absolute-path", PERSONAL_PATH), ("private-key", PEM), ("credential-token", TOKEN)):
            if pattern.search(line):
                findings.append((relative, label, lineno, kind))
        for kind, pattern in (("literal-secret-assignment", SECRET_ASSIGNMENT), ("literal-credential-header", COOKIE_HEADER), ("literal-environment-secret", ENV_SECRET)):
            for match in pattern.finditer(line):
                value = match.group(1)
                # Schema names, variable references, fake tests and placeholders are safe.
                if PLACEHOLDER.search(value) or value.startswith(("{", "$", "<", "os.", "process.")):
                    continue
                findings.append((relative, label, lineno, kind))
    return findings


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--max-mib", type=int, default=5)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir() or args.max_mib < 1:
        parser.error("root must be an existing directory and max-mib must be positive")
    maximum = args.max_mib * 1024 * 1024
    findings: list[tuple[str, str, int, str]] = []
    count = 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        # Ignored local environments never enter publication candidates; indexed
        # copies are checked separately even if .gitignore now hides them.
        for name in dirs:
            directory = Path(folder) / name
            if directory.is_symlink() and not ignored_dir(name):
                findings.append((directory.relative_to(root).as_posix(), "working", 0, "symlink-needs-review"))
        dirs[:] = [name for name in dirs if not ignored_dir(name)]
        for name in files:
            path = Path(folder) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                findings.append((relative, "working", 0, "symlink-needs-review"))
                continue
            problem = path_problem(relative)
            if problem:
                findings.append((relative, "working", 0, problem))
            try:
                if path.stat().st_size > maximum:
                    findings.append((relative, "working", 0, "oversize-file"))
                else:
                    findings.extend(scan_bytes(relative, path.read_bytes(), "working", maximum))
                count += 1
            except OSError:
                findings.append((relative, "working", 0, "unreadable-file"))
    indexed = 0
    # Avoid accidentally scanning the parent workspace repository.
    if (root / ".git").exists():
        result = git(root, "ls-files", "--stage", "-z")
        if result.returncode:
            findings.append((".git", "index", 0, "git-index-unreadable"))
        else:
            indexed_paths = {}
            for entry in result.stdout.split(b"\0"):
                if entry:
                    metadata, name = entry.split(b"\t", 1)
                    indexed_paths[os.fsdecode(name)] = metadata.split()[0]
            for relative, mode in sorted(indexed_paths.items()):
                if mode == b"120000":
                    findings.append((relative, "index", 0, "symlink-needs-review"))
                problem = path_problem(relative)
                if problem:
                    findings.append((relative, "index", 0, problem))
                result = git(root, "show", ":" + relative)
                if result.returncode:
                    findings.append((relative, "index", 0, "indexed-blob-unreadable"))
                    continue
                findings.extend(scan_bytes(relative, result.stdout, "index", maximum))
                indexed += 1
    for relative, label, line, kind in sorted(set(findings)):
        print(f"{relative}:{line} [{label}] {kind}")
    print(f"Checked {count} working files and {indexed} indexed blobs; {len(set(findings))} finding(s).")
    print("Scope: current files/index only; review binary media, real identities and prior Git history separately.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
