"""engine cli. dry run by default. only `find --live` spends credits, and only under a hard cap."""
import json
import os
from pathlib import Path

import typer

from . import suppression
from .db import connect, log_event, now
from .ingest import finder
from .ingest.clay_csv import ClayCsvSource, load_column_map
from .ingest.explorium import BudgetExceeded, CreditBudget, ExploriumClient, HttpTransport
from .ingest.importer import run_import
from .paths import CONFIG_DIR, db_path
from .score.scorer import Config as ScoreConfig, score_all
from .signals import engine as sig_engine
from .verify.local import LocalVerifier
from .verify.runner import verify_leads

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="DFY GTM Systems outbound engine. dry run by default.")
suppress_app = typer.Typer(no_args_is_help=True, help="manage the suppression list.")
app.add_typer(suppress_app, name="suppress")
mailbox_app = typer.Typer(no_args_is_help=True, help="sending mailboxes (secondary domains only).")
app.add_typer(mailbox_app, name="mailboxes")
linkedin_app = typer.Typer(no_args_is_help=True, help="linkedin: manual only.")
app.add_typer(linkedin_app, name="linkedin")
replies_app = typer.Typer(no_args_is_help=True, help="replies: ingest, sort, draft, review. never sends.")
app.add_typer(replies_app, name="replies")
signals_app = typer.Typer(no_args_is_help=True, help="signal engine: refresh, pick, list.")
app.add_typer(signals_app, name="signals")


def _conn():
    conn = connect(db_path())
    suppression.load_csv(conn, CONFIG_DIR / "suppression.csv")
    conn.commit()
    return conn


@app.command("import")
def import_cmd(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, help="csv export"),
    source: str = typer.Option("clay", help="clay, jobs (job-board csv) or prewritten (Email1..Email4 columns)"),
    only_agencies: bool = typer.Option(True, "--only-agencies/--all-industries",
                                       help="jobs: keep marketing, ad, design and media agencies only"),
):
    """import a lead csv. dedupes by email and domain, checks suppression."""
    if source == "prewritten":
        from .pipeline import import_csv
        r = import_csv(_conn(), CONFIG_DIR, path, "prewritten")
        typer.echo(f"read {r['rows']}, added {r['added']} leads with their 4 emails, rejected {r['rejected']}")
        typer.echo("next: engine build-batch (free email check), then the app or engine run --live")
        return
    if source not in ("clay", "jobs"):
        raise typer.BadParameter("--source is clay, jobs or prewritten")
    conn = _conn()
    jobs_src = None
    if source == "jobs":
        from .ingest.jobs_csv import CachedJobsSource, JobsCsvSource, add_job_signals
        jobs_src = JobsCsvSource(path, CONFIG_DIR / "jobs_columns.yaml", only_agencies)
        src = CachedJobsSource(jobs_src)
    else:
        src = ClayCsvSource(path, load_column_map(CONFIG_DIR / "clay_columns.yaml"))
    r = run_import(conn, src)
    if jobs_src is not None:
        n = add_job_signals(conn, r, jobs_src)
        for reason, k in jobs_src.skipped.most_common():
            typer.echo(f"  filtered         {reason:<36} {k}")
        typer.echo(f"  job signals      {n} (each with its job post link and today's date)")
    typer.echo(f"import #{r.import_id} from {path.name}")
    typer.echo(f"  rows read        {r.rows_read}")
    typer.echo(f"  leads added      {r.leads_added}")
    typer.echo(f"  companies added  {r.companies_added}  (existing updated: {r.companies_updated})")
    typer.echo(f"  rejected         {sum(r.rejected.values())}")
    for reason, n in r.rejected.most_common():
        typer.echo(f"    {reason:<20} {n}")
    if r.unmapped_columns:
        typer.echo("  unmapped columns (kept in raw_json): " + ", ".join(r.unmapped_columns))


@app.command()
def stats():
    """counts across the database."""
    conn = _conn()
    one = lambda q, *a: conn.execute(q, a).fetchone()[0]
    typer.echo(f"companies     {one('SELECT COUNT(*) FROM companies')}")
    typer.echo(f"leads         {one('SELECT COUNT(*) FROM leads')}")
    for row in conn.execute("SELECT source, COUNT(*) n FROM leads GROUP BY source"):
        typer.echo(f"  from {row['source']:<8} {row['n']}")
    for row in conn.execute("SELECT status, COUNT(*) n FROM leads GROUP BY status"):
        typer.echo(f"  status {row['status']:<6} {row['n']}")
    sup = "SELECT COUNT(*) FROM suppression WHERE type=?"
    typer.echo(f"suppression   {one('SELECT COUNT(*) FROM suppression')}"
               f"  (emails {one(sup, 'email')}, domains {one(sup, 'domain')})")
    typer.echo("imports")
    for row in conn.execute("SELECT * FROM imports ORDER BY id"):
        rej = json.loads(row["rejected_json"] or "{}")
        rej_txt = ", ".join(f"{k} {v}" for k, v in rej.items()) or "none"
        typer.echo(f"  #{row['id']} {row['source_file']}  read {row['rows_read']},"
                   f" added {row['leads_added']}, rejected: {rej_txt}")


EVENTS_RESERVE_PER_CALL = 40


def _client(conn, budget=None, run_id=None):
    key = os.environ.get("EXPLORIUM_API_KEY", "")
    if not key:
        return None

    def on_call(**kw):
        conn.execute(
            "INSERT INTO api_calls (ts, provider, endpoint, mode, records, credits, correlation_id, run_id)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (now(), "explorium", kw["endpoint"], kw["mode"], kw["records"], kw["credits"],
             kw["correlation_id"], run_id))
        conn.commit()
    return ExploriumClient(HttpTransport(key), budget=budget, on_call=on_call)


