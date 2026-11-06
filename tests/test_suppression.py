from engine import suppression


def test_email_and_domain_block(conn):
    suppression.add(conn, "email", "Client@Happy.test", "existing client")
    suppression.add(conn, "domain", "https://www.rival.test", "competitor")
    assert "existing client" in suppression.check(conn, "client@happy.test")
    assert "competitor" in suppression.check(conn, "anyone@rival.test")
    assert suppression.check(conn, "ok@fine.test") is None


def test_domain_covers_subdomains_and_company_domain(conn):
    suppression.add(conn, "domain", "rival.test", "competitor")
    assert suppression.check(conn, "a@mail.rival.test")
    # personal email but the company domain is suppressed
    assert suppression.check(conn, "a@gmail.test", company_domain="rival.test")
    # a lookalike is not a subdomain
    assert suppression.check(conn, "a@notrival.test") is None


def test_add_is_idempotent(conn):
    assert suppression.add(conn, "domain", "x.test", "r") is True
    assert suppression.add(conn, "domain", "X.test", "r") is False


def test_load_csv(conn, tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("type,value,reason\ndomain,a.test,own\nemail,b@c.test,unsub\nbogus,x,y\n")
    assert suppression.load_csv(conn, p) == 2
