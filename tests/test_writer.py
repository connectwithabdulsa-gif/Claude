import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from engine.paths import CONFIG_DIR, ROOT
from engine.personalize import prompts, qa
from engine.personalize import writer as w
from engine.personalize.llm import AnthropicLLM, FakeLLM

TODAY = datetime(2026, 11, 6, tzinfo=timezone.utc)
CFG = w.load_cfg(CONFIG_DIR)
GOOD = ("hey Jake,\n\nsaw Acme is hiring an SDR. a new hire takes a while to ramp before meetings show up.\n\n"
        "i'm abdul. i build outbound systems in clay. it finds the right people, checks the data, writes to them "
        "and sends it, so you only take the calls.\n\nwant me to send a one page map of how it would run for Acme?")
CTX = {"first_name": "Jake", "company": {"name": "Acme"},
       "signal": {"class": "hiring_sales", "saw_line": "saw Acme is hiring an SDR.", "key_terms": ["SDR"]},
       "facts": [{"id": "first_name", "text": "Jake"}, {"id": "company", "text": "Acme"},
                 {"id": "signal", "text": "saw Acme is hiring an SDR."}]}


def email(body=GOOD, subject="acme outbound", used=("company", "signal")):
    return {"subject": subject, "body": body, "facts_used": list(used)}


def test_good_email_passes_rules():
    assert qa.check(email(), CTX, CFG["rules"], True) == []


@pytest.mark.parametrize("mutate,expect", [
    (lambda b: b.replace("i build", "i leverage clay to build"), "banned phrase 'leverage'"),
    (lambda b: b.replace("show up.", "show up!"), "exclamation"),
    (lambda b: b.replace("ramp before", "ramp — before"), "em or en dash"),
    (lambda b: b + " see acme.com", "link"),
    (lambda b: b.replace("calls.", "calls. sound good?"), "question marks"),
    (lambda b: b.replace("i build", "i Build"), "capitalized word 'Build'"),
    (lambda b: b.replace("so you only take the calls", "so you can scale your pipeline"), "banned pattern"),
    (lambda b: b.replace("Acme", "your company"), "does not mention Acme"),
    (lambda b: b.replace(" an SDR", " someone"), "signal detail missing"),
    (lambda b: "hey Jake,\n\nsaw Acme is hiring an SDR. want the map?", "words"),
    (lambda b: b + "\n\nabdul", "sign off"),
])
def test_rule_fails(mutate, expect):
    fails = qa.check(email(mutate(GOOD)), CTX, CFG["rules"], True)
    assert any(expect in f for f in fails), fails


def test_subject_rules():
    assert any("re:" in f for f in qa.check(email(subject="re: acme outbound"), CTX, CFG["rules"], True))
    assert any("subject has 1 words" in f for f in qa.check(email(subject="hi"), CTX, CFG["rules"], True))
    assert any("quick question" in f for f in qa.check(email(subject="quick question acme"), CTX, CFG["rules"], True))


def test_facts_must_trace():
    fails = qa.check(email(used=("company", "signal", "revenue")), CTX, CFG["rules"], True)
    assert any("not given" in f for f in fails)
    assert any("signal fact not marked" in f for f in qa.check(email(used=("company",)), CTX, CFG["rules"], True))


def test_possessive_company_ok():
    b = GOOD.replace("saw Acme is hiring", "saw Acme's team is hiring")
    assert qa.check(email(b), CTX, CFG["rules"], True) == []


def test_system_prompt_pulls_rules_and_library():
    s = prompts.system_prompt(ROOT)
    assert "Banned words and phrases" in s and "Anatomy of email 1" in s and "amazon agency" in s
    assert "—" not in prompts.qa_system_prompt(ROOT)