@app.command()
def find(
    niche: str = typer.Argument(None, help="search key from config/searches.yaml"),
    limit: int = typer.Option(25, min=1, max=500, help="max companies to pull"),
    live: bool = typer.Option(False, "--live", help="actually pull and spend credits"),
    max_credits: int = typer.Option(0, help="hard credit cap for a live run (required with --live)"),
    list_: bool = typer.Option(False, "--list", help="list configured searches"),
):
    """find prospects via Explorium. dry run shows free counts and a credit estimate."""
    searches = finder.load_searches(CONFIG_DIR / "searches.yaml")
    if list_ or not niche:
        for k, v in searches.items():
            typer.echo(f"{k:<15} {v.get('niche', '')}")
        return
    if niche not in searches:
        raise typer.BadParameter(f"unknown niche {niche!r}. try --list")
    s = searches[niche]
    conn = _conn()

    if not live:
        est = finder.estimate(_client(conn), s, limit)
        typer.echo(f"DRY RUN  {niche}: {s.get('niche', '')}")
        typer.echo(f"  filters        countries {s['countries']}, size {s['company_size']}, levels {s['job_levels']}")
        typer.echo(f"  events         {s.get('events') or 'none'} in the last {s['lookback_days']} days")
        if est.businesses_matching is not None:
            typer.echo(f"  companies matching (free stats)   {est.businesses_matching}")
            typer.echo(f"  decision makers matching (free)   {est.prospects_matching}  (before the events filter)")
        typer.echo(f"  live run of {limit} companies, worst case:")
        typer.echo(f"    known costs  {est.known_credits} credits  (companies 1, people 1, email 2 each)")
        if est.events_calls:
            typer.echo(f"    events       {est.events_calls} call(s), price not in docs, "
                       f"reserve {est.events_calls * EVENTS_RESERVE_PER_CALL}")
        total = est.known_credits + est.events_calls * EVENTS_RESERVE_PER_CALL
        typer.echo(f"    suggested    --max-credits {total}")
        for n in est.notes:
            typer.echo(f"  note: {n}")
        typer.echo("nothing was pulled or spent.")
        return

    if max_credits <= 0:
        raise typer.BadParameter("--live needs --max-credits N (a hard cap)")
    if not os.environ.get("EXPLORIUM_API_KEY"):
        raise typer.BadParameter("EXPLORIUM_API_KEY missing in .env")
    budget = CreditBudget(max_credits)
    run_id = f"find-{niche}"
    client = _client(conn, budget, run_id)
    try:
        r = finder.run_live(conn, client, niche, s, limit)
    except BudgetExceeded as e:
        log_event(conn, "find_stopped", "run", run_id, str(e), spent=budget.spent)
        conn.commit()
        typer.echo(f"STOPPED: {e}  (spent so far: {budget.spent})")
        raise typer.Exit(1)
    imp = r.import_result
    typer.echo(f"LIVE {r.run_id}")
    typer.echo(f"  companies returned  {r.businesses_returned}  skipped {r.businesses_skipped or 'none'}")
    typer.echo(f"  events found        {r.events_found}")
    typer.echo(f"  people returned     {r.prospects_returned}")
    typer.echo(f"  emails found        {r.emails_found}  dropped {r.emails_dropped or 'none'}")
    typer.echo(f"  leads added         {imp.leads_added}  rejected {dict(imp.rejected) or 'none'}")
    typer.echo(f"  credits spent       {r.credits_spent} of {max_credits}"
               + (f"  ({r.unknown_cost_calls} call(s) did not report cost, counted at reserve)"
                  if r.unknown_cost_calls else ""))


@app.command()
def verify(
    offline: bool = typer.Option(False, "--offline", help="skip the MX lookup (marks it unchecked)"),
    redo: bool = typer.Option(False, "--redo", help="re-check leads that were already verified"),
):
    """free email checks: syntax, role mailbox, personal domain, domain match, MX, source status."""
    conn = _conn()
    counts = verify_leads(conn, LocalVerifier(offline=offline), only_unverified=not redo)
    typer.echo(f"verified  ok {counts['ok']}  risky {counts['risky']}  fail {counts['fail']}")
    if offline:
        typer.echo("note: offline, so mail servers were not checked")


@app.command()
def score():
    """score every lead 0 to 100 and set tier A, B, C or dnc. weights in config/scoring.yaml."""
    conn = _conn()
    c = score_all(conn, ScoreConfig.load(CONFIG_DIR))
    typer.echo(f"scored  A {c['A']}  B {c['B']}  C {c['C']}  dnc {c['dnc']}")


@app.command()
def leads(
    tier: str = typer.Option(None, help="A, B, C or dnc"),
    limit: int = typer.Option(50),
    reasons: bool = typer.Option(True, "--reasons/--no-reasons"),
):
    """list scored leads, best first."""
    conn = _conn()
    q = ("SELECT l.*, c.name AS company FROM leads l LEFT JOIN companies c ON c.id=l.company_id"
         + (" WHERE l.tier=?" if tier else "") + " ORDER BY l.score DESC, l.id LIMIT ?")
    rows = conn.execute(q, ((tier, limit) if tier else (limit,))).fetchall()
    for r in rows:
        name = f"{r['first_name'] or ''} {r['last_name'] or ''}".strip()
        typer.echo(f"{(r['tier'] or '-'):<4}{(r['score'] if r['score'] is not None else '-'):>4}  "
                   f"{name[:18]:<18} {(r['company'] or '')[:22]:<22} {r['email']}")
        if reasons and r["score_reason"]:
            typer.echo(f"          {r['score_reason']}")


@signals_app.command("refresh")
def signals_refresh():
    """detect csv signals, classify every signal, expire stale ones. run before `engine score`."""
    from datetime import datetime, timezone
    conn = _conn()
    d = sig_engine.detect_csv(conn, CONFIG_DIR / "csv_signals.yaml")
    e = sig_engine.classify_and_expire(conn, ScoreConfig.load(CONFIG_DIR), datetime.now(timezone.utc))
    typer.echo(f"csv signals   added {d['added']}, skipped no date {d['skipped_no_date']},"
               f" no match {d['skipped_no_match']}, already had {d['already']}")
    typer.echo(f"signals       active {e['classified'] - e['expired']}, expired {e['expired']},"
               f" undated {e['undated']}, unknown type {e['unknown_type']}")


@signals_app.command("pick")
def signals_pick():
    """pick the best signal per lead (max 2) and write the why-now lines. run after `engine score`."""
    from datetime import datetime, timezone
    conn = _conn()
    r = sig_engine.pick_all(conn, ScoreConfig.load(CONFIG_DIR), datetime.now(timezone.utc))
    typer.echo(f"picked  with signal {r['with_signal']}, plain fallback {r['fallback']}, skipped dnc {r['skipped_dnc']}")


