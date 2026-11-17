"""job-board csv (e.g. 24hrs_244_jobs.csv): companies that posted a job in the last day, with a
decision maker per row. keeps agencies only (by industry), one person per company (best title),
and turns each job post into a dated signal (hiring_delivery_role) with the post url as source."""
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Iterator

import yaml

from ..db import log_event, now
from ..normalize import norm_domain, norm_key
from .base import SourceInfo, SourceRow

TOP = re.compile(r"\b(founder|co-?founder|owner|co-?owner|ceo|chief executive|president|managing (director|partner)|"
                 r"principal|partner)\b", re.I)
MID = re.compile(r"\b(head of|director|vp|vice president|chief)\b", re.I)


def load_spec(path: Path) -> tuple[dict, set]:
    spec = yaml.safe_load(Path(path).read_text()) or {}
    industries = {i.lower() for i in spec.pop("agency_industries", []) or []}
    cmap = {}
    for field, aliases in spec.items():
        for a in [field, *(aliases or [])]:
            cmap.setdefault(norm_key(a), field)
    return cmap, industries


def title_rank(title: str) -> int:
    t = title or ""
    return 0 if TOP.search(t) else 1 if MID.search(t) else 2


def clean_role(role: str) -> str:
    """'Social Media Videographer & Editor (Part-Time) ? South OC' -> 'Social Media Videographer & Editor'."""
    r = re.sub(r"\s*[\(\[].*?[\)\]]", "", role or "")
    r = re.split(r"\s[?|–-]\s|;\s|,\s", r)[0].strip()
    return re.sub(r"\s{2,}", " ", r)


class JobsCsvSource:
    def __init__(self, path: Path, spec_path: Path, only_agencies: bool = True):
        self.path = Path(path)
        self.cmap, self.industries = load_spec(spec_path)
        self.only_agencies = only_agencies
        self.info = SourceInfo(source="jobs", source_file=self.path.name)
        self.skipped: Counter = Counter()

    def _read(self) -> list[tuple[int, dict, dict]]:
        out = []
        with self.path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            mapping = {}
            for h in headers:
                t = self.cmap.get(norm_key(h))
                if t and t not in mapping.values():
                    mapping[h] = t
            self.info.unmapped_columns = [h for h in headers if h not in mapping]
            for i, raw in enumerate(reader, start=1):
                fields = {t: (raw.get(h) or "").strip() for h, t in mapping.items()}
                if not fields.get("company_name") and not fields.get("email"):
                    continue
                out.append((i, fields, dict(raw)))
        return out

    def rows(self) -> Iterator[SourceRow]:
        best: dict[str, tuple] = {}
        for line, f, raw in self._read():
            if self.only_agencies and (f.get("industry") or "").lower() not in self.industries:
                self.skipped["not an agency (industry)"] += 1
                continue
            dom = norm_domain(f.get("company_domain")) or norm_domain(f.get("email")) or f.get("company_name", "").lower()
            key = (title_rank(f.get("title")), line)
            if dom in best and best[dom][0] <= key:
                self.skipped["another person at the same company"] += 1
                continue
            if dom in best:
                self.skipped["another person at the same company"] += 1
            best[dom] = (key, line, f, raw)
        for _key, line, f, raw in sorted(best.values(), key=lambda x: x[1]):
            if title_rank(f.get("title")) == 2:
                self.skipped["no decision maker title"] += 1
                continue
            f = {**f, "job_role": clean_role(f.get("job_role", ""))}
            yield SourceRow(fields=f, raw=raw, line=line)


def add_job_signals(conn, result, source: JobsCsvSource, observed_at: str | None = None) -> int:
    """one hiring_delivery_role signal per imported lead: value = role, source = the job post url."""
    seen = observed_at or now()
    n = 0
    rows = {r.line: r for r in source_rows_cache(source)}
    for line, lead_id in result.lead_ids.items():
        row = rows.get(line)
        if not row or not row.fields.get("job_role"):
            continue
        role, post = row.fields["job_role"], row.fields.get("job_post") or ""
        conn.execute(
            "INSERT INTO signals (lead_id, company_id, type, value, source, source_field, observed_at, detected_at,"
            " data_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (lead_id, result.company_ids[line], "hiring_delivery_role", role, f"csv:jobs {post}".strip(),
             "Job Role + Job Post (posted in the last 24h per the export)", seen, now(),
             json.dumps({"job_role_title": role, "job_post": post})))
        n += 1
    log_event(conn, "job_signals_added", "import", result.import_id, None, added=n)
    conn.commit()
    return n


def source_rows_cache(source: JobsCsvSource) -> list[SourceRow]:
    if not hasattr(source, "_cache"):
        source._cache = list(source.rows())
    return source._cache


class CachedJobsSource:
    """wraps JobsCsvSource so run_import and add_job_signals see the same rows."""

    def __init__(self, src: JobsCsvSource):
        self.src = src
        self.info = src.info

    def rows(self):
        return iter(source_rows_cache(self.src))
