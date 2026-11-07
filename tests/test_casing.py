from engine.casing import sentence_case as s
from engine.personalize import qa
from engine.personalize import writer as w
from engine.paths import CONFIG_DIR


def test_sentence_starts_and_i():
    assert s("hey Jake, saw Acme is hiring. i'll send it? not for you? just say so and i'll stop.") == \
        "Hey Jake, saw Acme is hiring. I'll send it? Not for you? Just say so and I'll stop."


def test_lines_names_and_links_kept():
    out = s("abdul\ndfy gtm systems\nhttps://calendly.com/x\nabdul@dfygtmsystems.com")
    assert out == "Abdul\nDFY GTM Systems\nhttps://calendly.com/x\nabdul@dfygtmsystems.com"


def test_no_change_inside_words():
    assert s("hi, iphone and wifi") == "Hi, iphone and wifi"


def test_qa_allows_sentence_case():
    rules = w.load_cfg(CONFIG_DIR)["rules"]
    ctx = {"first_name": "Jake", "company": {"name": "Acme"}, "facts": []}
    body = "Hey Jake. Saw Acme is hiring. I build outbound. Want the map?"
    fails = [f for f in qa.check({"subject": "Acme outbound", "body": body, "facts_used": []}, ctx, rules, False)
             if "capitalized" in f]
    assert fails == []
