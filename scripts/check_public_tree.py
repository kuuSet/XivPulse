"""Fail when the Git publish set contains private runtime files or obvious secrets."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_NAMES = {".env", ".env.server", "auth.json"}
FORBIDDEN_SUFFIXES = {
    ".crt",
    ".db",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
}
FORBIDDEN_PARTS = {"data", "logs", "model-cache", "backups", "playwright-report"}
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "OpenAI-style key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "personal Windows path": re.compile(r"\b[A-Za-z]:\\Users\\[^\\\s]+", re.IGNORECASE),
    "known private provider host": re.compile(r"eachen" r"-station\.site", re.IGNORECASE),
}


def publishable_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / value.decode("utf-8") for value in result.stdout.split(b"\0") if value]


def main() -> int:
    findings: list[str] = []
    for path in publishable_paths():
        relative = path.relative_to(ROOT)
        lowered_parts = {part.casefold() for part in relative.parts}
        if path.name.casefold() in FORBIDDEN_NAMES:
            findings.append(f"forbidden file: {relative}")
            continue
        if path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            findings.append(f"forbidden file type: {relative}")
            continue
        if lowered_parts & FORBIDDEN_PARTS:
            findings.append(f"private runtime directory: {relative}")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                findings.append(f"{label}: {relative}")
    if findings:
        print("Public-tree safety check failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Public-tree safety check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
