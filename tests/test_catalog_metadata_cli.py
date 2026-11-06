"""The metadata CLI can show usage without database or bot setup."""

import pytest

from telegram_ecommerce.database.catalog_metadata import main


def test_help_exits_before_loading_database(monkeypatch, capsys):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('DB_USER', raising=False)
    monkeypatch.delenv('BOT_TOKEN', raising=False)
    with pytest.raises(SystemExit) as error:
        main(['--help'])
    assert error.value.code == 0
    assert 'Omitted fields stay unchanged' in capsys.readouterr().out


def test_no_metadata_flags_exits_before_loading_database(capsys):
    with pytest.raises(SystemExit) as error:
        main(['1'])
    assert error.value.code == 2
    assert 'Supply at least one' in capsys.readouterr().err


def test_unknown_flags_are_rejected(capsys):
    with pytest.raises(SystemExit) as error:
        main(['1', '--brnad', 'Acme'])
    assert error.value.code == 2
    assert 'unrecognized arguments' in capsys.readouterr().err
