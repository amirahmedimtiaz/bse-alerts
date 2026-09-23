from unittest.mock import patch

import pytest

from src import main


def test_scan_uses_transactional_pipeline():
    with patch("src.scanner.run_local", return_value=3) as run:
        assert main.run() == 3
        run.assert_called_once_with(send_alerts=True)


def test_local_scan_reports_failure_and_preserves_migration(tmp_path, monkeypatch):
    from src import scanner
    from src.alert_store import AlertStore
    db = tmp_path / "state.sqlite3"
    monkeypatch.setenv("ALERT_DB_PATH", str(db))
    monkeypatch.setattr(scanner, "load_state", lambda: {"2": {"old-id"}})
    monkeypatch.setattr(scanner, "cycle", lambda *args: {"failed": ["2"], "email_failed": False})
    with pytest.raises(RuntimeError, match="incomplete"):
        main.run()
    store = AlertStore(db)
    assert store.company("2") is not None
    assert store.export()["announcements"][0]["news_id"] == "old-id"
    store.close()
