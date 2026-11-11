"""local business leads from Yelp listings, through Outscraper.

why Outscraper and not Yelp or Google directly: both of those forbid keeping the data.
Yelp's API terms cap storage at 24 hours, ban building your own listing database and ban
commercial use without written consent. Google's Maps terms name "copy and save business
names, addresses, or user reviews" as a prohibited example. Outscraper resells the data
under its own terms. docs/local-business-sourcing.md has the quoted clauses.

api contract verified against the official Outscraper python sdk 6.0.5 on pypi
(outscraper/client.py `yelp_search`, outscraper/transport.py):

    GET {base}/yelp-search
    header X-API-KEY: <key>
    params query (repeatable, Yelp search urls, max 250 per request), limit, async,
           fields, ui, webhook

    more than 10 queries forces async. an async response is {"id": ...}; poll
    GET {base}/requests/{id} every few seconds until status != "Pending", then read .data

dry run is the default everywhere (CLAUDE.md rule 1). nothing is requested and nothing is
spent unless the caller passes live=True, which the cli only does behind --live.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SOURCE = "outscraper_yelp"
YELP_SEARCH = "https://www.yelp.com/search"

# Outscraper passes through Yelp's own keys and has changed them before, so every
# canonical field maps from a list of candidates, best first. unknown keys are reported
# rather than silently dropped, so the first live batch tells us if a name moved.
FIELD_MAP = {
    "business_name": ["name", "business_name", "title"],
    "yelp_url": ["url", "link", "yelp_url", "business_url"],
    "yelp_id": ["id", "business_id", "alias", "yelp_id"],
    "rating": ["rating", "stars", "review_rating"],
    "reviews": ["reviews", "review_count", "reviews_count", "num_reviews"],
    "phone": ["phone", "display_phone", "phone_number", "telephone"],
    "website": ["website", "site", "business_website", "url_website"],
    "category": ["categories", "category", "primary_category"],
    "street": ["address1", "street", "address", "full_address"],
    "city": ["city", "locality"],
    "state": ["state", "region", "state_code"],
    "zip_code": ["zip_code", "zip", "postal_code", "postcode"],
    "country": ["country", "country_code"],
    "latitude": ["latitude", "lat"],
    "longitude": ["longitude", "lng", "lon"],
}


class OutscraperError(RuntimeError):
    pass


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text()) or {}
    for key in ("api", "filters", "target", "niches", "metros"):
        if key not in cfg:
            raise ValueError(f"local_listings config missing '{key}'")
    if cfg["filters"].get("country", "US").upper() != "US":
        raise ValueError("this source is US only; see CLAUDE.md section 8 geography rules")
    return cfg


def search_url(find_desc: str, find_loc: str) -> str:
    """a Yelp search url, which is what the Outscraper endpoint takes as its query."""
    qs = urllib.parse.urlencode({"find_desc": find_desc, "find_loc": find_loc})
    return f"{YELP_SEARCH}?{qs}"


@dataclass
class PlannedQuery:
    niche: str
    metro: str
    url: str


def plan(cfg: dict, niches: list[str] | None = None, max_metros: int | None = None) -> list[PlannedQuery]:
    """every query we would send, in the order we would send it.

    metros are ordered broadest first in config, so stopping early still gives a spread
    across the country rather than ten suburbs of one city.
    """
    chosen = niches or list(cfg["niches"])
    unknown = [n for n in chosen if n not in cfg["niches"]]
    if unknown:
        raise ValueError(f"unknown niches: {unknown}. known: {sorted(cfg['niches'])}")
    metros = cfg["metros"][: max_metros] if max_metros else cfg["metros"]
    out = []
    for niche in chosen:
        desc = cfg["niches"][niche]["find_desc"]
        for metro in metros:
            out.append(PlannedQuery(niche=niche, metro=metro, url=search_url(desc, metro)))
    return out


def _first(raw: dict, candidates: list[str]):
    for key in candidates:
        if key in raw and raw[key] not in (None, "", []):
            return raw[key]
    return None


def _category_text(value) -> str:
    """Yelp categories arrive as a list of dicts, a list of strings or a plain string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("title") or value.get("alias") or "")
    if isinstance(value, list):
        parts = [_category_text(v) for v in value]
        return ", ".join(p for p in parts if p)
    return str(value)


