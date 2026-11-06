from engine.verify.local import LocalVerifier
from engine.verify.runner import verify_leads

V = LocalVerifier(mx_lookup=lambda d: True)


def test_valid_source_and_checks_ok():
    r = V.verify("jake@acme.test", "acme.test", "valid")
    assert r.status == "ok", r.reasons


def test_hard_fails():
    assert V.verify("not-an-email", "acme.test", "valid").status == "fail"
    assert "role mailbox" in V.verify("info@acme.test", "acme.test", "valid").reason
    assert "personal address" in V.verify("jake@gmail.com", "acme.test", "valid").reason
    assert "does not match" in V.verify("jake@other.test", "acme.test", "valid").reason
    assert V.verify("jake@acme.test", "acme.test", "invalid").status == "fail"


def test_subdomain_matches_company():
    assert V.verify("jake@uk.acme.test", "acme.test", "valid").status == "ok"


def test_no_mx_fails_and_unknown_mx_never_ok_without_source():
    assert V.verify("jake@acme.test", "acme.test", "valid").status == "ok"
    no_mx = LocalVerifier(mx_lookup=lambda d: False)
    assert "no mail server" in no_mx.verify("jake@acme.test", "acme.test", "valid").reason
    offline = LocalVerifier(offline=True)
    r = offline.verify("jake@acme.test", "acme.test", "")
    assert r.status == "risky" and "mail server not checked" in r.reason
    # source valid still counts as ok when we could not check mx ourselves
    assert offline.verify("jake@acme.test", "acme.test", "valid").status == "ok"


def test_catch_all_and_missing_status_are_risky():
    assert V.verify("jake@acme.test", "acme.test", "catch-all").status == "risky"
    assert V.verify("jake@acme.test", "acme.test", None).status == "risky"


def test_runner_stores_status_and_provenance(conn):
    conn.execute("INSERT INTO companies (domain, source, first_seen_at, updated_at) VALUES ('acme.test','t','x','x')")
    conn.execute("INSERT INTO leads (company_id, email, email_domain, email_status_source, source, imported_at, updated_at)"
                 " VALUES (1, 'jake@acme.test', 'acme.test', 'valid', 't', 'x', 'x')")
    assert verify_leads(conn, V)["ok"] == 1
    lead = conn.execute("SELECT verify_status, verify_reason FROM leads").fetchone()
    assert lead[0] == "ok"
    fv = conn.execute("SELECT source FROM field_values WHERE field='verify_status'").fetchone()
    assert fv[0] == "local_verifier"
    assert verify_leads(conn, V) == {"ok": 0, "risky": 0, "fail": 0}   # only unverified by default