@signals_app.command("list")
def signals_list(limit: int = typer.Option(30), expired: bool = typer.Option(False, "--expired")):
    """leads with their picked signal, age and why-now lines. --expired shows stale signals instead."""
    conn = _conn()
    if expired:
        for r in conn.execute("SELECT s.*, c.name FROM signals s LEFT JOIN companies c ON c.id=s.company_id"
                              " WHERE s.active=0 ORDER BY s.id LIMIT ?", (limit,)):
            typer.echo(f"expired  {r['name'] or '':<22} {r['signal_class'] or r['type']:<18} seen {str(r['observed_at'])[:10]}")
        return
    q = """SELECT l.id, l.first_name, l.tier, l.score, l.fallback, c.name AS company,
                  p.signal_class, p.age_days, p.saw_line, p.matters_line, p.rank, s.source
           FROM leads l LEFT JOIN companies c ON c.id=l.company_id
           LEFT JOIN signal_picks p ON p.lead_id=l.id LEFT JOIN signals s ON s.id=p.signal_id
           WHERE l.tier != 'dnc' ORDER BY l.score DESC, l.id, p.rank LIMIT ?"""
    for r in conn.execute(q, (limit,)):
        head = f"{r['tier']:<2} {r['score']:>3}  {(r['first_name'] or ''):<8} {(r['company'] or '')[:20]:<20}"
        if r["signal_class"]:
            age = f"{r['age_days']}d" if r["age_days"] is not None else "current"
            typer.echo(f"{head} {r['signal_class']:<16} {age:>7}  [{r['source']}]")
            typer.echo(f"{'':>8}{r['saw_line']} {r['matters_line']}")
        else:
            typer.echo(f"{head} plain fallback (no usable signal)")


@app.command()
def write(
    limit: int = typer.Option(20, min=1, max=500),
    tiers: str = typer.Option("A,B,C", help="comma list of tiers to write for"),
    fake: bool = typer.Option(False, "--fake", help="use the offline fake writer (no api, no cost)"),
    yes: bool = typer.Option(False, "--yes", help="actually call the api after showing the estimate"),
    max_usd: float = typer.Option(0.0, help="hard dollar cap for a real run (required with --yes)"),
    redo: bool = typer.Option(False, "--redo", help="write again for leads that already have an email 1"),
    export: bool = typer.Option(False, "--export", help="session mode: write a json brief, no api"),
    import_path: Path = typer.Option(None, "--import", exists=True, dir_okay=False,
                                     help="session mode: read written emails back and run the rule checks"),
    out_dir: Path = typer.Option(None, help="where --export writes (default data/exports)"),
):
    """write email 1. writer_mode in config/llm.yaml: session (default, free) or api.

    session: --export writes a brief, Claude writes the emails in this session, --import checks them.
    api: estimate first, --yes --max-usd to run (or --fake for the offline stand in)."""
    from datetime import datetime, timezone
    from .personalize import fake_writer, prompts, session, writer as w
    from .personalize.llm import AnthropicLLM, FakeLLM
    from .paths import ROOT

    conn = _conn()
    cfg = w.load_cfg(CONFIG_DIR)
    today = datetime.now(timezone.utc)
    mode = cfg["llm"].get("writer_mode", "session")

    if import_path:
        r = session.import_file(conn, import_path, cfg)
        typer.echo(f"imported  ready {r['ready']}  held {r['held']}  (ready as plain fallback: {r['fallback']})")
        if r["unknown_lead"] or r["already_imported"]:
            typer.echo(f"skipped   not in that export {r['unknown_lead']}, already imported {r['already_imported']}")
        typer.echo("next: engine review")
        return

    leads_ = w.eligible_leads(conn, [t.strip() for t in tiers.split(",")], limit, redo)
    if not leads_:
        typer.echo("no eligible leads (needs a tier, a verified or risky email, and no email 1 yet)")
        return

    if export or (mode == "session" and not fake and not yes):
        path, n = session.export_batch(conn, leads_, cfg["niches"], out_dir or ROOT / "data" / "exports", today)
        typer.echo(f"exported {n} leads to {path}")
        typer.echo("nothing left this machine. ask Claude in the session to write the emails from that file,")
        typer.echo(f"then: engine write --import <answer file>")
        return

    if fake:
        llm = FakeLLM(fake_writer.writer, fake_writer.qa)
    else:
        # dry run sends nothing anywhere (rule 1), so it estimates offline.
        # with --yes we start the client and count tokens properly before writing.
        llm = None
        if yes:
            if max_usd <= 0:
                raise typer.BadParameter("--yes needs --max-usd N (a hard dollar cap)")
            try:
                llm = AnthropicLLM(cfg["llm"])
            except Exception as e:  # noqa: BLE001  no credentials
                raise typer.BadParameter(f"anthropic client could not start: {e}. add ANTHROPIC_API_KEY to .env")
        sample = w.build_context(conn, leads_[0], cfg["niches"], today)
        try:
            est = w.estimate(llm, cfg, sample, len(leads_))
        except Exception:  # noqa: BLE001  count_tokens failed
            est = w.estimate(None, cfg, sample, len(leads_))
        typer.echo(f"ESTIMATE  {len(leads_)} leads  writer {est['models']['writer']}, qa {est['models']['qa']}")
        typer.echo(f"  input tokens per call   writer {est['writer_input_tokens']}, qa {est['qa_input_tokens']}  ({est['method']})")
        typer.echo(f"  per lead                ${est['per_lead_typical']} typical, ${est['per_lead_worst']} worst (retry as fallback)")
        typer.echo(f"  batch                   ${est['batch_typical']} typical, ${est['batch_worst']} worst")
        typer.echo("  note: prompt caching on the system prompt should bring the real cost below this.")
        if not yes:
            typer.echo(f"nothing was sent to the api. to run: engine write --limit {limit} --yes --max-usd {est['batch_worst']}")
            return
        if est["batch_worst"] > max_usd:
            typer.echo(f"note: worst case ${est['batch_worst']} is above the ${max_usd} cap, so the run stops early.")

    spend = w.Spend()
    sys_w, sys_q = prompts.system_prompt(), prompts.qa_system_prompt()
    per_lead_reserve = 0.0 if fake else est["per_lead_worst"]
    counts = {"ready": 0, "held": 0, "fallback": 0}
    for lead in leads_:
        if not fake and spend.usd + per_lead_reserve > max_usd:
            typer.echo(f"STOPPED at the ${max_usd} cap (spent ${spend.usd:.4f}). remaining leads not written.")
            break
        if redo:   # replace unsent drafts, never touch an email that has a send record
            conn.execute("DELETE FROM messages WHERE lead_id=? AND step=1 AND id NOT IN (SELECT message_id FROM sends)",
                         (lead["id"],))
        o = w.write_one(conn, llm, cfg, lead, today, spend, sys_w, sys_q)
        conn.commit()
        counts[o.status] += 1
        counts["fallback"] += int(o.uses_fallback)
    typer.echo(f"written  ready {counts['ready']}  held {counts['held']}  (used plain fallback: {counts['fallback']})")
    typer.echo(f"api      {'fake, $0' if fake else f'{spend.calls} calls, ${spend.usd:.4f}, {spend.input_tokens} in / {spend.output_tokens} out tokens'}")


