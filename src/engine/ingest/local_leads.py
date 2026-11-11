"""drives the Outscraper Yelp source: batches queries, stops at target, writes the csv.

dry run by default. `collect(live=False)` sends nothing and spends nothing; it returns the
query plan and the cost estimate so abdul can approve before a live run (CLAUDE.md rule 8).
"""
import csv
from dataclasses import dataclass, field
from pathlib import Path

from .outscraper_yelp import (
    OutscraperClient,
    Rejects,
    dedupe_key,
    normalize,
    plan,
    qualifies,
    unmapped_keys,
)

CSV_COLUMNS = [
    "business_name", "niche", "category", "rating", "reviews",
    "phone", "website", "yelp_url",
    "street", "city", "state", "zip_code", "country",
    "latitude", "longitude", "search_metro", "source",
]


@dataclass
class CollectResult:
    live: bool
    planned_queries: int = 0
    queries_sent: int = 0
    api_calls: int = 0
    records_received: int = 0
    duplicates: int = 0
    rows: list = field(default_factory=list)
    rejects: Rejects = field(default_factory=Rejects)
    per_niche: dict = field(default_factory=dict)
    unmapped: list = field(default_factory=list)
    stopped_early: dict = field(default_factory=dict)

    @property
    def qualified(self) -> int:
        return len(self.rows)


def estimate(cfg: dict, planned: int, niches: int | None = None) -> dict:
    """two numbers worth seeing before a live run.

    the ceiling is every planned query running to its limit. the likely figure is what we
    actually expect to pull, because each niche stops the moment it hits its target, and
    only a fraction of Yelp listings clear the 4.0 and 30 review gate. the qualify rate is
    a configured assumption, not a measurement: the first live run replaces it.
    """
    limit = int(cfg["api"]["limit_per_query"])
    per_request = int(cfg["api"]["queries_per_request"])
    pricing = cfg.get("pricing") or {}
    rate = pricing.get("price_per_1000_records_usd")
    qualify_rate = float(pricing.get("assumed_qualify_rate") or 0.5)

    niche_count = niches if niches is not None else len(cfg["niches"])
    target = int(cfg["target"]["per_niche"]) * niche_count

    ceiling_records = planned * limit
    likely_records = min(ceiling_records, int(round(target / qualify_rate))) if qualify_rate > 0 else ceiling_records
    likely_queries = min(planned, (likely_records + limit - 1) // limit)

    def cost(records):
        return round(records / 1000 * float(rate), 2) if rate is not None else None

    return {
        "planned_queries": planned,
        "limit_per_query": limit,
        "target_rows": target,
        "assumed_qualify_rate": qualify_rate,
        "likely_queries": likely_queries,
        "likely_records": likely_records,
        "likely_cost_usd": cost(likely_records),
        "ceiling_queries": planned,
        "ceiling_records": ceiling_records,
        "ceiling_cost_usd": cost(ceiling_records),
        "http_calls_ceiling": (planned + per_request - 1) // per_request if planned else 0,
        "rate_per_1000_usd": rate,
    }


def collect(cfg: dict, api_key: str | None = None, live: bool = False,
            niches: list[str] | None = None, max_metros: int | None = None,
            progress=None) -> CollectResult:
    planned = plan(cfg, niches=niches, max_metros=max_metros)
    res = CollectResult(live=live, planned_queries=len(planned))
    if not live:
        return res

    filters = cfg["filters"]
    per_niche_target = int(cfg["target"]["per_niche"])
    batch_size = int(cfg["api"]["queries_per_request"])
    limit = int(cfg["api"]["limit_per_query"])
    client = OutscraperClient(api_key, cfg)
    seen: set[str] = set()
    unmapped: set[str] = set()

    # group by niche so a niche that hits target stops costing money
    by_niche: dict[str, list] = {}
    for q in planned:
        by_niche.setdefault(q.niche, []).append(q)

    for niche, queries in by_niche.items():
        kept = 0
        for start in range(0, len(queries), batch_size):
            if kept >= per_niche_target:
                res.stopped_early[niche] = f"hit target after {start} of {len(queries)} queries"
                break
            batch = queries[start : start + batch_size]
            if progress:
                progress(f"{niche}: querying {len(batch)} metros ({start + len(batch)}/{len(queries)})")
            records = client.yelp_search([q.url for q in batch], limit)
            res.queries_sent += len(batch)
            res.records_received += len(records)

            # the api returns a flat list, so the metro is only reliable per batch,
            # not per record. the record's own city is the authoritative location.
            metro_label = batch[0].metro if len(batch) == 1 else f"{batch[0].metro} +{len(batch)-1}"
            for raw in records:
                unmapped.update(unmapped_keys(raw))
                row = normalize(raw, niche, metro_label)
                if not qualifies(row, filters, res.rejects):
                    continue
                key = dedupe_key(row)
                if key in seen:
                    res.duplicates += 1
                    continue
                seen.add(key)
                res.rows.append(row)
                kept += 1
                if kept >= per_niche_target:
                    break
        res.per_niche[niche] = kept

    res.api_calls = client.calls
    res.unmapped = sorted(unmapped)
    return res


def write_csv(rows: list[dict], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return out_path
