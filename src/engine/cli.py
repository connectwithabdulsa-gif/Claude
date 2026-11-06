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
from .verify.local import LocalVerifier
from .verify.runner import verify_leads

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="DFY GTM Systems outbound engine. dry run by default.")
suppress_app = typer.Typer(no_args_is_help=True, help="manage the suppression list.")
app.add_typer(suppress_app, name="suppress")


def _conn():
    conn = connect(db_path())
    suppression.load_csv(conn, CONFIG_DIR / "suppression.csv")
    conn.commit()
    return conn


@app.command("import")
def import_cmd(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, help="csv export"),
    source: str = typer.Option("clay", help="source type. only 'clay' for now."),
):
    """import a lead csv. dedupes by email and domain, checks suppression."""
    if source != "clay":
        raise typer.BadParameter("only --source clay is supported right now")
    conn = _conn()
    src = ClayCsvSource(path, load_column_map(CONFIG_DIR / "clay_columns.yaml"))
    r = run_import(conn, src)
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