@app.command()
def messages(
    status: str = typer.Option(None, help="ready or held"),
    limit: int = typer.Option(10),
    full: bool = typer.Option(True, "--full/--short"),
):
    """show written email 1s with QA verdicts."""
    import json as _j
    conn = _conn()
    q = ("SELECT m.*, l.first_name, l.email, l.tier, c.name AS company FROM messages m JOIN leads l ON l.id=m.lead_id"
         " LEFT JOIN companies c ON c.id=l.company_id WHERE m.step=1" + (" AND m.status=?" if status else "")
         + " ORDER BY m.id LIMIT ?")
    for r in conn.execute(q, ((status, limit) if status else (limit,))):
        typer.echo(f"--- #{r['id']} {r['status'].upper()}  tier {r['tier']}  {r['email']}  variant {r['variant']}"
                   f"  {r['signal_class']}  tries {r['attempts']}")
        reasons = _j.loads(r["qa_reasons"] or "[]")
        for x in reasons:
            typer.echo(f"    qa: {x}")
        if full and r["body"]:
            typer.echo(f"subject: {r['subject']}")
            typer.echo(r["body"])


@app.command()
def review(
    approve: str = typer.Option(None, help="message ids to approve, comma list, or 'all' (pending only)"),
    hold: str = typer.Option(None, help="message ids to hold, comma list"),
    note: str = typer.Option(None, help="reason, stored with the decision"),
    status: str = typer.Option("pending", help="which to show: pending, approved or held"),
    limit: int = typer.Option(50),
):
    """email 1 next to its facts and the rule check result. approve or hold by id."""
    import json as _j
    from .personalize import session
    conn = _conn()
    if approve or hold:
        if approve:
            ids = [r["id"] for r in session.review_rows(conn, "pending", 10000)] if approve == "all" else \
                [int(x) for x in approve.split(",") if x.strip()]
            typer.echo(f"approved {session.set_review(conn, ids, 'approved', note)}")
        if hold:
            typer.echo(f"held {session.set_review(conn, [int(x) for x in hold.split(',') if x.strip()], 'held', note)}")
        return
    rows = session.review_rows(conn, status, limit)
    if not rows:
        typer.echo(f"nothing {status}")
        return
    for r in rows:
        f = _j.loads(r["facts_json"] or "{}")
        facts, used = f.get("facts", []), set(f.get("facts_used", []))
        typer.echo("=" * 78)
        typer.echo(f"#{r['id']}  {r['first_name']} <{r['email']}>  tier {r['tier']} ({r['score']})  "
                   f"{r['signal_class']}  variant {r['variant']}  {r['writer_mode'] or 'api'}")
        typer.echo("facts given:")
        for x in facts:
            mark = "used" if x["id"] in used else "    "
            src = f"{x.get('source') or ''} {x.get('field') or ''}".strip()
            typer.echo(f"  [{mark}] {x['id']:<10} {x['text'][:70]}  <- {src}{', ' + str(x['date'])[:10] if x.get('date') else ''}")
        reasons = _j.loads(r["qa_reasons"] or "[]")
        typer.echo(f"rule check: {'PASS' if r['status'] == 'ready' else 'FAIL'}"
                   + (f"  (earlier try failed: {len(reasons)} issue(s))" if reasons and r['status'] == 'ready' else ""))
        for x in reasons:
            typer.echo(f"  - {x}")
        typer.echo(f"subject: {r['subject']}")
        typer.echo(r["body"])
    typer.echo("=" * 78)
    typer.echo("approve: engine review --approve <ids|all>   hold: engine review --hold <ids> --note '...'")


@mailbox_app.command("add")
def mailbox_add(
    address: str,
    warmup_start: str = typer.Option(None, help="YYYY-MM-DD the warmup started (new mailboxes)"),
    aged: bool = typer.Option(False, "--aged", help="an existing work mailbox already in real use: no warmup wait,"
                              " the ramp starts today (10 a day, +5 a week)"),
    daily_cap: int = typer.Option(30, help="hard ceiling for this mailbox"),
    cap: int = typer.Option(None, help="fixed sends a day, no ramp (mature mailbox). e.g. --cap 300"),
):
    """add a sending mailbox. refuses the main domain (CLAUDE.md hard rule 2)."""
    import yaml as _y
    from .normalize import norm_domain
    conn = _conn()
    dom = norm_domain(address)
    from .send.runner import is_override
    sender_cfg = _y.safe_load((CONFIG_DIR / "sender.yaml").read_text()) or {}
    blocked = set(sender_cfg.get("main_domains") or [])
    override = is_override(sender_cfg, address)
    if override:
        mx = int(sender_cfg["main_domain_override"].get("max_per_day", 10))
        cap = min(cap or mx, mx)
        daily_cap = max(daily_cap, cap)
        typer.echo(f"main domain override (abdul's call): {cap} a day, auto pause on bounces or complaints")
    elif dom in blocked or dom in ("gmail.com", "googlemail.com"):
        raise typer.BadParameter(f"{dom} is a main domain or personal gmail. cold email only from secondary domains")
    conn.execute("INSERT OR IGNORE INTO domains (domain) VALUES (?)", (dom,))
    did = conn.execute("SELECT id FROM domains WHERE domain=?", (dom,)).fetchone()[0]
    from datetime import date as _d
    if not warmup_start and not aged:
        raise typer.BadParameter("give --warmup-start YYYY-MM-DD for a new mailbox, or --aged for one already in use")
    live_from = _d.today().isoformat() if aged else None
    if cap:
        daily_cap = max(daily_cap, cap)
    conn.execute("INSERT OR IGNORE INTO mailboxes (address, domain_id, warmup_started_at, daily_cap, live_from,"
                 " fixed_cap) VALUES (?,?,?,?,?,?)", (address.lower(), did, warmup_start, daily_cap, live_from, cap))
    conn.commit()
    typer.echo(f"mailbox {address} added, " + (f"{cap} a day, no ramp" if cap else
               f"aged, ramp starts {live_from}" if aged else f"warmup from {warmup_start}"))


@mailbox_app.command("set-cap")
def mailbox_set_cap(address: str, cap: int = typer.Argument(..., min=0, max=500, help="sends a day, 0 = back to the ramp")):
    """set a fixed daily number for a mature mailbox (no ramp). Spacemail's own limit is 500 an hour."""
    import yaml as _y
    from .send.runner import is_override
    sender_cfg = _y.safe_load((CONFIG_DIR / "sender.yaml").read_text()) or {}
    if is_override(sender_cfg, address):
        cap = min(cap, int(sender_cfg["main_domain_override"].get("max_per_day", 10)))
    conn = _conn()
    n = conn.execute("UPDATE mailboxes SET fixed_cap=?, daily_cap=MAX(daily_cap, ?) WHERE address=?",
                     (cap or None, cap, address.lower())).rowcount
    log_event(conn, "mailbox_cap_set", "mailbox", address, None, cap=cap)
    conn.commit()
    typer.echo(f"{address}: {cap or 'ramp'} a day" if n else f"no mailbox {address}. engine mailboxes add first")


