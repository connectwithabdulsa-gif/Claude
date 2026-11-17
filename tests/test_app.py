"""DFY Sender app: every page renders, csv upload writes emails, Start/Stop, password stays out of html."""
import io
import re
import shutil

import pytest

from engine.paths import CONFIG_DIR
from engine.send import runner
from engine.send import spacemail as sm

from test_volume import HEAD


def _csv_bytes():
    rows = [HEAD,
            ["Graphic Designer", "https://jobs.test/1", "Acme Studio", "Austin, TX", "acme.test", "Acme Studio",
             "2-10 employees", "5", "", "Advertising Services", "Jane", "Doe", "Founder", "", "jane@acme.test"],
            ["Barista", "https://jobs.test/2", "Bean Cafe", "Austin, TX", "bean.test", "Bean Cafe", "2-10 employees",
             "3", "", "Food & Beverages", "Al", "Bee", "Owner", "", "al@bean.test"]]
    import csv
    s = io.StringIO()
    csv.writer(s).writerows(rows)
    return s.getvalue().encode()


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, cfg_dir)
    monkeypatch.setenv("ENGINE_DB", str(tmp_path / "app.db"))
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr("engine.app.server.CONFIG_DIR", cfg_dir)
    from engine import pipeline
    from engine.verify.local import LocalVerifier
    monkeypatch.setattr(pipeline, "LocalVerifier", lambda offline=False: LocalVerifier(offline=True))  # no DNS here
    from engine.app.server import create_app
    app = create_app(config_dir=cfg_dir, root=tmp_path, worker=None)
    app.config["TESTING"] = True
    c = app.test_client()
    c.get("/")
    c.cfg_dir, c.root = cfg_dir, tmp_path
    return c


def csrf(c):
    with c.session_transaction() as s:
        return s["csrf"]


def test_pages_render(client):
    for path in ["/", "/leads", "/emails", "/inbox", "/settings", "/emails?tab=held"]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert b"DFY Sender" in r.data


def test_post_without_csrf_refused(client):
    assert client.post("/stop", data={}).status_code == 400


def test_upload_imports_and_writes(client):
    r = client.post("/leads", data={"csrf": csrf(client), "source": "jobs", "only_agencies": "on",
                                    "csv": (io.BytesIO(_csv_bytes()), "jobs.csv")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    assert b"1 new leads" in r.data and b"not an agency" in r.data
    assert b"Acme Studio" in r.data
    page = client.get("/emails?tab=approved").data + client.get("/emails?tab=held").data
    assert b"Hi Jane," in page


def test_settings_saves_password_privately(client, monkeypatch):
    monkeypatch.delenv(sm.env_key("abdul@dfygtmsystems.com"), raising=False)
    r = client.post("/settings", data={"csrf": csrf(client), "act": "save", "address": "abdul@dfygtmsystems.com",
                                       "password": "Sup3rSecret!", "cap": "300",
                                       "physical_address": "1 Test St, Karachi", "alerts_to": "me@x.test"},
                    follow_redirects=True)
    assert b"Sup3rSecret" not in r.data and b"saved" in r.data
    env = (client.root / ".env").read_text()
    assert "SPACEMAIL_PASSWORD_ABDUL_DFYGTMSYSTEMS_COM=Sup3rSecret!" in env
    sender = (client.cfg_dir / "sender.yaml").read_text()
    assert 'physical_address: "1 Test St, Karachi"' in sender and "me@x.test" in sender
    assert "# never send cold email" in sender            # comments kept


def test_start_blocked_until_ready_then_stop(client, monkeypatch):
    r = client.post("/start", data={"csrf": csrf(client)}, follow_redirects=True)
    assert b"Can&#39;t start yet" in r.data or b"Can't start yet" in r.data
    assert not (client.root / "data" / "live_ok").exists()
    # make it ready: mailbox + password + fresh dns + address
    client.post("/settings", data={"csrf": csrf(client), "act": "save", "address": "abdul@dfygtmsystems.com",
                                   "password": "pw", "cap": "300", "physical_address": "1 Test St, Karachi"})
    from engine.db import connect
    from engine.paths import db_path
    from engine.send import dns_check
    conn = connect(db_path())
    dns_check.save(conn, dns_check.DomainReport("dfygtmsystems.com", {k: ("ok", "") for k in
                                                                      ("mx", "spf", "dkim", "dmarc", "blocklists")}))
    conn.commit()
    conn.close()
    r = client.post("/start", data={"csrf": csrf(client)}, follow_redirects=True)
    assert b"Sending is ON" in r.data
    assert (client.root / "data" / "live_ok").exists() and not (client.root / "data" / "STOP").exists()
    r = client.post("/stop", data={"csrf": csrf(client)}, follow_redirects=True)
    assert b"Sending is OFF" in r.data and (client.root / "data" / "STOP").exists()


def test_refresh_dns_once_a_day(tmp_path, monkeypatch):
    from engine.app.worker import refresh_dns
    from engine.db import connect
    conn = connect(tmp_path / "w.db")
    conn.execute("INSERT INTO domains (domain) VALUES ('a.test')")
    conn.execute("INSERT INTO mailboxes (address, domain_id) VALUES ('me@a.test', 1)")
    calls = []

    def lookup(name, rtype):
        calls.append(name)
        return None
    assert refresh_dns(conn, lookup=lookup) == 1
    n = len(calls)
    assert refresh_dns(conn, lookup=lookup) == 0 and len(calls) == n


def test_start_scripts_quote_paths():
    bat = (CONFIG_DIR.parent / "START.bat").read_text()
    assert 'cd /d "%~dp0"' in bat and '".venv\\Scripts\\engine.exe" app' in bat
    assert re.search(r"pip install .* -e \.", bat)
