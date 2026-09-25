"""Secret-at-rest: the box, key resolution, and the config-row boundary."""

from __future__ import annotations

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


def test_no_key_configured_means_no_key_and_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "secret_key", None)
    monkeypatch.setattr(settings, "secret_key_file", None)
    assert load_key_material() is None
    assert list(tmp_path.rglob("*")) == []


def test_key_file_is_read_never_created(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "secret_key", None)
    path = tmp_path / "secret"
    monkeypatch.setattr(settings, "secret_key_file", path)
    assert load_key_material() is None
    assert not path.exists()
    path.write_text("from-file\n")
    assert load_key_material() == "from-file"


def test_env_key_wins_over_file(tmp_path, monkeypatch):
    path = tmp_path / "secret"
    path.write_text("from-file")
    monkeypatch.setattr(settings, "secret_key", "from-env")
    monkeypatch.setattr(settings, "secret_key_file", path)
    assert load_key_material() == "from-env"


def test_without_key_secrets_stay_plain(no_secret_key):
    config = {"ha_url": "u", "ha_token": "t0k"}
    assert seal_config("ha_fordpass", config) == config
    assert not needs_sealing("ha_fordpass", config)
    assert open_config("ha_fordpass", config).values == config


def test_without_key_ciphertext_is_unreadable(no_secret_key):
    sealed = SecretBox("test-secret-key").encrypt("t")
    opened = open_config("ha_fordpass", {"ha_token": sealed})
    assert opened.unreadable == frozenset({"ha_token"})


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
