from engine.normalize import norm_domain, norm_email, norm_key, parse_int


def test_domain_from_url_and_email():
    assert norm_domain("https://www.Acme.com/about?x=1") == "acme.com"
    assert norm_domain("acme.com:443") == "acme.com"
    assert norm_domain("jake@Mail.Acme.com") == "mail.acme.com"
    assert norm_domain("") == ""


def test_email_and_header_keys():
    assert norm_email("  <Jake@Acme.COM> ") == "jake@acme.com"
    assert norm_key("Work_Email") == norm_key("work email") == "workemail"


def test_parse_int_ranges():
    assert parse_int("11-50") == 11
    assert parse_int("1,200 employees") == 1200
    assert parse_int("") is None
