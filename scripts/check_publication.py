from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

FORBIDDEN_NAMES = {
    "auth.json",
    "pipeline_index.yaml",
    "state.json",
}
FORBIDDEN_PARTS = {
    ".agents",
    ".claude",
    ".codex",
    "backups",
    "captures",
    "doc_lake",
    "doc_mart",
    "doc_wh",
    "factory-backup",
    "inbox",
    "local-private",
    "logs",
    "recordings",
    "runtime",
    "transcripts",
}
FORBIDDEN_SUFFIXES = {
    ".bin",
    ".dump",
    ".elf",
    ".hex",
    ".img",
    ".key",
    ".nvs",
    ".p12",
    ".pcap",
    ".pcapng",
    ".pem",
    ".pfx",
    ".uf2",
}
TEXT_SUFFIXES = {
    "",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".html",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
CONTENT_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "OpenAI key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "local user path": re.compile(
        r"(?i)(?:[A-Z]:[/\\]Users[/\\]|/Users/|/home/)[^/\\\s]+"
    ),
    "temporary runtime path": re.compile(r"(?i)(?:AppData[/\\]Local[/\\]Temp|scratchpad)"),
    "email address": re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    "hardware identifier": re.compile(r"(?i)\b[0-9A-F]{2}(?:[:-][0-9A-F]{2}){5}\b"),
    "Windows port": re.compile(r"(?i)\bCOM\d+\b"),
    "internal metadata": re.compile(r"(?im)^sensitivity:\s*internal\s*$"),
}


SKIPPED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pio",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}


def candidate_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    tracked = {ROOT / Path(item.decode()) for item in result.stdout.split(b"\0") if item}
    working_tree = {
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not SKIPPED_PARTS.intersection(path.relative_to(ROOT).parts)
    }
    return sorted(tracked | working_tree)


def inspect(path: Path) -> list[str]:
    relative = path.relative_to(ROOT)
    findings: list[str] = []
    if path.is_symlink():
        findings.append("symbolic link")
    if path.name in FORBIDDEN_NAMES:
        findings.append("forbidden filename")
    if FORBIDDEN_PARTS.intersection(relative.parts):
        findings.append("forbidden directory")
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        findings.append("forbidden artifact")
    if path == SELF or path.name == ".gitignore" or path.suffix.lower() not in TEXT_SUFFIXES:
        return findings
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        findings.append("non UTF-8 text")
        return findings
    findings.extend(name for name, pattern in CONTENT_PATTERNS.items() if pattern.search(text))
    return findings


def main() -> int:
    failures: list[tuple[Path, list[str]]] = []
    for path in candidate_files():
        findings = inspect(path)
        if findings:
            failures.append((path.relative_to(ROOT), findings))
    if failures:
        for path, findings in failures:
            print(f"{path}: {', '.join(findings)}")
        return 1
    print("publication boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