def _seed(conn, n=3, niche="amazon_agency"):
    conn.execute("INSERT INTO companies (domain, name, source, first_seen_at, updated_at) VALUES ('acme.test','Acme Ltd','t','x','x')")
    for i in range(n):
        conn.execute("INSERT INTO leads (company_id, email, email_domain, first_name, title, tier, score, niche,"
                     " verify_status, source, imported_at, updated_at) VALUES (1,?,'acme.test','Jake','Founder','A',90,?,"
                     "'ok','t','x','x')", (f"j{i}@acme.test", niche))
        conn.execute("INSERT INTO signals (company_id, type, value, source, source_field, observed_at, detected_at,"
                     " data_json) VALUES (1,'hiring_in_sales_department','v','explorium','/v2/businesses/events:e1',"
                     "'2026-11-01','x','{\"job_titles\": [\"SDR\"]}')")
        conn.execute("INSERT INTO signal_picks (lead_id, signal_id, rank, signal_class, points, age_days, saw_line,"
                     " matters_line, picked_at) VALUES (?,?,1,'hiring_sales',30,5,'saw Acme is hiring an SDR.',"
                     "'a new hire takes a while to ramp before meetings show up.','x')", (i + 1, i + 1))


def _run(conn, writer_fn, qa_fn=None):
    from engine.personalize import fake_writer
    llm = FakeLLM(writer_fn, qa_fn or fake_writer.qa)
    spend = w.Spend()
    outs = [w.write_one(conn, llm, CFG, lead, TODAY, spend, "sys", "sys")
            for lead in w.eligible_leads(conn, ["A", "B", "C"], 10)]
    return outs, llm


def _good_writer(p):
    sig = p["signal"]
    first = "hey Jake,\n\n" + (f"{sig['saw_line']} {sig['matters_line']}" if sig else
                               "if most of Acme's clients come from referrals, outbound can sit next to that and fill the gaps.")
    body = (f"{first}\n\ni'm abdul. i build outbound systems in clay. it finds the right people, checks the data, "
            f"writes to them and sends it, so you only take the calls.\n\nwant me to send a one page map of how it "
            f"would run for Acme?")
    return {"subject": "acme outbound", "body": body, "facts_used": ["company"] + (["signal"] if sig else [])}


def test_context_has_dated_signal_fact_and_proof(conn):
    _seed(conn, 1)
    ctx = w.build_context(conn, dict(conn.execute("SELECT * FROM leads").fetchone()), CFG["niches"], TODAY)
    sig = next(f for f in ctx["facts"] if f["id"] == "signal")
    assert sig["date"] == "2026-11-01" and sig["source"] == "explorium"
    assert any(f["id"] == "proof" for f in ctx["facts"]) and ctx["company"]["name"] == "Acme"
    assert ctx["signal"]["key_terms"] == ["SDR"]


def test_good_path_ready_with_footer_and_rotation(conn):
    _seed(conn, 3)
    outs, llm = _run(conn, _good_writer)
    assert [o.status for o in outs] == ["ready"] * 3 and not any(o.uses_fallback for o in outs)
    assert [o.variant for o in outs] == ["v1", "v2", "v3"]
    assert outs[0].body.endswith("not for you? just say so and i'll stop.") and "[ADD PHYSICAL ADDRESS]" in outs[0].body
    assert llm.calls == ["writer", "qa"] * 3


def test_bad_signal_email_falls_back(conn):
    _seed(conn, 1)
    bad = lambda p: ({**_good_writer(p), "body": _good_writer(p)["body"] + " we leverage it"} if p["signal"]
                     else _good_writer(p))
    (o,), llm = _run(conn, bad)
    assert o.status == "ready" and o.uses_fallback and o.attempts == 2
    assert any("leverage" in r for r in o.reasons)
    assert llm.calls == ["writer", "writer", "qa"]   # no paid qa pass on a rule fail
    m = conn.execute("SELECT signal_class, signal_ids FROM messages").fetchone()
    assert m[0] == "fallback" and json.loads(m[1]) == []


def test_llm_qa_fail_holds(conn):
    _seed(conn, 1)
    qa_fail = lambda p: {"checks": [{"id": 6, "pass": False, "reason": "generic, fits anyone"}], "verdict": "fail"}
    (o,), _ = _run(conn, _good_writer, qa_fail)
    assert o.status == "held" and any("generic" in r for r in o.reasons)