@mailbox_app.command("list")
def mailbox_list():
    """mailboxes with today's cap and health."""
    from datetime import date
    from .sequence import scheduler as sch
    conn = _conn()
    cfg = sch.load_cfg(CONFIG_DIR)
    for r in conn.execute("SELECT * FROM mailboxes ORDER BY id"):
        cap = sch.mailbox_cap(dict(r), date.today(), cfg["sending"]["ramp"])
        sent = conn.execute("SELECT COUNT(*) FROM sends WHERE mailbox_id=? AND status='sent'", (r["id"],)).fetchone()[0]
        today = conn.execute("SELECT COUNT(*) FROM sends WHERE mailbox_id=? AND status='sent' AND date(sent_at)=?",
                             (r["id"], date.today().isoformat())).fetchone()[0]
        last = conn.execute("SELECT outcome FROM sends WHERE mailbox_id=? AND status IN ('sent','failed')"
                            " ORDER BY id DESC LIMIT 200", (r["id"],)).fetchall()
        bpct = round(sum(1 for x in last if x["outcome"] == "bounce") / len(last) * 100, 1) if last else 0.0
        replies = sum(1 for x in last if x["outcome"] == "reply")
        state = f"PAUSED: {r['pause_reason']}" if r["paused"] else "ok"
        start = r["live_from"] and f"aged, live {str(r['live_from'])[:10]}" or f"warmup {str(r['warmup_started_at'])[:10]}"
        typer.echo(f"{r['address']:<32} {start}  today {today:>2}/{cap:<2}  sends {sent:>4}  bounce {bpct}% (last 200)"
                   f"  replies {replies}  {state}")


@mailbox_app.command("unpause")
def mailbox_unpause(address: str):
    """resume a paused mailbox after you checked what went wrong."""
    conn = _conn()
    conn.execute("UPDATE mailboxes SET paused=0, pause_reason=NULL WHERE address=?", (address.lower(),))
    log_event(conn, "mailbox_unpaused", "mailbox", address, "by abdul")
    conn.commit()
    typer.echo(f"unpaused {address}")


@mailbox_app.command("test")
def mailbox_test(
    address: str,
    to: str = typer.Option(..., help="one of your own addresses (sender.yaml own_addresses)"),
    live: bool = typer.Option(False, "--live", help="really send the one test email"),
):
    """send ONE test email from a mailbox to your own address, to check login, headers and the spam tab."""
    import yaml as _y
    from .paths import load_dotenv
    from .send import spacemail as sm
    from .send.base import OutgoingEmail, build_headers
    load_dotenv()
    sender = _y.safe_load((CONFIG_DIR / "sender.yaml").read_text()) or {}
    own = {a.lower() for a in sender.get("own_addresses") or []}
    if to.lower() not in own:
        raise typer.BadParameter(f"{to} is not in own_addresses in config/sender.yaml. tests only go to you")
    body = ("Test from the outbound engine. If this landed in spam, tell Claude.\n\n" + "\n".join(
        [sender.get("sign_off", ""), sender.get("company_line", ""), sender.get("opt_out_line", "")]))
    out = OutgoingEmail(0, 0, address.lower(), to, "Engine test", body, "")
    import uuid as _u
    out.headers = build_headers(out, None, f"<{_u.uuid4().hex}@{address.split('@')[1]}>", True)
    msg = sm.build_message(address.lower(), sender.get("from_name") or "", to, out.subject, body, out.headers)
    if not live:
        typer.echo("dry run. this is what would go out (add --live to send it):\n")
        typer.echo(msg.as_string())
        return
    with sm.SmtpSession(address.lower(), sm.password_for(address)) as smtp:
        smtp.send(msg)
    conn = _conn()
    log_event(conn, "test_send", "mailbox", address, None, to=to)
    conn.commit()
    typer.echo(f"sent one test email {address} -> {to}. check inbox AND spam, then open 'show original' for SPF/DKIM/DMARC.")


@app.command()
def deliverability(domain: str = typer.Option(None, help="one domain (default: every sending mailbox domain)")):
    """check MX, SPF, DKIM, DMARC and blocklists for sending domains. live sending needs a pass."""
    from .normalize import norm_domain
    from .send import dns_check
    conn = _conn()
    doms = [domain] if domain else sorted({norm_domain(r["address"]) for r in conn.execute("SELECT address FROM mailboxes")})
    if not doms:
        typer.echo("no mailboxes yet. engine mailboxes add you@second-domain.com --aged")
        raise typer.Exit(1)
    bad = 0
    for d in doms:
        rep = dns_check.check_domain(d)
        dns_check.save(conn, rep)
        log_event(conn, "dns_check", "domain", d, "pass" if rep.ok else "blocked")
        for line in rep.lines():
            typer.echo(line)
        bad += 0 if rep.ok else 1
    conn.commit()
    raise typer.Exit(1 if bad else 0)


@app.command("build-batch")
def build_batch(
    limit: int = typer.Option(500, help="max emails to write this run"),
    offline: bool = typer.Option(False, "--offline", help="skip the MX lookup during verify"),
):
    """the whole morning in one go: signals, verify, score, pick, write with the approved templates.
    template emails that pass every rule are approved automatically (sending.yaml auto_approve_template)."""
    from .pipeline import build_batch as _bb
    conn = _conn()
    out = _bb(conn, CONFIG_DIR, offline, limit)
    e, v, s, p, r, auto = (out[k] for k in ("signals", "verify", "score", "picks", "written", "auto_approve"))
    typer.echo(f"signals   active {e['classified'] - e['expired']}, expired {e['expired']}")
    typer.echo(f"verify    ok {v['ok']}  risky {v['risky']}  fail {v['fail']}")
    typer.echo(f"score     A {s['A']}  B {s['B']}  C {s['C']}  dnc {s['dnc']}")
    typer.echo(f"picks     with signal {p['with_signal']}, fallback {p['fallback']}")
    typer.echo(f"written   ready {r['ready']}{' (auto approved)' if auto else ''}  held {r['held']}"
               f"  no signal {r['no_signal']}  no template {r['no_template']}")
    typer.echo("next: engine review --status held (why held), engine simulate --days 1 (dry run of the day)")


