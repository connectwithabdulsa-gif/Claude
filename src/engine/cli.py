"""engine cli. everything is dry run. nothing here talks to an outside service."""
import json
from pathlib import Path

import typer

from . import suppression
from .db import connect
from .ingest.clay_csv import ClayCsvSource, load_column_map
from .ingest.importer import run_import
from .paths import CONFIG_DIR, db_path

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
