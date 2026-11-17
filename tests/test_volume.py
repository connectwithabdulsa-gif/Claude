"""300 a day from one mature mailbox, jobs csv import, template writer."""
import csv
import random
from datetime import date, datetime, timedelta, timezone

from engine.ingest.importer import run_import
from engine.ingest.jobs_csv import CachedJobsSource, JobsCsvSource, add_job_signals, clean_role
from engine.paths import CONFIG_DIR
from engine.personalize import template_writer as tw, writer as w
from engine.score.scorer import Config as ScoreConfig, score_all
from engine.send import runner
from engine.send.base import SimulatorSender
from engine.sequence import scheduler as sch
from engine.signals import engine as sig_engine

from test_sequence import seed, TUE

CFG = sch.load_cfg(CONFIG_DIR)


def test_fixed_cap_skips_ramp_and_pause_still_wins():
    ramp = CFG["sending"]["ramp"]
    mb = {"paused": 0, "warmup_started_at": None, "live_from": "2026-11-17", "daily_cap": 300, "fixed_cap": 300}
    assert sch.mailbox_cap(mb, date(2026, 11, 17), ramp) == 300
    assert sch.mailbox_cap({**mb, "paused": 1}, date(2026, 11, 17), ramp) == 0
    assert sch.mailbox_cap({**mb, "fixed_cap": None}, date(2026, 11, 17), ramp) == 10


def test_300_in_one_day_fit_the_window_with_spacing(conn, tmp_path):
    seed(conn, n=320, mailboxes=1)
    conn.execute("UPDATE mailboxes SET fixed_cap=300, daily_cap=300, live_from='2026-11-01'")
    conn.commit()
    p = sch.plan_and_send(conn, CFG, TUE, SimulatorSender(tmp_path / "o.jsonl"), random.Random(3))
    assert sum(p.sent.values()) == 300
    times = sorted(datetime.fromisoformat(r[0]) for r in conn.execute("SELECT sent_at FROM sends"))
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    assert min(gaps) >= 44.9
    assert (times[-1] - times[0]) <= timedelta(hours=8)


def test_runner_auto_per_tick():
    cfg = {"sending": {"live": {"max_per_tick_per_mailbox": "auto", "tick_minutes": 5},
                       "window": {"start_hour": 9, "end_hour": 17}}}
    assert runner.per_tick_limit(cfg, {"fixed_cap": 300}) == 6       # 300 / 96 ticks, +2 slack
    cfg["sending"]["live"]["max_per_tick_per_mailbox"] = 1
    assert runner.per_tick_limit(cfg, {"fixed_cap": 300}) == 1


HEAD = ["Job Role", "Job Post", "Company Name", "Location", "Domain", "Enrich Company", "Size", "Employee Count",
        "Url", "Industry", "First Name ", "Last Name", "Job Title", "Linkedin Url", "Work Email"]


def _csv(path):
    rows = [
        ["Graphic Designer (Part-Time)", "https://jobs.test/1", "Acme Studio", "Austin, TX", "acme.test", "Acme Studio",
         "2-10 employees", "5", "", "Advertising Services", "Jane", "Doe", "Founder", "https://linkedin.test/jane",
         "jane@acme.test"],
        ["Graphic Designer", "https://jobs.test/1", "Acme Studio", "Austin, TX", "acme.test", "Acme Studio",
         "2-10 employees", "5", "", "Advertising Services", "Bob", "Roe", "Office Manager", "", "bob@acme.test"],
        ["Barista", "https://jobs.test/2", "Bean Cafe", "Austin, TX", "bean.test", "Bean Cafe", "2-10 employees", "3",
         "", "Food & Beverages", "Al", "Bee", "Owner", "", "al@bean.test"],
    ]
    with open(path, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(HEAD)
        wr.writerows(rows)


def test_jobs_import_filters_and_signals(conn, tmp_path):
    p = tmp_path / "jobs.csv"
    _csv(p)
    src = JobsCsvSource(p, CONFIG_DIR / "jobs_columns.yaml")
    r = run_import(conn, CachedJobsSource(src))
    assert r.leads_added == 1 and src.skipped["not an agency (industry)"] == 1
    assert src.skipped["another person at the same company"] == 1
    assert add_job_signals(conn, r, src) == 1
    s = conn.execute("SELECT * FROM signals").fetchone()
    assert s["type"] == "hiring_delivery_role" and s["value"] == "Graphic Designer" and "jobs.test/1" in s["source"]
    assert clean_role("Video Editor ? South OC") == "Video Editor"


def test_template_writer_end_to_end(conn, tmp_path):
    p = tmp_path / "jobs.csv"
    _csv(p)
    src = JobsCsvSource(p, CONFIG_DIR / "jobs_columns.yaml")
    r = run_import(conn, CachedJobsSource(src))
    add_job_signals(conn, r, src)
    conn.execute("UPDATE leads SET verify_status='ok'")
    scfg = ScoreConfig.load(CONFIG_DIR)
    today = datetime.now(timezone.utc)
    sig_engine.classify_and_expire(conn, scfg, today)
    score_all(conn, scfg)
    sig_engine.pick_all(conn, scfg, today)
    cfg = w.load_cfg(CONFIG_DIR)
    leads = w.eligible_leads(conn, ["A", "B", "C"], 10)
    assert len(leads) == 1
    out = tw.write_batch(conn, cfg, CONFIG_DIR, leads, today, auto_approve=True)
    assert out["ready"] == 1
    m = conn.execute("SELECT * FROM messages").fetchone()
    assert m["review"] == "approved" and m["writer_mode"] == "template"
    assert "Hi Jane," in m["body"] and "Acme Studio" in m["body"] and "graphic designer" in m["body"].lower()
    assert "{" not in m["body"] and "Not for you? Just say so and I'll stop." in m["body"]


def test_template_fill_never_guesses():
    assert tw.fill("Hi {first_name}", {"first_name": None}) is None
    assert tw.fill("Hi {first_name}", {"first_name": "Jane"}) == "Hi Jane"
