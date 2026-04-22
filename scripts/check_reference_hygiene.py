#!/usr/bin/env python3
"""Detect internal planning references inside Python comments and docstrings.

This script flags shorthand that makes source text hard to read outside the
planning workflow, such as D-prefixed decision tokens, phase references, and
direct mentions of planning artifact files.

By default, it scans Python files in the current tree. With ``--staged``, it
checks only added lines in the index (useful for pre-commit enforcement).
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


@dataclass(frozen=True)
class LineEntry:
    line: int
    kind: str
    text: str


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    kind: str
    rule_name: str
    snippet: str


RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "decision token",
        re.compile(r"\bD(?:-[A-Z])?(?:-?\d+(?:[-.]\d+)*)\b"),
    ),
    (
        "phase reference",
        re.compile(r"\bphase\s+\d+(?:[.-]\d+)?\b", re.IGNORECASE),
    ),
    (
        "planning artifact reference",
        re.compile(r"\b(?:CONTEXT|PLAN|SUMMARY)\.md\b", re.IGNORECASE),
    ),
]

DEFAULT_SCAN_DIRS = ["web", "scripts", "tests", "db", "docs"]
SKIP_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "site",
    "__pycache__",
}
SKIP_PATH_SUFFIXES = (
    "db/migrations/versions",
)


def _is_skipped(path: Path) -> bool:
    norm = path.as_posix()
    if any(part in SKIP_PARTS for part in path.parts):
        return True
    return any(norm.startswith(prefix) or f"/{prefix}" in norm for prefix in SKIP_PATH_SUFFIXES)


def iter_python_comment_doc_lines(source: str) -> Iterator[LineEntry]:
    """Yield comment/docstring lines with source line numbers (1-based)."""

    # Token comments (line comments only, e.g. # ...)
    reader = io.StringIO(source).readline
    for tok in tokenize.generate_tokens(reader):
        if tok.type != tokenize.COMMENT:
            continue
        text = tok.string.lstrip("#").strip()
        if not text:
            continue
        yield LineEntry(line=tok.start[0], kind="comment", text=text)

    # Docstring lines at module/class/function scope
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(
            node,
            (
                ast.Module,
                ast.ClassDef,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        ):
            continue
        if not getattr(node, "body", None):
            continue
        first = node.body[0]
        if not (
            isinstance(first, ast.Expr)
            and isinstance(getattr(first, "value", None), ast.Constant)
            and isinstance(first.value.value, str)
        ):
            continue

        for idx, line_text in enumerate(first.value.value.splitlines()):
            stripped = line_text.strip()
            if not stripped:
                continue
            yield LineEntry(line=first.lineno + idx, kind="docstring", text=stripped)


def find_violations(
    *,
    path: Path,
    source: str,
    line_filter: set[int] | None = None,
) -> list[Violation]:
    """Return violations for one Python source blob.

    If ``line_filter`` is provided, only entries whose line number is in the
    set are evaluated.
    """
    out: list[Violation] = []
    for entry in iter_python_comment_doc_lines(source):
        if line_filter is not None and entry.line not in line_filter:
            continue
        for rule_name, pattern in RULES:
            if not pattern.search(entry.text):
                continue
            out.append(
                Violation(
                    path=path.as_posix(),
                    line=entry.line,
                    kind=entry.kind,
                    rule_name=rule_name,
                    snippet=entry.text,
                )
            )
    return out


def discover_python_files(paths: Iterable[str] | None = None) -> list[Path]:
    """Resolve files from explicit paths or default source roots."""
    resolved: list[Path] = []
    if paths:
        for raw in paths:
            p = Path(raw)
            if p.is_dir():
                for child in p.rglob("*.py"):
                    if not _is_skipped(child):
                        resolved.append(child)
                continue
            if p.suffix == ".py" and p.exists() and not _is_skipped(p):
                resolved.append(p)
        return sorted(set(resolved))

    for root in DEFAULT_SCAN_DIRS:
        p = Path(root)
        if not p.exists():
            continue
        for child in p.rglob("*.py"):
            if not _is_skipped(child):
                resolved.append(child)
    return sorted(set(resolved))


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def staged_python_files(explicit_paths: Iterable[str] | None = None) -> list[Path]:
    """Return staged Python files (ACMR) optionally filtered to explicit paths."""
    if explicit_paths:
        out: list[Path] = []
        for raw in explicit_paths:
            p = Path(raw)
            if p.suffix == ".py" and p.exists() and not _is_skipped(p):
                out.append(p)
        return sorted(set(out))

    raw = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "--", "*.py")
    out = []
    for line in raw.splitlines():
        p = Path(line.strip())
        if p and p.suffix == ".py" and p.exists() and not _is_skipped(p):
            out.append(p)
    return sorted(set(out))


def staged_added_lines(path: Path) -> set[int]:
    """Return 1-based new-file line numbers added in the staged diff for path."""
    diff = _git("diff", "--cached", "-U0", "--", path.as_posix())
    added: set[int] = set()
    new_line: int | None = None
    hunk_re = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    for raw in diff.splitlines():
        if raw.startswith("@@"):
            m = hunk_re.search(raw)
            if not m:
                new_line = None
                continue
            new_line = int(m.group(1))
            continue

        if new_line is None:
            continue

        if raw.startswith("+++"):
            continue

        if raw.startswith("+"):
            added.add(new_line)
            new_line += 1
            continue

        if raw.startswith("-") and not raw.startswith("---"):
            continue

        if raw.startswith("\\ No newline"):
            continue

        # Context line (unlikely with -U0 but keep parser robust)
        new_line += 1

    return added


def read_staged_file(path: Path) -> str:
    """Read file contents from index for an existing staged path."""
    return _git("show", f":{path.as_posix()}")


def run_full(paths: list[Path]) -> list[Violation]:
    violations: list[Violation] = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        violations.extend(find_violations(path=path, source=source))
    return violations


def run_staged(paths: list[Path]) -> list[Violation]:
    violations: list[Violation] = []
    for path in paths:
        added = staged_added_lines(path)
        if not added:
            continue
        source = read_staged_file(path)
        violations.extend(find_violations(path=path, source=source, line_filter=added))
    return violations


def print_report(violations: list[Violation]) -> None:
    for v in sorted(violations, key=lambda x: (x.path, x.line, x.rule_name)):
        print(f"{v.path}:{v.line}: {v.kind}: {v.rule_name}: {v.snippet}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paths",
        nargs="*",
        help="Specific files/directories to scan. Defaults to core source dirs.",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Scan only added lines from staged Python diffs.",
    )
    args = parser.parse_args(argv)

    try:
        paths = (
            staged_python_files(args.paths) if args.staged else discover_python_files(args.paths)
        )
    except RuntimeError as exc:
        print(f"reference-hygiene: {exc}", file=sys.stderr)
        return 2

    if not paths:
        print("reference-hygiene: no Python files to scan")
        return 0

    try:
        violations = run_staged(paths) if args.staged else run_full(paths)
    except (RuntimeError, OSError, SyntaxError) as exc:
        print(f"reference-hygiene: scan failed: {exc}", file=sys.stderr)
        return 2

    if not violations:
        print("reference-hygiene: OK")
        return 0

    print_report(violations)
    print(f"reference-hygiene: found {len(violations)} violation(s)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
