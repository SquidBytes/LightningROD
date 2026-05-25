#!/usr/bin/env python3
"""Pre-commit content checks.

Subchecks:
  pii   — scan staged additions (any file) for personal tokens listed in
          ~/.config/lightningrod/pii-tokens.txt (override with
          $LIGHTNINGROD_PII_TOKENS). Multi-word tokens also match
          hyphen/underscore/concatenated forms, e.g. "Mike Smith" matches
          "mike smith", "mike-smith", "mike_smith", "mikesmith"
          (all case-insensitive).
  refs  — scan added Python comment/docstring lines for internal planning
          references (decision tokens, phase IDs, planning artifact filenames).

Usage:
  scripts/checker.py                     # all checks, staged mode (pre-commit)
  scripts/checker.py --check pii         # only PII
  scripts/checker.py --check refs        # only refs
  scripts/checker.py --check refs --all  # refs full-tree scan (not staged)

Exit codes: 0 ok / skipped, 1 violations, 2 invocation or scan error.
"""

from __future__ import annotations

import argparse
import ast
import io
import os
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# Shared git helpers
# --------------------------------------------------------------------------- #


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


@dataclass(frozen=True)
class Addition:
    """One added line in the staged diff."""

    path: str
    lineno: int
    text: str


def staged_additions(pathspecs: Iterable[str] | None = None) -> list[Addition]:
    """Parse `git diff --cached -U0` into (file, new-lineno, added-line) tuples."""
    cmd = ["diff", "--cached", "--diff-filter=ACMR", "-U0"]
    if pathspecs:
        cmd.append("--")
        cmd.extend(pathspecs)
    diff = _git(*cmd)

    results: list[Addition] = []
    current_file: str | None = None
    new_lineno = 0
    hunk_re = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("@@"):
            m = hunk_re.search(line)
            new_lineno = int(m.group(1)) - 1 if m else 0
            continue
        if line.startswith("+"):
            new_lineno += 1
            if current_file is not None:
                results.append(Addition(current_file, new_lineno, line[1:]))
    return results


# --------------------------------------------------------------------------- #
# PII check
# --------------------------------------------------------------------------- #


PII_TOKEN_PATH_DEFAULT = Path.home() / ".config" / "lightningrod" / "pii-tokens.txt"


def _load_pii_tokens(path: Path) -> list[str]:
    tokens: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            tokens.append(line)
    return tokens


def _pii_variants(tok: str) -> list[str]:
    """Lowercase variants to match. Multi-word tokens also produce
    hyphen/underscore/concatenated forms; single-word tokens are unchanged."""
    base = tok.lower()
    parts = base.split()
    if len(parts) < 2:
        return [base]
    return [" ".join(parts), "-".join(parts), "_".join(parts), "".join(parts)]


def check_pii() -> int:
    token_path = Path(os.environ.get("LIGHTNINGROD_PII_TOKENS", str(PII_TOKEN_PATH_DEFAULT)))
    if not token_path.exists():
        return 0
    tokens = _load_pii_tokens(token_path)
    if not tokens:
        return 0

    expanded = [(tok, v) for tok in tokens for v in _pii_variants(tok)]

    hits: list[tuple[Addition, str]] = []
    for add in staged_additions():
        lc = add.text.lower()
        seen: set[str] = set()
        for orig_tok, variant in expanded:
            if orig_tok in seen:
                continue
            if variant in lc:
                hits.append((add, orig_tok))
                seen.add(orig_tok)

    if not hits:
        return 0

    print("checker[pii]: BLOCKED — personal tokens detected in staged additions:", file=sys.stderr)
    for add, tok in hits[:20]:
        masked = tok[:2] + "***" if len(tok) > 4 else "***"
        print(f"  {add.path}:{add.lineno}: matched token starting '{masked}'", file=sys.stderr)
        print(f"    > {add.text.strip()[:120]}", file=sys.stderr)
    if len(hits) > 20:
        print(f"  ... and {len(hits) - 20} more", file=sys.stderr)
    print(file=sys.stderr)
    print(
        "Replace with a placeholder (e.g. <your-vin>, owner@example.com).",
        file=sys.stderr,
    )
    print(f"Token list: {token_path}", file=sys.stderr)
    print("Bypass (not recommended): git commit --no-verify", file=sys.stderr)
    return 1


# --------------------------------------------------------------------------- #
# Reference-hygiene check
# --------------------------------------------------------------------------- #


REF_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("decision token", re.compile(r"\bD(?:-[A-Z])?(?:-?\d+(?:[-.]\d+)*)\b")),
    ("phase reference", re.compile(r"\bphase\s+\d+(?:[.-]\d+)?\b", re.IGNORECASE)),
    ("planning artifact", re.compile(r"\b(?:CONTEXT|PLAN|SUMMARY)\.md\b", re.IGNORECASE)),
]

REF_DEFAULT_DIRS = ["web", "scripts", "tests", "db", "docs"]
REF_SKIP_PARTS = {".git", ".venv", "node_modules", "site", "__pycache__"}
REF_SKIP_SUFFIXES = ("db/migrations/versions",)


@dataclass(frozen=True)
class RefHit:
    path: str
    lineno: int
    kind: str  # "comment" or "docstring"
    rule: str
    snippet: str


