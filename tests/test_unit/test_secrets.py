"""Secret-at-rest: the box, key resolution, and the config-row boundary."""

from __future__ import annotations

import stat

import pytest

from config import settings
from web.services import secrets as secrets_mod
from web.services.secrets import (
    PREFIX,
    SecretBox,
    SecretUnreadable,
    is_encrypted,
    load_key_material,
)
from web.services.sources.config_secrets import (
    needs_sealing,
    open_config,
    seal_config,
)

pytestmark = pytest.mark.unit


def test_round_trip_and_prefix():
    box = SecretBox("k")
    sealed = box.encrypt("hunter2")
    assert sealed.startswith(PREFIX)
    assert "hunter2" not in sealed
    assert box.decrypt(sealed) == "hunter2"


def test_encrypt_is_idempotent_and_plaintext_passes_through():
    box = SecretBox("k")
    sealed = box.encrypt("x")
    assert box.encrypt(sealed) == sealed
    assert box.decrypt("plain") == "plain"


def test_wrong_key_raises_unreadable():
    sealed = SecretBox("a").encrypt("x")
    with pytest.raises(SecretUnreadable):
        SecretBox("b").decrypt(sealed)


def test_key_file_created_private_and_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "secret_key", None)
    path = tmp_path / "sub" / "secret.key"
    first = load_key_material(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_key_material(path) == first


def test_env_key_wins_over_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "from-env")
    path = tmp_path / "secret.key"
    assert load_key_material(path) == "from-env"
    assert not path.exists()


def test_seal_encrypts_only_declared_fields():
    sealed = seal_config("ha_fordpass", {"ha_url": "http://x", "ha_token": "t0k"})
    assert sealed["ha_url"] == "http://x"
    assert is_encrypted(sealed["ha_token"])
    assert open_config("ha_fordpass", sealed).values["ha_token"] == "t0k"


def test_seal_leaves_empty_and_undeclared_sources_alone():
    assert seal_config("ha_fordpass", {"ha_token": ""})["ha_token"] == ""
    assert seal_config("ha_gas_price", {"x": "y"}) == {"x": "y"}


def test_open_names_unreadable_fields_and_drops_them():
    foreign = SecretBox("other").encrypt("t")
    opened = open_config("ha_fordpass", {"ha_url": "u", "ha_token": foreign})
    assert opened.unreadable == frozenset({"ha_token"})
    assert "ha_token" not in opened.values
    assert opened.values["ha_url"] == "u"


def test_needs_sealing():
    assert needs_sealing("ha_fordpass", {"ha_token": "plain"})
    assert not needs_sealing("ha_fordpass", seal_config("ha_fordpass", {"ha_token": "p"}))
    assert not needs_sealing("ha_fordpass", {})


@pytest.fixture
def fresh_box():
    secrets_mod.reset_secret_box()
    yield
    secrets_mod.reset_secret_box()


def test_secret_box_is_cached(fresh_box):
    assert secrets_mod.secret_box() is secrets_mod.secret_box()