def test_tier_c_always_plain_fallback(conn):
    _seed(conn, 1)
    conn.execute("UPDATE leads SET tier='C'")
    seen = []
    (o,), _ = _run(conn, lambda p: (seen.append(p["signal"]), _good_writer(p))[1])
    assert seen == [None] and o.uses_fallback and o.status == "ready"


def test_dnc_and_unverified_never_written(conn):
    _seed(conn, 2)
    conn.execute("UPDATE leads SET tier='dnc' WHERE id=1")
    conn.execute("UPDATE leads SET verify_status='fail' WHERE id=2")
    assert w.eligible_leads(conn, ["A", "B", "C", "dnc"], 10) == []


def test_price_math():
    assert w.price(CFG["llm"], "claude-opus-5-5", 1_000_000, 1_000_000) == pytest.approx(24.0)
    assert w.price(CFG["llm"], "unknown", 10, 10) == 0.0


def test_estimate_without_key_is_rough_and_worst_case_doubles():
    ctx = {**CTX, "lead_id": 1, "title": "Founder", "niche": {}}
    e = w.estimate(None, CFG, ctx, 10)
    assert e["method"].startswith("rough") and e["batch_worst"] == pytest.approx(e["batch_typical"] * 2, rel=0.01)


class _FakeSDK:
    def __init__(self, stop="end_turn", text='{"subject":"a b","body":"x","facts_used":[]}'):
        self.kwargs = None
        outer = self

        class _Msgs:
            def create(self, **kw):
                outer.kwargs = kw
                return SimpleNamespace(
                    model=kw["model"], stop_reason=stop, stop_details=SimpleNamespace(category="cyber"),
                    content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                    usage=SimpleNamespace(input_tokens=100, output_tokens=50, cache_creation_input_tokens=10,
                                          cache_read_input_tokens=0))
        self.beta = SimpleNamespace(messages=_Msgs())


def test_anthropic_request_shape():
    sdk = _FakeSDK()
    r = AnthropicLLM(CFG["llm"], client=sdk).json_call(role="writer", system="s", user="u", schema=prompts.WRITER_SCHEMA)
    k = sdk.kwargs
    assert k["model"] == "claude-opus-5-5" and k["betas"] == ["server-side-fallback-2026-07-01"]
    assert k["fallbacks"] == "default" and k["cache_control"] == {"type": "ephemeral"}
    assert k["output_config"]["effort"] == "medium" and k["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in k and "thinking" not in k
    assert r.data["subject"] == "a b" and r.input_tokens == 110


def test_anthropic_refusal_returns_no_data():
    r = AnthropicLLM(CFG["llm"], client=_FakeSDK(stop="refusal")).json_call(
        role="qa", system="s", user="u", schema=prompts.QA_SCHEMA)
    assert r.refused and r.data is None and r.refusal_category == "cyber"


def test_cli_dry_run_never_builds_a_client(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from engine.cli import app
    import engine.personalize.llm as llm_mod
    monkeypatch.setenv("ENGINE_DB", str(tmp_path / "t.db"))
    called = []
    monkeypatch.setattr(llm_mod.AnthropicLLM, "__init__", lambda self, *a, **k: called.append(1))
    from engine.db import connect
    conn = connect(tmp_path / "t.db")
    _seed(conn, 1)
    conn.commit()
    real_load = w.load_cfg
    monkeypatch.setattr(w, "load_cfg", lambda d: {**real_load(d), "llm": {**real_load(d)["llm"], "writer_mode": "api"}})
    r = CliRunner().invoke(app, ["write", "--limit", "5"])
    assert r.exit_code == 0, r.output
    assert "ESTIMATE" in r.output and "nothing was sent" in r.output and called == []
    r2 = CliRunner().invoke(app, ["write", "--yes"])
    assert r2.exit_code != 0 and "max-usd" in r2.output and called == []