@app.command("app")
def app_cmd(port: int = typer.Option(8765), no_browser: bool = typer.Option(False, "--no-browser")):
    """DFY Sender: the dashboard in your browser (upload CSV, emails, inbox, Start/Stop). keep it open to send."""
    from .app.server import main
    main(port, open_browser=not no_browser)


@app.command("go-live")
def go_live(confirm: str = typer.Option(..., prompt="type SEND to allow live sending on this machine")):
    """create the live_ok file. only after abdul approved live sending in chat."""
    import yaml as _y
    from .send.runner import _p
    if confirm != "SEND":
        raise typer.BadParameter("not confirmed")
    lv = _y.safe_load((CONFIG_DIR / "sending.yaml").read_text())["live"]
    _p(lv["ok_file"]).parent.mkdir(parents=True, exist_ok=True)
    _p(lv["ok_file"]).write_text(f"approved {__import__('datetime').datetime.now().isoformat()}\n")
    conn = _conn()
    log_event(conn, "go_live", None, None, "live_ok created")
    conn.commit()
    typer.echo("live sending allowed. engine run --live (or the cron) will now send when every other gate passes.")


@app.command()
def stop():
    """kill switch: no sending at all until engine start."""
    import yaml as _y
    from .send.runner import _p
    lv = _y.safe_load((CONFIG_DIR / "sending.yaml").read_text())["live"]
    _p(lv["stop_file"]).parent.mkdir(parents=True, exist_ok=True)
    _p(lv["stop_file"]).write_text("stopped\n")
    conn = _conn()
    log_event(conn, "kill_switch_on", None, None, "engine stop")
    conn.commit()
    typer.echo("STOPPED. nothing will be sent. engine start to resume.")


@app.command()
def start():
    """turn the kill switch off again."""
    import yaml as _y
    from .send.runner import _p
    lv = _y.safe_load((CONFIG_DIR / "sending.yaml").read_text())["live"]
    _p(lv["stop_file"]).unlink(missing_ok=True)
    conn = _conn()
    log_event(conn, "kill_switch_off", None, None, "engine start")
    conn.commit()
    typer.echo("kill switch off.")


@app.command()
def run(live: bool = typer.Option(False, "--live", help="really send and read the mailboxes")):
    """one tick of the live sender (cron every 5 min). without --live it only shows the gates."""
    from datetime import datetime, timezone
    from .paths import load_dotenv
    from .send import runner
    from .sequence import scheduler as sch
    load_dotenv()
    conn = _conn()
    cfg = sch.load_cfg(CONFIG_DIR)
    if not live:
        g = runner.global_gates(cfg)
        typer.echo("gates: " + ("all clear" if not g else "; ".join(g)))
        at = datetime.now(timezone.utc)
        for r in conn.execute("SELECT * FROM mailboxes ORDER BY id"):
            typer.echo(f"  {r['address']:<32} " + (runner.mailbox_gate(conn, cfg, dict(r), at) or "ready"))
        q = conn.execute("SELECT COUNT(*) FROM sends WHERE status='queued' AND mode='live'").fetchone()[0]
        typer.echo(f"queued live sends: {q}. dry view only, add --live to run a real tick.")
        return
    lock = runner.RunLock(runner._p(cfg["sending"]["live"]["lock_file"]))
    if not lock.acquire():
        typer.echo("another tick is still running, skipped")
        return
    try:
        res = runner.tick(conn, cfg)
        for line in res.lines():
            typer.echo(line)
        if res.alerts:
            mbs = [dict(r) for r in conn.execute("SELECT * FROM mailboxes WHERE paused=0 ORDER BY id")]
            runner.send_alert(cfg, res.alerts, mbs)
    finally:
        lock.release()


@app.command()
def simulate(
    days: int = typer.Option(3, min=1, max=60),
    start: str = typer.Option(None, help="first day YYYY-MM-DD (default: today)"),
    assume_warm: bool = typer.Option(False, "--assume-warm", help="pretend warmup is finished (simulation only)"),
    bounce_rate: float = typer.Option(0.0, help="share of sends that bounce in the simulation"),
    reply_rate: float = typer.Option(0.0, help="share of sends that get a reply in the simulation"),
    seed: int = typer.Option(7),
):
    """run the scheduler day by day with the simulator sender. nothing is sent."""
    import random as _r
    from datetime import date, timedelta
    from .paths import ROOT
    from .send.base import SimulatorSender
    from .sequence import scheduler as sch
    conn = _conn()
    cfg = sch.load_cfg(CONFIG_DIR)
    rng = _r.Random(seed)
    sender = SimulatorSender(ROOT / "data" / "sim" / "outbox.jsonl")
    d0 = date.fromisoformat(start) if start else date.today()
    for i in range(days):
        day = d0 + timedelta(days=i)
        p = sch.plan_and_send(conn, cfg, day, sender, rng, assume_warm)
        sent = sum(p.sent.values())
        typer.echo(f"{day.isoformat()} {day.strftime('%a').lower()}  sent {sent}  by step {dict(sorted(p.by_step.items())) or '-'}")
        for mb, cap in p.caps.items():
            fl = p.first_last_local.get(mb)
            span = f"  local {fl[0]} to {fl[1]}" if fl else ""
            typer.echo(f"    {mb:<30} {p.sent[mb]:>3} / cap {cap}{span}")
        if p.skipped:
            typer.echo(f"    skipped: {p.skipped}")
        if bounce_rate or reply_rate:
            for s in conn.execute("SELECT id FROM sends WHERE date(sent_at)=? AND outcome IS NULL AND mode='dry_run'",
                                  (day.isoformat(),)).fetchall():
                x = rng.random()
                kind = "bounce" if x < bounce_rate else "reply" if x < bounce_rate + reply_rate else None
                if kind:
                    for a in sch.record_outcome(conn, cfg, s["id"], kind):
                        typer.echo(f"    ALERT {a}")
    typer.echo("simulation only. every send is in data/sim/outbox.jsonl, nothing left this machine.")


@app.command()
def outcome(send_id: int, kind: str = typer.Argument(..., help="reply, bounce, unsubscribe or complaint")):
    """record what happened to a send. stops the sequence and suppresses where needed."""
    from .sequence import scheduler as sch
    conn = _conn()
    for a in sch.record_outcome(conn, sch.load_cfg(CONFIG_DIR), send_id, kind) or ["recorded"]:
        typer.echo(a)


@app.command()
def calendar(days: int = typer.Option(21)):
    """sends per day and step, from the send log."""
    conn = _conn()
    rows = conn.execute("""SELECT date(sent_at) d, step, COUNT(*) n FROM sends GROUP BY d, step ORDER BY d
                           LIMIT ?""", (days * 4,)).fetchall()
    cur = None
    for r in rows:
        if r["d"] != cur:
            cur = r["d"]
            typer.echo(f"{cur}")
        typer.echo(f"   step {r['step']}: {r['n']}")


