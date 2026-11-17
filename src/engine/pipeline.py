"""the morning in one call, shared by `engine build-batch` and the app: classify signals, free verify,
score, pick, write email 1 from the approved templates (auto approve on a full rule pass)."""
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .personalize import template_writer as tw, writer as w
from .score.scorer import Config as ScoreConfig, score_all
from .signals import engine as sig_engine
from .verify.local import LocalVerifier
from .verify.runner import verify_leads


def build_batch(conn, config_dir: Path, offline: bool = False, limit: int = 500) -> dict:
    today = datetime.now(timezone.utc)
    scfg = ScoreConfig.load(config_dir)
    e = sig_engine.classify_and_expire(conn, scfg, today)
    v = verify_leads(conn, LocalVerifier(offline=offline), only_unverified=True)
    s = score_all(conn, scfg)
    p = sig_engine.pick_all(conn, scfg, today)
    cfg = w.load_cfg(config_dir)
    auto = bool((yaml.safe_load((Path(config_dir) / "sending.yaml").read_text()) or {}).get("auto_approve_template"))
    r = tw.write_batch(conn, cfg, config_dir, w.eligible_leads(conn, ["A", "B", "C"], limit), today, auto)
    return {"signals": e, "verify": v, "score": s, "picks": p, "written": r, "auto_approve": auto}


def import_csv(conn, config_dir: Path, path: Path, source: str = "jobs", only_agencies: bool = True) -> dict:
    from .ingest.clay_csv import ClayCsvSource, load_column_map
    from .ingest.importer import run_import
    if source == "jobs":
        from .ingest.jobs_csv import CachedJobsSource, JobsCsvSource, add_job_signals
        js = JobsCsvSource(path, Path(config_dir) / "jobs_columns.yaml", only_agencies)
        r = run_import(conn, CachedJobsSource(js))
        n = add_job_signals(conn, r, js)
        filtered = dict(js.skipped)
    else:
        r = run_import(conn, ClayCsvSource(path, load_column_map(Path(config_dir) / "clay_columns.yaml")))
        n, filtered = 0, {}
    return {"rows": r.rows_read, "added": r.leads_added, "rejected": dict(r.rejected), "filtered": filtered,
            "signals": n}
