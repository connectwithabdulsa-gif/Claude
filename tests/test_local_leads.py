"""tests for the Outscraper Yelp local business source."""
import csv
import urllib.parse
from pathlib import Path

import pytest

from engine.ingest import local_leads
from engine.ingest.outscraper_yelp import (
    OutscraperClient,
    OutscraperError,
    Rejects,
    dedupe_key,
    load_config,
    normalize,
    plan,
    qualifies,
    search_url,
    unmapped_keys,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg():
    return load_config(ROOT / "config" / "local_listings.yaml")


# ---------- config ----------

def test_config_has_abdul_icp(cfg):
    assert cfg["filters"]["min_rating"] == 4.0
    assert cfg["filters"]["min_reviews"] == 30
    assert cfg["filters"]["country"] == "US"
    assert cfg["target"]["total"] == 2000
    assert set(cfg["niches"]) == {"roofing", "medspas", "chiropractors", "plumbing", "hvac"}


def test_config_rejects_non_us(tmp_path, cfg):
    import yaml
    cfg["filters"]["country"] = "DE"
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match="US only"):
        load_config(p)


def test_config_missing_section(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("api: {}\n")
    with pytest.raises(ValueError, match="missing"):
        load_config(p)


# ---------- query building ----------

def test_search_url_encodes_desc_and_loc():
    url = search_url("Heating & Air Conditioning/HVAC", "Dallas, TX")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["find_desc"] == ["Heating & Air Conditioning/HVAC"]
    assert q["find_loc"] == ["Dallas, TX"]
    assert url.startswith("https://www.yelp.com/search?")


def test_plan_is_niches_times_metros(cfg):
    p = plan(cfg, niches=["roofing", "hvac"], max_metros=5)
    assert len(p) == 10
    assert {q.niche for q in p} == {"roofing", "hvac"}
    # metro order is preserved so stopping early still spreads across the country
    assert [q.metro for q in p[:5]] == cfg["metros"][:5]


def test_plan_rejects_unknown_niche(cfg):
    with pytest.raises(ValueError, match="unknown niches"):
        plan(cfg, niches=["dentists"])


# ---------- normalizing ----------

def test_normalize_primary_field_names():
    raw = {
        "name": "Ace Roofing", "url": "https://www.yelp.com/biz/ace-roofing-dallas",
        "id": "abc123", "rating": 4.5, "reviews": 212,
        "phone": "+12145550100", "website": "https://aceroofing.com",
        "categories": [{"alias": "roofing", "title": "Roofing"}],
        "address1": "100 Main St", "city": "Dallas", "state": "TX", "zip_code": "75201",
    }
    row = normalize(raw, "roofing", "Dallas, TX")
    assert row["business_name"] == "Ace Roofing"
    assert row["rating"] == 4.5 and row["reviews"] == 212
    assert row["category"] == "Roofing"
    assert row["yelp_url"].endswith("ace-roofing-dallas")
    assert row["niche"] == "roofing" and row["country"] == "US"


def test_normalize_falls_back_to_alternate_names():
    """Outscraper has renamed Yelp passthrough keys before; fallbacks must hold."""
    raw = {"business_name": "Bee Plumbing", "business_url": "https://yelp.com/biz/bee",
           "stars": "4.0", "review_count": "128 reviews",
           "display_phone": "(214) 555-0101", "site": "bee.example.com",
           "category": "Plumbing", "locality": "Austin", "region": "TX"}
    row = normalize(raw, "plumbing", "Austin, TX")
    assert row["business_name"] == "Bee Plumbing"
    assert row["rating"] == 4.0
    assert row["reviews"] == 128
    assert row["phone"] == "(214) 555-0101"
    assert row["city"] == "Austin" and row["state"] == "TX"


def test_normalize_category_shapes():
    for value, want in [
        ("Roofing", "Roofing"),
        (["Roofing", "Gutters"], "Roofing, Gutters"),
        ([{"title": "Medical Spas"}], "Medical Spas"),
        ([{"alias": "chiropractors"}], "chiropractors"),
        (None, ""),
    ]:
        assert normalize({"categories": value}, "n", "m")["category"] == want


def test_normalize_missing_everything_does_not_crash():
    row = normalize({}, "hvac", "Reno, NV")
    assert row["business_name"] == ""
    assert row["rating"] is None and row["reviews"] is None


def test_unmapped_keys_flags_new_columns():
    assert "some_new_field" in unmapped_keys({"name": "x", "some_new_field": 1})
    assert unmapped_keys({"name": "x", "rating": 4.5}) == []


# ---------- the icp gate ----------

def good_row(**over):
    row = {"business_name": "Ace", "rating": 4.5, "reviews": 50,
           "phone": "+12145550100", "website": "https://ace.com", "state": "TX"}
    row.update(over)
    return row


def test_qualifies_accepts_a_good_row():
    assert qualifies(good_row(), {"min_rating": 4.0, "min_reviews": 30, "require_phone": True})


@pytest.mark.parametrize("over,reason", [
    ({"rating": 3.9}, "rating_below_min"),
    ({"reviews": 29}, "reviews_below_min"),
    ({"rating": None}, "no_rating"),
    ({"reviews": None}, "no_review_count"),
    ({"phone": ""}, "no_phone"),
    ({"business_name": ""}, "no_business_name"),
])
def test_qualifies_rejects_and_records_reason(over, reason):
    rej = Rejects()
    filters = {"min_rating": 4.0, "min_reviews": 30, "require_phone": True}
    assert not qualifies(good_row(**over), filters, rej)
    assert rej.counts == {reason: 1}


def test_boundary_values_are_inclusive():
    filters = {"min_rating": 4.0, "min_reviews": 30, "require_phone": True}
    assert qualifies(good_row(rating=4.0, reviews=30), filters)


def test_website_optional_unless_required():
    base = {"min_rating": 4.0, "min_reviews": 30, "require_phone": True}
    assert qualifies(good_row(website=""), base)
    assert not qualifies(good_row(website=""), {**base, "require_website": True})


# ---------- dedupe ----------

def test_dedupe_prefers_yelp_id_then_url_then_name():
    assert dedupe_key({"yelp_id": "AbC"}) == "yelp_id:abc"
    assert dedupe_key({"yelp_url": "https://Yelp.com/biz/X"}) == "yelp_url:https://yelp.com/biz/x"
    assert dedupe_key({"business_name": "Ace", "city": "Dallas"}) == "name:ace|dallas"


def test_same_business_twice_is_one_key():
    a = {"yelp_id": "x1", "business_name": "Ace Roofing"}
    b = {"yelp_id": "X1", "business_name": "Ace Roofing LLC"}
    assert dedupe_key(a) == dedupe_key(b)


# ---------- estimate and dry run ----------

def test_dry_run_sends_nothing(cfg):
    res = local_leads.collect(cfg)
    assert res.live is False
    assert res.queries_sent == 0 and res.api_calls == 0 and res.rows == []
    assert res.planned_queries == len(cfg["niches"]) * len(cfg["metros"])


def test_estimate_likely_is_far_below_ceiling(cfg):
    est = local_leads.estimate(cfg, 755)
    assert est["target_rows"] == 2000
    assert est["likely_records"] == 4000          # 2000 target at a 50% qualify rate
    assert est["likely_records"] < est["ceiling_records"]
    assert est["likely_cost_usd"] is None         # no rate configured yet, must not be guessed


def test_estimate_prices_when_rate_is_set(cfg):
    cfg["pricing"]["price_per_1000_records_usd"] = 3.0
    est = local_leads.estimate(cfg, 755)
    assert est["likely_cost_usd"] == pytest.approx(12.0)


# ---------- client guards, no network ----------

def test_client_needs_a_key(cfg):
    with pytest.raises(OutscraperError, match="no api key"):
        OutscraperClient("", cfg)


def test_client_rejects_over_250_queries(cfg):
    client = OutscraperClient("k", cfg)
    with pytest.raises(OutscraperError, match="250"):
        client.yelp_search([f"u{i}" for i in range(251)], 50)


def test_client_empty_query_list_is_a_noop(cfg):
    assert OutscraperClient("k", cfg).yelp_search([], 50) == []


def test_flatten_handles_list_per_query_and_bare_dicts():
    f = OutscraperClient._flatten
    assert f([[{"a": 1}, {"a": 2}], [{"a": 3}]]) == [{"a": 1}, {"a": 2}, {"a": 3}]
    assert f([{"a": 1}]) == [{"a": 1}]
    assert f(None) == []
    assert f([[], None]) == []


# ---------- collect, with a stubbed client ----------

def test_collect_stops_at_target_dedupes_and_filters(cfg, monkeypatch):
    # 4, not 3: with 3 the target is met before the repeated id is ever seen,
    # so the dedupe path would not be exercised at all
    cfg["target"]["per_niche"] = 4
    cfg["api"]["queries_per_request"] = 2
    cfg["metros"] = cfg["metros"][:6]

    def fake_search(self, urls, limit):
        self.calls += 1
        out = []
        for i, _ in enumerate(urls):
            out.append({"name": f"Good {self.calls}-{i}", "id": f"g{self.calls}-{i}",
                        "rating": 4.6, "reviews": 80, "phone": "+1214555", "city": "X"})
            out.append({"name": "Low rated", "id": f"low{self.calls}-{i}",
                        "rating": 3.2, "reviews": 900, "phone": "+1214555", "city": "X"})
            out.append({"name": "Dupe", "id": "SAME",
                        "rating": 5.0, "reviews": 400, "phone": "+1214555", "city": "X"})
        return out

    monkeypatch.setattr(OutscraperClient, "yelp_search", fake_search)
    res = local_leads.collect(cfg, api_key="k", live=True, niches=["roofing"])

    assert res.qualified == 4                      # capped at the per niche target
    assert res.rejects.counts.get("rating_below_min")
    assert res.duplicates >= 1                     # the repeated "SAME" id
    assert res.per_niche["roofing"] == 4
    assert res.queries_sent < len(cfg["metros"])   # stopped before burning every metro


def test_collect_reports_unmapped_columns(cfg, monkeypatch):
    cfg["target"]["per_niche"] = 1
    cfg["metros"] = cfg["metros"][:1]
    cfg["api"]["queries_per_request"] = 1

    def fake(self, urls, limit):
        self.calls += 1
        return [{"name": "Ace", "id": "a", "rating": 4.5, "reviews": 44,
                 "phone": "+1", "brand_new_key": "surprise"}]

    monkeypatch.setattr(OutscraperClient, "yelp_search", fake)
    res = local_leads.collect(cfg, api_key="k", live=True, niches=["roofing"])
    assert "brand_new_key" in res.unmapped


# ---------- csv ----------

def test_write_csv_has_every_requested_column(tmp_path):
    rows = [normalize({"name": "Ace", "rating": 4.5, "reviews": 60, "phone": "+1",
                       "website": "https://a.com", "url": "https://yelp.com/biz/a",
                       "categories": "Roofing", "city": "Dallas", "state": "TX"},
                      "roofing", "Dallas, TX")]
    out = local_leads.write_csv(rows, tmp_path / "x.csv")
    got = list(csv.DictReader(out.open()))
    assert len(got) == 1
    for col in ("business_name", "niche", "category", "rating", "reviews",
                "phone", "website", "yelp_url", "city", "state"):
        assert col in got[0]
    assert got[0]["business_name"] == "Ace"
    assert "views" not in got[0]      # not available from Yelp, deliberately absent
