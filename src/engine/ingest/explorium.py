"""Explorium AgentSource v2 client (the API behind Vibe Prospecting).

docs read (developers.explorium.ai, v2):
  overview.md                      base url https://api.explorium.ai, `api_key` header
  businesses/fetch_businesses.md   POST /v2/businesses, 1 credit per returned result,
                                   events filter needs last_occurrence 30 to 120 days
  businesses/fetch_businesses_statistics.md  POST /v2/businesses/stats
  prospects/fetch_prospects.md     POST /v2/prospects, page_size 1 to 500, mode "full"
                                   (preview mode is custom plan only)
  prospects/fetch_prospects_statistics.md    POST /v2/prospects/stats, no credit charges
  prospects/enrichments/contact_information_enrich.md
                                   POST /v2/prospects/contact_information/enrich, up to 50 ids,
                                   email only = 2 credits, unmatched rows not charged,
                                   returns professional_email_status valid / invalid / catch-all
  businesses/events/fetch_events.md  POST /v2/businesses/events, up to 40 business_ids
  prospects/events/fetch_events.md   POST /v2/prospects/events
  credits/per-call-credit-usage.md   header `credit-usage: true` adds credit_usage
                                     {total_results, total_credits} to each response

not stated in the docs: the credit cost of the events endpoints. we read it from
credit_usage on every call and the budget guard stops the run once the cap is hit.
"""
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

BASE_URL = "https://api.explorium.ai"

# documented per-result costs. None = not documented, measured from credit_usage.
COST_PER_RESULT = {
    "/v2/businesses": 1,
    "/v2/prospects": 1,
    "/v2/prospects/contact_information/enrich#email": 2,
    "/v2/businesses/events": None,
    "/v2/prospects/events": None,
    "/v2/businesses/stats": 0,
    "/v2/prospects/stats": 0,
}


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class CreditBudget:
    """hard cap on credits for one run. checks the worst case before each paid call."""
    max_credits: int
    spent: int = 0
    unknown_cost_calls: int = 0
    log: list = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return self.max_credits - self.spent

    def reserve(self, endpoint: str, worst_case: int) -> None:
        if worst_case > self.remaining:
            raise BudgetExceeded(
                f"{endpoint} could cost up to {worst_case} credits, only {self.remaining} left "
                f"of the {self.max_credits} cap. stopping before the call.")

    def record(self, endpoint: str, credits: int | None, fallback: int) -> None:
        used = credits if credits is not None else fallback
        if credits is None:
            self.unknown_cost_calls += 1
        self.spent += used
        self.log.append((endpoint, used, credits is not None))


class Transport(Protocol):
    def post(self, path: str, body: dict) -> dict: ...


class HttpTransport:
    """stdlib http. sends the api key only in the header, never logs it."""

    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout: int = 60, retries: int = 3):
        if not api_key:
            raise ValueError("EXPLORIUM_API_KEY is empty. add it to .env")
        self._key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.retries = retries

    def post(self, path: str, body: dict) -> dict:
        data = json.dumps(body).encode()
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(
                self.base_url + path, data=data, method="POST",
                headers={"api_key": self._key, "Content-Type": "application/json",
                         "Accept": "application/json", "credit-usage": "true"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode() or "{}")
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                detail = e.read().decode(errors="replace")[:300]
                raise RuntimeError(f"explorium {path} http {e.code}: {detail}") from None
        raise RuntimeError(f"explorium {path}: retries exhausted")


def _chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


class ExploriumClient:
    def __init__(self, transport: Transport, budget: CreditBudget | None = None, on_call=None):
        self.t = transport
        self.budget = budget
        self.on_call = on_call or (lambda **kw: None)

    def _call(self, path: str, body: dict, worst_case: int, paid: bool, cost_key: str | None = None):
        if paid:
            if self.budget is None:
                raise BudgetExceeded("paid call without a credit budget")
            self.budget.reserve(path, worst_case)
        resp = self.t.post(path, body)
        usage = resp.get("credit_usage") or {}
        credits = usage.get("total_credits")
        records = usage.get("total_results")
        if paid:
            self.budget.record(path, credits, worst_case)
        self.on_call(endpoint=path, mode="paid" if paid else "free", records=records,
                     credits=credits, correlation_id=(resp.get("response_context") or {}).get("correlation_id"))
        return resp

    # free
    def businesses_stats(self, filters: dict) -> dict:
        return self._call("/v2/businesses/stats", {"filters": filters}, 0, paid=False)

    def prospects_stats(self, filters: dict) -> dict:
        return self._call("/v2/prospects/stats", {"filters": filters}, 0, paid=False)

    # paid
    def fetch_businesses(self, filters: dict, page_size: int) -> dict:
        body = {"mode": "full", "page_size": page_size, "page": 1, "filters": filters}
        return self._call("/v2/businesses", body, worst_case=page_size, paid=True)

    def fetch_prospects(self, filters: dict, page_size: int) -> dict:
        body = {"mode": "full", "page_size": page_size, "page": 1, "filters": filters}
        return self._call("/v2/prospects", body, worst_case=page_size, paid=True)

    def enrich_emails(self, prospect_ids: list[str]) -> list[dict]:
        out = []
        for chunk in _chunks(prospect_ids, 50):
            body = {"prospect_ids": chunk, "parameters": {"contact_types": ["email"]}}
            resp = self._call("/v2/prospects/contact_information/enrich", body,
                              worst_case=2 * len(chunk), paid=True)
            out.extend(resp.get("data") or [])
        return out

    def business_events(self, business_ids: list[str], event_types: list[str],
                        timestamp_from: str, worst_case_per_call: int) -> list[dict]:
        """worst_case_per_call is our own reserve, since the docs give no price for events."""
        out = []
        for chunk in _chunks(business_ids, 40):
            body = {"business_ids": chunk, "event_types": event_types, "timestamp_from": timestamp_from}
            resp = self._call("/v2/businesses/events", body, worst_case=worst_case_per_call, paid=True)
            out.extend(resp.get("output_events") or resp.get("data") or [])
        return out
