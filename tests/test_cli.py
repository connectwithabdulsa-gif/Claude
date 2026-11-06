from typer.testing import CliRunner

from engine.cli import app


def test_help_and_stats(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_DB", str(tmp_path / "t.db"))
    r = CliRunner().invoke(app, ["stats"])
    assert r.exit_code == 0, r.output
    assert "leads" in r.output
