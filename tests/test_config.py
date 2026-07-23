import os

import pytest

from arbfinder import config


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "creds.json")
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)


def test_save_and_load_round_trip():
    config.save_credentials("APPID", "SECRET")
    creds = config.load_credentials()
    assert creds == {"ebay_client_id": "APPID", "ebay_client_secret": "SECRET"}
    assert config.has_saved_secret()


def test_env_vars_override_saved_file(monkeypatch):
    config.save_credentials("FILE_ID", "FILE_SECRET")
    monkeypatch.setenv("EBAY_CLIENT_ID", "ENV_ID")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "ENV_SECRET")
    creds = config.load_credentials()
    assert creds["ebay_client_id"] == "ENV_ID"
    assert creds["ebay_client_secret"] == "ENV_SECRET"


def test_clear_credentials():
    config.save_credentials("APPID", "SECRET")
    assert config.clear_credentials() is True
    assert not config.has_saved_secret()
    assert config.load_credentials() == {"ebay_client_id": "", "ebay_client_secret": ""}
    assert config.clear_credentials() is False  # nothing left to remove


def test_missing_file_is_empty():
    assert config.load_credentials() == {"ebay_client_id": "", "ebay_client_secret": ""}
    assert not config.has_saved_secret()


def test_corrupt_file_is_tolerated():
    config.CONFIG_PATH.write_text("not json{{", encoding="utf-8")
    assert config.load_credentials() == {"ebay_client_id": "", "ebay_client_secret": ""}
