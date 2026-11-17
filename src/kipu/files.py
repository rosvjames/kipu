"""Project file discovery. Skips vendored/generated folders and never yields secret files."""
from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path

IGNORE_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", "dist", "build", ".kipu", ".idea", ".vscode",
    # dbt artifacts and vendored packages
    "dbt_packages", "dbt_modules", "target", "logs",
}
SECRET_PATTERNS = [".env", ".env.*", "*.pem", "*.p8", "*.key", "*.p12", "*.pfx", "kaggle.json", "id_rsa*", "*.secret*"]
# Extensions the scanners care about (also what the watcher watches).
WATCHED_EXT = {".py", ".sql", ".yml", ".yaml", ".json", ".csv"}


def is_secret(name: str) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in SECRET_PATTERNS) and not name.endswith(".example")


def walk(root: Path, exts: set[str] | None = None):
    """Yield project files (absolute Paths) under root, sorted, skipping ignored dirs and secrets."""
    exts = exts or WATCHED_EXT
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if is_secret(name):
                continue
            p = Path(dirpath) / name
            if p.suffix.lower() in exts:
                yield p


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def snapshot_mtimes(root: Path) -> dict[str, float]:
    out = {}
    for p in walk(root):
        try:
            out[rel(root, p)] = p.stat().st_mtime
        except OSError:
            pass
    return out


def comment_above(lines: list[str], line: int, marker: str = "#") -> str:
    """The comment block right above a 1-based line (or starting at it), as plain text. '' if none."""
    out = []
    i = line - 2
    while i >= 0 and lines[i].strip().startswith(marker):
        out.append(lines[i].strip()[len(marker):].strip())
        i -= 1
    text = " ".join(t for t in (x.strip("-= ") for x in reversed(out)) if t)   # "# ---- TITLE ----" -> "TITLE"
    return " ".join(text.split())


def leading_comment(text: str, marker: str = "--") -> str:
    """Comment lines at the very top of a file, as plain text."""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith(marker):
            out.append(s[len(marker):].strip())
        elif s:
            break
    return " ".join(" ".join(out).split())