@app.command()
def summary(save: bool = typer.Option(True, "--save/--no-save", help="--no-save peeks without moving the baseline")):
    """the whole pipeline, starting with what changed since the last summary."""
    from .paths import ROOT
    from .report.summary import full_summary
    out = full_summary(_conn(), save=save)
    typer.echo(out)
    latest = ROOT / "data" / "summary-latest.txt"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(out + "\n")


@app.command()
def dashboard(
    out: Path = typer.Option(None, help="html file to write (default data/dashboard.html)"),
    include_dry_run: bool = typer.Option(False, "--include-dry-run",
                                         help="after going live, also show simulated sends"),
):
    """write the local html dashboard: totals, prospect table with filters, full history per person.
    one file, no external links, never uploaded (data/ is gitignored)."""
    from .paths import ROOT
    from .report.dashboard import write_dashboard
    path = out or ROOT / "data" / "dashboard.html"
    d = write_dashboard(_conn(), CONFIG_DIR, path, include_dry_run)
    typer.echo(f"dashboard written to {path}  ({len(d['prospects'])} prospects, {d['mode']} data)")


def _ingest(source):
    from .replies import ingest as ri
    from .sequence import scheduler as sch
    conn = _conn()
    seq_cfg, rules = sch.load_cfg(CONFIG_DIR), ri.load_rules(CONFIG_DIR)
    counts = {}
    for r in source.replies():
        res = ri.ingest_one(conn, seq_cfg, rules, r)
        key = res.category or res.status
        counts[key] = counts.get(key, 0) + 1
        for a in res.alerts:
            typer.echo(f"ALERT {a}")
    typer.echo("ingested  " + "  ".join(f"{k} {v}" for k, v in counts.items()))
    typer.echo("sequences of every matched reply were stopped (out of office: paused). next: engine replies export")


@replies_app.command("add")
def replies_add(
    from_: str = typer.Option(..., "--from", help="who wrote it"),
    text: str = typer.Option(..., help="the reply text"),
    subject: str = typer.Option(""),
    in_reply_to: str = typer.Option(None, help="Message-ID it answers, if you have it"),
    received_at: str = typer.Option(None, help="ISO time, default now"),
):
    """add one reply by hand (paste it in)."""
    from .replies.base import FakeReplySource
    _ingest(FakeReplySource([{"from": from_, "body": text, "subject": subject, "in_reply_to": in_reply_to,
                              "received_at": received_at or now()}]))


@replies_app.command("ingest")
def replies_ingest(path: Path = typer.Argument(..., exists=True, dir_okay=False)):
    """read a json list of replies (from, subject, body, received_at, message_id, in_reply_to)."""
    from .replies.base import FileReplySource
    _ingest(FileReplySource(path))


@replies_app.command("export")
def replies_export(out_dir: Path = typer.Option(None)):
    """session mode: write a brief for Claude to sort and draft. nothing leaves the machine."""
    from .paths import ROOT
    from .replies import classify as rc
    conn = _conn()
    path, n = rc.export_batch(conn, rc.load_cfg(CONFIG_DIR), out_dir or ROOT / "data" / "exports")
    typer.echo(f"exported {n} replies to {path}" if n else "no replies waiting to be sorted")
    if n:
        typer.echo("ask Claude in the session to sort them, then: engine replies import <answer file>")


@replies_app.command("import")
def replies_import(path: Path = typer.Argument(..., exists=True, dir_okay=False)):
    """read the answers back. every one is checked before anything is acted on."""
    from .replies import classify as rc
    r = rc.import_file(_conn(), rc.load_cfg(CONFIG_DIR), path)
    typer.echo(f"imported  classified {r['classified']}  needs abdul {r['needs_abdul']}"
               + (f"  skipped {r['unknown'] + r['already']}" if r["unknown"] or r["already"] else ""))
    typer.echo("next: engine replies inbox")


@replies_app.command("classify-api")
def replies_classify_api(yes: bool = typer.Option(False, "--yes", help="really call the api")):
    """api mode: Claude Haiku sorts new replies (costs money). needs --yes."""
    from .replies import classify as rc
    from .personalize.llm import AnthropicLLM
    cfg = rc.load_cfg(CONFIG_DIR)
    if not yes:
        n = _conn().execute("SELECT COUNT(*) FROM replies WHERE status='new'").fetchone()[0]
        typer.echo(f"{n} replies would go to {cfg['llm']['classifier']['model']}. nothing sent. add --yes to run.")
        return
    r = rc.classify_api(_conn(), cfg, AnthropicLLM(cfg["llm"]))
    typer.echo(f"classified {r['classified']}  needs abdul {r['needs_abdul']}")


@replies_app.command("inbox")
def replies_inbox(limit: int = typer.Option(30)):
    """urgent first, then what needs you, then what is waiting to be sorted."""
    import json as _j
    from datetime import datetime, timezone
    from .replies import classify as rc
    conn = _conn()
    rows = rc.inbox(conn, rc.load_cfg(CONFIG_DIR), limit)
    if not rows:
        typer.echo("inbox empty")
        return
    group = None
    for r in rows:
        if r["group"] != group:
            group = r["group"]
            typer.echo(f"\n==== {group.upper()} ====")
        try:
            got = datetime.fromisoformat(r["received_at"].replace("Z", "+00:00"))
            secs = (datetime.now(timezone.utc) - got).total_seconds()
            age_txt = f"received {got.strftime('%Y-%m-%d %H:%M')} utc" + (
                f", {int(secs // 3600)}h {int(secs % 3600 // 60)}m ago" if secs >= 0 else "")
        except ValueError:
            age_txt = ""
        who = f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r["from_address"]
        typer.echo(f"#{r['id']}  {who} ({r['company'] or 'no match'})  {age_txt}  matched by {r['matched_by']}")
        typer.echo(f"   category {r['category'] or '-'}  confidence {r['confidence'] if r['confidence'] is not None else '-'}"
                   f"  by {r['classified_by'] or '-'}  draft {r['draft_status'] or '-'}")
        if r["reason"]:
            typer.echo(f"   why: {r['reason']}")
        for x in _j.loads(r["check_reasons"] or "[]"):
            typer.echo(f"   CHECK: {x}")
        from .replies.ingest import new_text
        typer.echo("   they wrote: " + " ".join(new_text(r["body"] or "").split())[:220])
        if r["draft"]:
            typer.echo("   draft:")
            for line in r["draft"].splitlines():
                typer.echo(f"     {line}")
    typer.echo("\napprove: engine replies approve <id>   hold: engine replies hold <id> --note '...'   "
               "edit: engine replies edit <id> --file draft.txt")


