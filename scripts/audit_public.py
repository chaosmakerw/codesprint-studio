"""Read-only release audit. Reports categories, never the matched secret text.

Run without arguments for the public working tree, --staged before committing,
and --history before pushing. This is a useful check, not a complete detector of
all personal information, copyright issues, or credentials.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
import sys
import zlib

PUBLIC_DIRS = frozenset({"src", "web", "tests", "examples", "docs", ".github", "scripts", "prompts"})
PUBLIC_ROOT = frozenset({
    "README.md", "LICENSE", "NOTICE", "SECURITY.md", "CONTRIBUTING.md",
    "THIRD_PARTY_NOTICES.md", ".gitignore", ".env.example", "run.py", "START.cmd",
})
TEXT_EXTENSIONS = frozenset({
    ".py", ".js", ".cjs", ".mjs", ".html", ".css", ".md", ".txt", ".json",
    ".yaml", ".yml", ".toml", ".xml", ".svg", ".csv", ".cmd", ".sh", ".gitignore",
})
FORBIDDEN_EXTENSIONS = frozenset({
    ".sqlite", ".sqlite3", ".db", ".log", ".pem", ".p12", ".jks", ".key",
    ".keystore", ".apk", ".aab", ".mp3", ".mp4", ".pdf", ".zip", ".exe",
    ".dll", ".class", ".jar", ".pyc", ".pyo",
})
FORBIDDEN_PARTS = frozenset({
    "node_modules", "__pycache__", ".venv", "private-signing", "uploads", "backups",
    "data", "output", "dist", ".idea", ".vscode",
})
SECRET_PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----\s+[A-Za-z0-9+/=]{24}")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("cloud-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("model-api-key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}\b")),
    ("private-email", re.compile(r"\b[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("personal-home-path", re.compile(r"(?:[A-Za-z]:[/\\]" + "Users" + r"[/\\]|/" + "Users" + r"/|/" + "home" + r"/)(?!<|YOUR_USER\b|username\b|user\b)[^\s\"'`<>/\\]+", re.I)),
    ("personal-study-record", re.compile(r"sprint-day:w\d+:\d{4}|\"(?:completedDays|learningProgressSnapshot)\"\s*:")),
)
PUBLIC_EMAIL = re.compile(r"^(?:\d+\+)?[a-zA-Z0-9-]+@users\.noreply\.github\.com$")
EXAMPLE_EMAIL_DOMAINS = frozenset({"example.com", "example.org", "example.net", "users.noreply.github.com"})


def sensitive_values() -> tuple[str, ...]:
    """Host-specific values are read only in memory and excluded from reports."""
    values = [os.environ.get("USERNAME", ""), os.environ.get("COMPUTERNAME", ""), socket.gethostname()]
    return tuple(value for value in set(values) if len(value) >= 2 and value.lower() not in {"user", "root", "runner"})


def safe_display(path: str, values: tuple[str, ...]) -> str:
    for value in values:
        path = re.sub(re.escape(value), "[redacted]", path, flags=re.I)
    return path


def path_issue(name: str) -> str | None:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return "unsafe-path"
    if len(path.parts) == 1:
        if name not in PUBLIC_ROOT:
            return "outside-public-allowlist"
    elif path.parts[0] not in PUBLIC_DIRS:
        return "outside-public-allowlist"
    if any(part in FORBIDDEN_PARTS for part in path.parts[1:]):
        return "private-or-generated-directory"
    if path.name == ".env" or (path.name.startswith(".env.") and name != ".env.example"):
        return "private-environment-file"
    if path.suffix.lower() in FORBIDDEN_EXTENSIONS or ".sqlite3-" in path.name:
        return "private-data-or-binary"
    if path.suffix.lower() == ".png":
        return None if len(path.parts) == 3 and path.parts[:2] == ("docs", "screenshots") else "binary-outside-screenshot-directory"
    if len(path.parts) > 1 and path.suffix.lower() not in TEXT_EXTENSIONS and path.name not in {"LICENSE", "NOTICE"}:
        return "unsupported-file-type"
    return None


def scan_content(blob: bytes, values: tuple[str, ...]) -> list[str]:
    if len(blob) > 10 * 1024 * 1024:
        return ["public-file-too-large"]
    try:
        content = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ["unexpected-binary"]
    findings = set()
    for value in values:
        if value.casefold() in content.casefold():
            findings.add("current-host-personal-identity")
    for category, pattern in SECRET_PATTERNS:
        if category == "private-email":
            for match in pattern.finditer(content):
                domain = match.group(0).rsplit("@", 1)[1].lower()
                if domain not in EXAMPLE_EMAIL_DOMAINS:
                    findings.add(category)
        elif pattern.search(content):
            findings.add(category)
    return sorted(findings)


def scan_png(blob: bytes) -> list[str]:
    """Permit only structural PNG chunks; no EXIF, text, or arbitrary metadata."""
    if len(blob) > 10 * 1024 * 1024 or not blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return ["invalid-screenshot-png"]
    allowed = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"sRGB", b"gAMA", b"cHRM", b"pHYs"}
    offset = 8
    seen_header = False
    seen_pixels = False
    while offset + 12 <= len(blob):
        length = int.from_bytes(blob[offset:offset + 4], "big")
        kind = blob[offset + 4:offset + 8]
        if kind not in allowed:
            return ["screenshot-metadata-not-allowed"]
        if offset + 12 + length > len(blob):
            return ["invalid-screenshot-png"]
        chunk = blob[offset + 4:offset + 8 + length]
        checksum = int.from_bytes(blob[offset + 8 + length:offset + 12 + length], "big")
        if zlib.crc32(chunk) & 0xffffffff != checksum:
            return ["invalid-screenshot-png"]
        if not seen_header:
            if kind != b"IHDR" or length != 13:
                return ["invalid-screenshot-png"]
            seen_header = True
        elif kind == b"IHDR":
            return ["invalid-screenshot-png"]
        if kind == b"IDAT":
            seen_pixels = True
        offset += 12 + length
        if kind == b"IEND":
            if length != 0 or not seen_pixels:
                return ["invalid-screenshot-png"]
            return [] if offset == len(blob) else ["trailing-screenshot-data"]
    return ["invalid-screenshot-png"]


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("git-command-failed")
    return result.stdout


def inspect_file(name: str, blob: bytes, values: tuple[str, ...]) -> list[dict[str, str]]:
    issues = []
    issue = path_issue(name)
    if issue:
        issues.append(issue)
    if any(value.casefold() in name.casefold() for value in values):
        issues.append("personal-identity-in-filename")
    issues.extend(scan_png(blob) if name.lower().endswith(".png") else scan_content(blob, values))
    return [{"file": safe_display(name, values), "category": issue} for issue in set(issues)]


def working_files(repo: Path):
    for name in sorted(PUBLIC_ROOT):
        path = repo / name
        if path.is_file():
            yield name, path
    for directory in sorted(PUBLIC_DIRS):
        base = repo / directory
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and not any(part in {"__pycache__", ".pytest_cache"} for part in path.relative_to(base).parts):
                yield path.relative_to(repo).as_posix(), path


def audit(repo: Path, mode: str, expected_author: str) -> dict:
    values = sensitive_values()
    findings = []
    count = 0
    commits = 0
    if mode != "working-tree":
        top = Path(git(repo, "rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
        if top != repo.resolve():
            raise RuntimeError("independent-repository-required")
    if mode == "working-tree":
        for name, path in working_files(repo):
            count += 1
            if path.is_symlink():
                findings.append({"file": safe_display(name, values), "category": "symlink-not-allowed"})
            else:
                findings.extend(inspect_file(name, path.read_bytes(), values))
    elif mode == "staged":
        names = git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z").decode("utf-8").split("\0")
        for name in filter(None, names):
            count += 1
            index_entry = git(repo, "ls-files", "--stage", "-z", "--", name)
            if index_entry.startswith((b"120000 ", b"160000 ")):
                findings.append({"file": safe_display(name, values), "category": "symlink-or-submodule-not-allowed"})
            findings.extend(inspect_file(name, git(repo, "show", ":" + name), values))
    elif mode == "history":
        refs = git(repo, "rev-list", "--all").decode("ascii").splitlines()
        checked_blobs = set()
        for ref in refs:
            commits += 1
            message_issues = scan_content(git(repo, "show", "-s", "--format=%B", ref), values)
            findings.extend({"file": "[commit message]", "category": issue} for issue in message_issues)
            author = git(repo, "show", "-s", "--format=%an%n%ae%n%cn%n%ce", ref).decode("utf-8").splitlines()
            valid_identity = len(author) == 4 and author[0] == expected_author and author[2] == expected_author
            valid_identity = valid_identity and all(PUBLIC_EMAIL.fullmatch(email) and email.split("@", 1)[0].split("+")[-1] == expected_author for email in (author[1], author[3]))
            if not valid_identity:
                findings.append({"file": "[commit identity]", "category": "non-public-git-identity"})
            for entry in git(repo, "ls-tree", "-r", "-z", ref).split(b"\0"):
                if not entry:
                    continue
                metadata, raw_name = entry.split(b"\t", 1)
                mode_bits, kind, blob_id = metadata.decode("ascii").split()
                name = raw_name.decode("utf-8")
                if mode_bits == "120000" or kind != "blob":
                    findings.append({"file": safe_display(name, values), "category": "symlink-or-submodule-not-allowed"})
                    continue
                key = (name, blob_id)
                if key in checked_blobs:
                    continue
                checked_blobs.add(key)
                count += 1
                findings.extend(inspect_file(name, git(repo, "cat-file", "blob", blob_id), values))
    return {"status": "PASS" if not findings else "FAIL", "mode": mode, "files_checked": count,
            "commits_checked": commits, "findings": findings,
            "limitation": "Pattern checks do not prove absence of every secret, personal detail, or rights issue; review the actual release list and screenshots."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--history", action="store_true")
    parser.add_argument("--author", default="chaosmakerw", help="Expected public GitHub account name; never a private identity.")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    selected = "staged" if args.staged else "history" if args.history else "working-tree"
    try:
        result = audit(repo, selected, args.author)
    except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
        result = {"status": "FAIL", "mode": selected, "files_checked": 0, "commits_checked": 0,
                  "findings": [{"file": "[audit input]", "category": "unreadable-or-invalid-input"}]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
