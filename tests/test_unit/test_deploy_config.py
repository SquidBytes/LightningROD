"""Guards on compose/env deploy files that code-level tests can't see."""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_FILES = (
    _ROOT / "docker-compose.yml",
    _ROOT / "docker" / "docker-compose.standalone.yml",
)


@pytest.mark.parametrize("compose_path", _COMPOSE_FILES, ids=lambda p: p.name)
def test_compose_never_overrides_baked_version(compose_path):
    """A LIGHTNINGROD_VERSION entry in `environment:` clobbers the version
    baked into the image ENV at build time, making Settings report the
    compose default (e.g. "latest") instead of the running release."""
    config = yaml.safe_load(compose_path.read_text())
    for name, service in (config.get("services") or {}).items():
        env = service.get("environment") or []
        entries = env if isinstance(env, list) else [f"{k}={v}" for k, v in env.items()]
        offenders = [e for e in entries if str(e).startswith("LIGHTNINGROD_VERSION")]
        assert not offenders, (
            f"{compose_path.name} service {name!r} re-declares "
            f"LIGHTNINGROD_VERSION in environment: {offenders}"
        )


def test_env_example_does_not_set_version():
    """An active LIGHTNINGROD_VERSION in .env.example leaks into containers
    via env_file (wrong displayed version) and points compose at a
    nonexistent image tag for grab-and-go users. Keep it commented out."""
    active = [
        line
        for line in (_ROOT / ".env.example").read_text().splitlines()
        if line.strip().startswith("LIGHTNINGROD_VERSION")
    ]
    assert not active, f".env.example sets LIGHTNINGROD_VERSION: {active}"