@replies_app.command("approve")
def replies_approve(reply_id: int, note: str = typer.Option(None)):
    """approve a draft. it is queued for sending in phase 8, nothing is sent now."""
    from .replies import classify as rc
    typer.echo(rc.set_draft(_conn(), reply_id, "approved", note))


@replies_app.command("hold")
def replies_hold(reply_id: int, note: str = typer.Option(None)):
    from .replies import classify as rc
    typer.echo(rc.set_draft(_conn(), reply_id, "held", note))


@replies_app.command("edit")
def replies_edit(reply_id: int, file: Path = typer.Option(..., exists=True, dir_okay=False)):
    """replace a draft with your own text. the same checks run on it."""
    from .replies import classify as rc
    typer.echo(rc.set_draft(_conn(), reply_id, "edit", new_draft=file.read_text().strip(),
                            cfg=rc.load_cfg(CONFIG_DIR)))


def _day(d):
    from datetime import date
    return date.fromisoformat(d) if d else date.today()


@app.command("linkedin-daily")
def linkedin_daily(day: str = typer.Option(None, "--date", help="YYYY-MM-DD, default today")):
    """today's list of up to 20 tier A people: profile link + exact note. you send them by hand."""
    import yaml as _y
    from .linkedin import daily
    from .paths import ROOT
    conn = _conn()
    rd = lambda n: _y.safe_load((CONFIG_DIR / n).read_text())
    d = _day(day)
    out = daily.build(conn, rd("sending.yaml"), rd("niches.yaml"), rd("copy_rules.yaml"), d)
    md = daily.markdown(out, d)
    path = ROOT / "data" / "linkedin" / f"{d.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md)
    typer.echo(md)
    typer.echo(f"saved to {path}")


@linkedin_app.command("mark")
def linkedin_mark(lead_id: int, status: str = typer.Argument(..., help="sent, accepted, replied or skipped")):
    """record what happened after you sent it by hand."""
    from .linkedin import daily
    n = daily.mark(_conn(), lead_id, status)
    typer.echo(f"marked {status}" if n else "that lead is not on a linkedin list")


@app.command()
def booked(lead: str = typer.Argument(..., help="lead id or email"), when: str = typer.Option(None, help="ISO date, default now")):
    """record a booked call. counts in the reports."""
    from .normalize import norm_email
    conn = _conn()
    r = conn.execute("SELECT id FROM leads WHERE id=? OR email=?", (int(lead) if lead.isdigit() else -1, norm_email(lead))).fetchone()
    if not r:
        raise typer.BadParameter(f"no lead {lead!r}")
    conn.execute("UPDATE leads SET booked_at=? WHERE id=?", (when or now(), r["id"]))
    log_event(conn, "call_booked", "lead", r["id"], None)
    conn.commit()
    typer.echo(f"call booked for lead {r['id']}")


@app.command()
def report(kind: str = typer.Argument("daily", help="daily or weekly"),
           day: str = typer.Option(None, "--date", help="last day of the report, default today")):
    """sends, bounces, replies, positive replies, calls booked, split by niche, signal, variant and subject, plus a/b."""
    import yaml as _y
    from .report import periodic
    if kind not in ("daily", "weekly"):
        raise typer.BadParameter("daily or weekly")
    cfg = _y.safe_load((CONFIG_DIR / "sending.yaml").read_text())
    typer.echo(periodic.text(periodic.build(_conn(), cfg, kind, _day(day))))


@app.command()
def serve(
    port: int = typer.Option(8765, help="local port"),
    host: str = typer.Option("127.0.0.1", help="bind address. keep 127.0.0.1 so only this machine can reach it"),
    include_dry_run: bool = typer.Option(False, "--include-dry-run"),
):
    """serve the dashboard on localhost. only the dashboard page is served, never the database
    or anything else in data/. it rebuilds from the database on every page load."""
    import http.server
    from .report.dashboard import write_dashboard
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="dfy-dash-"))
    page = tmp / "dashboard.html"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0].split("#")[0] not in ("/", "/dashboard.html"):
                self.send_error(404, "only the dashboard is served here")
                return
            write_dashboard(_conn(), CONFIG_DIR, page, include_dry_run)   # fresh numbers every load
            body = page.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    srv = http.server.ThreadingHTTPServer((host, port), Handler)
    typer.echo(f"dashboard on http://{host}:{port}/  (ctrl+c to stop). only this page is served.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


@app.command()
def note(lead: str = typer.Argument(..., help="lead id or email"), text: str = typer.Argument(...)):
    """add a note to a prospect. it shows in their dashboard history."""
    from .normalize import norm_email
    conn = _conn()
    row = conn.execute("SELECT id FROM leads WHERE id=? OR email=?",
                       (int(lead) if lead.isdigit() else -1, norm_email(lead))).fetchone()
    if not row:
        raise typer.BadParameter(f"no lead {lead!r}")
    conn.execute("INSERT INTO notes (lead_id, text, created_at) VALUES (?,?,?)", (row["id"], text, now()))
    log_event(conn, "note_added", "lead", row["id"], None)
    conn.commit()
    typer.echo(f"note added to lead {row['id']}")


@app.command()
def variants():
    """variant rotation report: how many email 1s per signal class and variant."""
    conn = _conn()
    rows = conn.execute("SELECT signal_class, variant, status, COUNT(*) n FROM messages WHERE step=1"
                        " GROUP BY signal_class, variant, status ORDER BY signal_class, variant").fetchall()
    table: dict = {}
    for r in rows:
        table.setdefault(r["signal_class"], {}).setdefault(r["variant"], {})[r["status"]] = r["n"]
    for cls, vs in table.items():
        cells = "  ".join(f"{v}: {d.get('ready', 0)} ready/{d.get('held', 0)} held" for v, d in sorted(vs.items()))
        typer.echo(f"{cls:<18} {cells}")


@suppress_app.command("add")
def suppress_add(
    value: str = typer.Argument(..., help="email or domain"),
    reason: str = typer.Option(..., "--reason", "-r"),
):
    """add an email or domain to the suppression list."""
    conn = _conn()
    type_ = "email" if "@" in value else "domain"
    added = suppression.add(conn, type_, value, reason)
    conn.commit()
    typer.echo(f"{'added' if added else 'already there'}: {type_} {value}")


@suppress_app.command("check")
def suppress_check(email: str):
    """check if an email would be blocked."""
    why = suppression.check(_conn(), email)
    typer.echo(why or "not suppressed")


if __name__ == "__main__":
    app()
