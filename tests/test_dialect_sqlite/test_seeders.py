"""scripts/seed package runs cleanly against SQLite.

Note: the plan was authored against legacy ``scripts/seed.py`` /
``scripts/seed_sample.py`` flat scripts. The current code root organises seed
generation as the ``scripts.seed`` package with ``scripts/seed/main.py`` as
the entry point and a single ``--all`` flag (FK-respecting RUN_ORDER baked
in). This test exercises that real entry point — Rule 3 deviation
(blocking issue: referenced files don't exist).
"""
import os
import subprocess
import sys

import pytest


@pytest.mark.db
def test_seed_main_dry_run_runs_clean():
    """`python -m scripts.seed.main --all --dry-run` exits 0 against SQLite.

    --dry-run rolls back the txn so the per-test-session DB state is not
    affected. We only assert the seed pipeline can plan + execute end-to-end
    against the SQLite dialect; the squashed migration is required to have
    landed first (Plan 30-03).
    """
    env = {**os.environ, "DATABASE_URL": os.environ["DATABASE_URL"]}
    cp = subprocess.run(
        [sys.executable, "-m", "scripts.seed.main", "--all", "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    # seed pipeline writes a contracts-gap report to a relative path under
    # .planning/spikes/; that file IO is best-effort and may warn on a clean
    # sandbox. Accept rc=0; surface anything else with full output for triage.
    assert cp.returncode == 0, (
        f"scripts.seed.main --all --dry-run failed with rc={cp.returncode}\n"
        f"STDOUT:\n{cp.stdout}\n"
        f"STDERR:\n{cp.stderr}"
    )