def _ref_is_skipped(path: Path) -> bool:
    if any(part in REF_SKIP_PARTS for part in path.parts):
        return True
    norm = path.as_posix()
    return any(norm.startswith(p) or f"/{p}" in norm for p in REF_SKIP_SUFFIXES)


def _ref_comment_doc_lines(source: str) -> Iterator[tuple[int, str, str]]:
    """Yield (lineno, kind, text) for comments and docstrings in source."""
    reader = io.StringIO(source).readline
    for tok in tokenize.generate_tokens(reader):
        if tok.type != tokenize.COMMENT:
            continue
        text = tok.string.lstrip("#").strip()
        if text:
            yield tok.start[0], "comment", text

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr):
            continue
        value = first.value
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        for idx, line_text in enumerate(value.value.splitlines()):
            stripped = line_text.strip()
            if stripped:
                yield first.lineno + idx, "docstring", stripped


def _ref_violations_in_source(
    path: Path, source: str, line_filter: set[int] | None
) -> list[RefHit]:
    out: list[RefHit] = []
    for lineno, kind, text in _ref_comment_doc_lines(source):
        if line_filter is not None and lineno not in line_filter:
            continue
        for rule_name, pattern in REF_RULES:
            if pattern.search(text):
                out.append(RefHit(path.as_posix(), lineno, kind, rule_name, text))
    return out


def _ref_discover_python(paths: Iterable[str] | None) -> list[Path]:
    resolved: list[Path] = []
    sources: Iterable[str] = paths or REF_DEFAULT_DIRS
    for raw in sources:
        p = Path(raw)
        if p.is_dir():
            resolved.extend(c for c in p.rglob("*.py") if not _ref_is_skipped(c))
        elif p.suffix == ".py" and p.exists() and not _ref_is_skipped(p):
            resolved.append(p)
    return sorted(set(resolved))


def _ref_staged_python(explicit: Iterable[str] | None) -> list[Path]:
    if explicit:
        return sorted({Path(p) for p in explicit if Path(p).suffix == ".py" and Path(p).exists()
                       and not _ref_is_skipped(Path(p))})
    raw = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "--", "*.py")
    out: list[Path] = []
    for line in raw.splitlines():
        p = Path(line.strip())
        if p and p.suffix == ".py" and p.exists() and not _ref_is_skipped(p):
            out.append(p)
    return sorted(set(out))


def _ref_staged_added_lines(path: Path) -> set[int]:
    diff = _git("diff", "--cached", "-U0", "--", path.as_posix())
    added: set[int] = set()
    new_line: int | None = None
    hunk_re = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for raw in diff.splitlines():
        if raw.startswith("@@"):
            m = hunk_re.search(raw)
            new_line = int(m.group(1)) if m else None
            continue
        if new_line is None or raw.startswith("+++"):
            continue
        if raw.startswith("+"):
            added.add(new_line)
            new_line += 1
        elif raw.startswith("\\ No newline"):
            continue
        elif raw.startswith("-") and not raw.startswith("---"):
            continue
        else:
            new_line += 1
    return added


def check_refs(*, staged: bool, paths: list[str] | None) -> int:
    try:
        files = _ref_staged_python(paths) if staged else _ref_discover_python(paths)
    except RuntimeError as exc:
        print(f"checker[refs]: {exc}", file=sys.stderr)
        return 2

    if not files:
        return 0

    hits: list[RefHit] = []
    try:
        for path in files:
            if staged:
                added = _ref_staged_added_lines(path)
                if not added:
                    continue
                source = _git("show", f":{path.as_posix()}")
                hits.extend(_ref_violations_in_source(path, source, added))
            else:
                source = path.read_text(encoding="utf-8")
                hits.extend(_ref_violations_in_source(path, source, None))
    except (RuntimeError, OSError, SyntaxError) as exc:
        print(f"checker[refs]: scan failed: {exc}", file=sys.stderr)
        return 2

    if not hits:
        return 0

    print("checker[refs]: BLOCKED — planning references in code comments/docstrings:", file=sys.stderr)
    for h in sorted(hits, key=lambda x: (x.path, x.lineno, x.rule)):
        print(f"  {h.path}:{h.lineno}: {h.kind}: {h.rule}: {h.snippet}", file=sys.stderr)
    print(file=sys.stderr)
    print(
        "Remove internal IDs/references (D2, D-B3, 'Phase 29', CONTEXT.md, …) from added lines.",
        file=sys.stderr,
    )
    return 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


CHECKS = ("pii", "refs", "all")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check",
        choices=CHECKS,
        default="all",
        help="Which check to run (default: all).",
    )
    parser.add_argument(
        "--all",
        dest="full_tree",
        action="store_true",
        help="Full-tree scan (refs only). Default is staged-content scan.",
    )
    parser.add_argument(
        "--paths",
        nargs="*",
        help="Limit refs scan to these files/dirs. Ignored by pii (always staged).",
    )
    args = parser.parse_args(argv)

    rc = 0
    if args.check in ("pii", "all"):
        rc = max(rc, check_pii())
    if args.check in ("refs", "all"):
        rc = max(rc, check_refs(staged=not args.full_tree, paths=args.paths))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
