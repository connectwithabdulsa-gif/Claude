import json

from engine import suppression
from engine.ingest.importer import run_import


def row(email, domain="acme.test", first="Jake", **extra):
    r = {"First Name": first, "Last Name": "Reed", "Job Title": "Founder", "Work Email": email,
         "Company Name": "Acme", "Company Domain": domain, "Employee Count": "11-50",
         "Country": "United States"}
    r.update(extra)
    return r


def test_basic_import_tracks_source(conn, write_csv, source):
    p = write_csv([row("jake@acme.test", "https://www.acme.test/")], name="clay_a.csv")
    r = run_import(conn, source(p))
    assert (r.rows_read, r.leads_added, r.companies_added) == (1, 1, 1)
    lead = conn.execute("SELECT * FROM leads").fetchone()
    assert lead["source"] == "clay" and lead["source_file"] == "clay_a.csv"
    assert lead["imported_at"] and lead["import_id"] == r.import_id
    co = conn.execute("SELECT * FROM companies").fetchone()
    assert co["domain"] == "acme.test" and co["headcount"] == 11


def test_dedupe_in_file_by_email(conn, write_csv, source):
    p = write_csv([row("jake@acme.test"), row("  JAKE@acme.test ")])
    r = run_import(conn, source(p))
    assert r.leads_added == 1 and r.rejected["duplicate in file"] == 1


def test_dedupe_across_imports_and_company_by_domain(conn, write_csv, source):
    run_import(conn, source(write_csv([row("jake@acme.test")], name="1.csv")))
    r = run_import(conn, source(write_csv(
        [row("jake@acme.test"), row("priya@acme.test", "acme.test", "Priya")], name="2.csv")))
    assert r.rejected["already in database"] == 1
    assert r.leads_added == 1 and r.companies_added == 0
    assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 2


def test_suppression_on_import(conn, write_csv, source):
    suppression.add(conn, "domain", "rival.test", "competitor")
    suppression.add(conn, "email", "client@happy.test", "existing client")
    p = write_csv([row("kai@rival.test", "rival.test"), row("client@happy.test", "happy.test"),
                   row("ok@fine.test", "fine.test")])
    r = run_import(conn, source(p))
    assert r.leads_added == 1 and r.rejected["suppressed"] == 2
    reasons = [e["reason"] for e in conn.execute(
        "SELECT reason FROM events WHERE action='suppressed_on_import'")]
    assert any("competitor" in x for x in reasons) and any("existing client" in x for x in reasons)


def test_missing_email_rejected_and_logged(conn, write_csv, source):
    r = run_import(conn, source(write_csv([row("")])))
    assert r.leads_added == 0 and r.rejected["missing email"] == 1
    assert conn.execute("SELECT COUNT(*) FROM events WHERE action='import_rejected'").fetchone()[0] == 1


def test_company_domain_falls_back_to_email(conn, write_csv, source):
    run_import(conn, source(write_csv([row("jake@beta.test", domain="")])))
    assert conn.execute("SELECT domain FROM companies").fetchone()[0] == "beta.test"


def test_unmapped_columns_kept(conn, write_csv, source):
    r = run_import(conn, source(write_csv([row("jake@acme.test", **{"Job Openings": "SDR"})])))
    assert "Job Openings" in r.unmapped_columns
    raw = json.loads(conn.execute("SELECT raw_json FROM leads").fetchone()[0])
    assert raw["Job Openings"] == "SDR"


def test_full_name_split(conn, write_csv, source):
    p = write_csv([{"Full Name": "Sofia Lopez Diaz", "Email": "sofia@x.test"}])
    run_import(conn, source(p))
    lead = conn.execute("SELECT first_name, last_name FROM leads").fetchone()
    assert (lead[0], lead[1]) == ("Sofia", "Lopez Diaz")


def test_company_fill_blanks_never_overwrites(conn, write_csv, source):
    run_import(conn, source(write_csv([row("a@acme.test", **{"Industry": "Software"})], name="1.csv")))
    run_import(conn, source(write_csv([row("b@acme.test", first="B", **{"Industry": "Other"})], name="2.csv")))
    assert conn.execute("SELECT industry FROM companies").fetchone()[0] == "Software"