def _number(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().replace(",", "")
    # review counts show up as "128 reviews" often enough to be worth handling
    head = ""
    for ch in text:
        if ch.isdigit() or ch == ".":
            head += ch
        elif head:
            break
    try:
        return float(head) if "." in head else int(head)
    except ValueError:
        return None


def normalize(raw: dict, niche: str, metro: str) -> dict:
    row = {"niche": niche, "search_metro": metro, "source": SOURCE}
    for canon, candidates in FIELD_MAP.items():
        row[canon] = _first(raw, candidates)
    row["category"] = _category_text(row["category"])
    row["rating"] = _number(row["rating"])
    row["reviews"] = _number(row["reviews"])
    for key in ("business_name", "yelp_url", "phone", "website", "street", "city", "state", "zip_code"):
        row[key] = (str(row[key]).strip() if row[key] is not None else "")
    if not row["country"]:
        row["country"] = "US"
    return row


def unmapped_keys(raw: dict) -> list[str]:
    known = {c for cands in FIELD_MAP.values() for c in cands}
    return sorted(k for k in raw if k not in known)


@dataclass
class Rejects:
    counts: dict = field(default_factory=dict)

    def bump(self, reason: str):
        self.counts[reason] = self.counts.get(reason, 0) + 1


def qualifies(row: dict, filters: dict, rejects: Rejects | None = None) -> bool:
    """abdul's icp gate. Yelp search has no server side rating or review filter,
    so this runs locally on every row."""
    def no(reason):
        if rejects is not None:
            rejects.bump(reason)
        return False

    if not row.get("business_name"):
        return no("no_business_name")
    if row.get("rating") is None:
        return no("no_rating")
    if row["rating"] < float(filters["min_rating"]):
        return no("rating_below_min")
    if row.get("reviews") is None:
        return no("no_review_count")
    if row["reviews"] < int(filters["min_reviews"]):
        return no("reviews_below_min")
    if filters.get("require_phone", True) and not row.get("phone"):
        return no("no_phone")
    if filters.get("require_website", False) and not row.get("website"):
        return no("no_website")
    state = (row.get("state") or "").strip()
    if state and len(state) == 2 and not state.isalpha():
        return no("bad_state")
    return True


def dedupe_key(row: dict) -> str:
    """prefer the Yelp id, fall back to url, then name plus city."""
    for key in ("yelp_id", "yelp_url"):
        if row.get(key):
            return f"{key}:{str(row[key]).strip().lower()}"
    return "name:" + f"{row.get('business_name','')}|{row.get('city','')}".strip().lower()


class OutscraperClient:
    """thin client for the one endpoint we need. no sdk dependency: the published
    outscraper 6.0.5 wheel ships a businesses module that imports a schema package it
    does not include, so importing it fails."""

    def __init__(self, api_key: str, cfg: dict, timeout: int = 120):
        if not api_key:
            raise OutscraperError("no api key. set OUTSCRAPER_API_KEY in .env")
        self._key = api_key
        api = cfg["api"]
        self.bases = [api["base_url"], *api.get("fallback_urls", [])]
        self.poll_seconds = int(api.get("poll_seconds", 5))
        self.poll_timeout = int(api.get("poll_timeout_seconds", 1800))
        self.timeout = timeout
        self.calls = 0

    def _open(self, url: str):
        req = urllib.request.Request(url, headers={"X-API-KEY": self._key, "client": "dfy-outbound-engine"})
        last = None
        for _ in self.bases:
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")[:300]
                if e.code in (401, 403):
                    raise OutscraperError(f"auth rejected ({e.code}). check OUTSCRAPER_API_KEY. {body}")
                if e.code == 402:
                    raise OutscraperError(f"out of credits ({e.code}). {body}")
                last = OutscraperError(f"http {e.code}: {body}")
                break
            except (urllib.error.URLError, TimeoutError) as e:
                last = OutscraperError(f"network error: {e}")
                continue
        raise last or OutscraperError("request failed")

    def _url(self, base: str, path: str, params: list[tuple[str, str]]) -> str:
        return f"{base}{path}?{urllib.parse.urlencode(params)}"

    def yelp_search(self, urls: list[str], limit: int) -> list[dict]:
        """one call covering up to 250 Yelp search urls. returns the flat record list."""
        if not urls:
            return []
        if len(urls) > 250:
            raise OutscraperError(f"{len(urls)} queries exceeds the documented 250 per request")
        is_async = len(urls) > 10
        params = [("query", u) for u in urls]
        params += [("limit", str(limit)), ("async", "true" if is_async else "false")]
        payload = self._open(self._url(self.bases[0], "/yelp-search", params))
        self.calls += 1
        if payload.get("error"):
            raise OutscraperError(f"api error: {payload.get('errorMessage')}")
        if is_async:
            return self._await_archive(payload["id"])
        return self._flatten(payload.get("data", []))

    def _await_archive(self, request_id: str) -> list[dict]:
        waited = 0
        while waited < self.poll_timeout:
            time.sleep(self.poll_seconds)
            waited += self.poll_seconds
            result = self._open(f"{self.bases[0]}/requests/{request_id}")
            status = result.get("status")
            if status == "Pending":
                continue
            if status in ("Success", "Finished", "Completed", None):
                return self._flatten(result.get("data", []))
            raise OutscraperError(f"request {request_id} ended as {status}")
        raise OutscraperError(f"request {request_id} still pending after {self.poll_timeout}s")

    @staticmethod
    def _flatten(data) -> list[dict]:
        """data comes back as a list per query. flatten, keeping query order."""
        out = []
        for chunk in data or []:
            if isinstance(chunk, list):
                out.extend(c for c in chunk if isinstance(c, dict))
            elif isinstance(chunk, dict):
                out.append(chunk)
        return out
