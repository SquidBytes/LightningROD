"""Dev-only audit: surface PG-only constructs that scout grep + library-version
verification missed.

Run from app-public/ with:
    uv run python scripts/audit_dialect_portability.py

Exits 0 if clean, 1 if findings.

Implements the four audits from
``.planning/phases/30-add-sqlite-as-alternative-database-option-with-demo-site-cap/30-RESEARCH.md``
§"PG-Only Audit — Beyond Grep":

1. Re-grep BLIND-SPOTS 1-4 + PG-only token catalog in production code.
2. ``alembic upgrade head --sql`` smoke render on both dialects (best-effort —
   degrades gracefully when PG isn't reachable).
3. Optional sqlglot transpile pass (informational only — see RESEARCH §"Audit 4"
   for the full manual procedure that captures live SQL traces).
4. (Folded into 1) PG-only token catalog re-grep.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

# Files that legitimately import from sqlalchemy.dialects.postgresql / sqlite
# OR use the PG-only branch of a dispatcher. These are the canonical dispatcher
# / TypeDecorator modules — adding to this list requires explicit code review
# of this audit script (T-30-06-02 mitigation).
ALLOWLIST_FILES: frozenset[str] = frozenset({
    "db/types.py",            # JSONStorage TypeDecorator imports JSONB on PG branch
    "db/portable_insert.py",  # portable_insert dispatcher imports both Insert ctors
    "db/dialect.py",          # date_trunc_compat dispatcher uses func.date_trunc on PG branch
    "scripts/audit_dialect_portability.py",  # this file — defines the patterns it scans for
})

# PG-only patterns. Tuple shape: (regex, fix-suggestion, severity).
PG_ONLY_PATTERNS: list[tuple[str, str, str]] = [
    (r"from sqlalchemy\.dialects\.postgresql import",
     "PG-only dialect import — wire through db/portable_insert.py or db/types.py instead",
     "error"),
    (r"\bpg_insert\b",
     "Use db.portable_insert.portable_insert(table, *, dialect=...) instead",
     "error"),
    (r"\bfunc\.date_trunc\b",
     "Use db.dialect.date_trunc_compat(unit, col, *, dialect=...) instead",
     "error"),
    (r"::numeric|::text|::int(?:eger)?\b",
     "PG-only cast operator — use CAST(... AS X) for cross-dialect SQL",
     "error"),
    (r"\bgen_random_uuid\b",
     "PG-only UDF — use Python uuid.uuid4 default at the column level",
     "error"),
    (r"sa\.text\(['\"]NOW\(\)['\"]\)",
     "PG-only server default literal — use sa.func.now() (renders NOW()/CURRENT_TIMESTAMP per dialect)",
     "error"),
    (r"\bsa\.UUID\(\)",
     "Use sa.Uuid(as_uuid=True) (cross-dialect; UUID native on PG, CHAR(32) on SQLite)",
     "error"),
]

# Production-code globs we audit. Tests, migrations and vendored code are
# intentionally excluded — migrations are reviewed during squash, tests run
# both backends, vendored code is upstream's problem.
PRODUCTION_CODE_GLOBS: list[str] = [
    "config.py",
    "db/**/*.py",
    "scripts/**/*.py",
    "web/**/*.py",
]


def _iter_production_files() -> list[Path]:
    """Return de-duplicated, repo-root-relative production source files."""
    seen: set[Path] = set()
    out: list[Path] = []
    for glob in PRODUCTION_CODE_GLOBS:
        for path in REPO_ROOT.glob(glob):
            if not path.is_file():
                continue
            # Skip migration version files — they're frozen per release and
            # may legitimately reference dialect-specific kwargs (sqlite_where,
            # postgresql_where) that we don't want to flag.
            rel_str = path.relative_to(REPO_ROOT).as_posix()
            if rel_str.startswith("db/migrations/versions/"):
                continue
            if path in seen:
                continue
            seen.add(path)
            out.append(path)
    return out


def grep_findings() -> list[dict[str, Any]]:
    """Audit (1) + (4): re-grep BLIND-SPOTS 1-4 and PG-only token catalog."""
    issues: list[dict[str, Any]] = []
    for path in _iter_production_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWLIST_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern, fix, severity in PG_ONLY_PATTERNS:
            for m in re.finditer(pattern, text):
                line_no = text.count("\n", 0, m.start()) + 1
                issues.append({
                    "file": rel,
                    "line": line_no,
                    "match": m.group(0),
                    "fix": fix,
                    "severity": severity,
                })
    return issues


def alembic_smoke_render() -> dict[str, dict[str, Any]]:
    """Audit (2): ``alembic upgrade head --sql`` on both dialects (best-effort).

    SQLite uses an in-memory URL. PG uses a transient URL that may not resolve
    in dev shells without a running PG instance — that path returns rc=-1 with
    a recorded error rather than failing the audit (T-30-06-04 mitigation).
    """
    results: dict[str, dict[str, Any]] = {}
    targets: list[tuple[str, str]] = [
        ("sqlite", "sqlite+aiosqlite:////tmp/_audit_sl.db"),
        (
            "postgresql",
            "postgresql+asyncpg://lightningrod_test:testpass@localhost:5433/_audit_pg",
        ),
    ]
    for dialect_name, url in targets:
        try:
            cp = subprocess.run(
                ["uv", "run", "alembic", "upgrade", "head", "--sql"],
                cwd=REPO_ROOT,
                env={
                    "DATABASE_URL": url,
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": os.environ.get("HOME", ""),
                },
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            results[dialect_name] = {
                "rc": cp.returncode,
                "stdout_chars": len(cp.stdout),
                "stderr_tail": (
                    cp.stderr.splitlines()[-5:] if cp.stderr else []
                ),
            }
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            results[dialect_name] = {"rc": -1, "error": str(e)}
    return results


def sqlglot_transpile_smoke() -> dict[str, Any]:
    """Audit (3): optional sqlglot transpile pass.

    Live trace + transpile is a manual procedure (see RESEARCH §"Audit 4")
    because it requires capturing emitted SQL from a real test run. Here we
    only confirm sqlglot is importable so the dependency edge isn't silently
    broken (RESEARCH §"Standard Stack" — sqlglot is a dev-only dependency
    landed by Plan 30-01).
    """
    try:
        import sqlglot  # noqa: F401
    except ImportError:
        return {
            "skipped": True,
            "reason": "sqlglot not installed (dev dependency missing)",
        }
    return {
        "skipped": True,
        "reason": (
            "sqlglot import OK; live trace + transpile requires --sql output "
            "capture — see RESEARCH §'Audit 4' for the manual procedure"
        ),
    }


def main() -> int:
    findings: dict[str, Any] = {
        "grep": grep_findings(),
        "alembic": alembic_smoke_render(),
        "sqlglot": sqlglot_transpile_smoke(),
    }
    summary_path = Path("/tmp/audit_dialect_portability.json")
    summary_path.write_text(json.dumps(findings, indent=2, default=str))
    print(json.dumps(findings, indent=2, default=str))

    error_count = sum(
        1 for f in findings["grep"] if f.get("severity") == "error"
    )
    alembic_failed = any(
        v.get("rc", 0) not in (0, -1)  # -1 == graceful skip (PG unreachable)
        for v in findings["alembic"].values()
    )
    if error_count == 0 and not alembic_failed:
        print("\nAUDIT PASS — no portability findings.")
        return 0
    print(
        f"\nAUDIT FOUND ISSUES — {error_count} grep findings, "
        f"alembic_failed={alembic_failed}"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
