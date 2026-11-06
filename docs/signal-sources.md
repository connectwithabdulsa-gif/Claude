# which signals each source really provides

checked november 2026. Explorium from its official v2 docs (developers.explorium.ai).
Clay from the field list of the Clay connector attached to the build session. no real
Clay csv export has been checked yet.

| signal (CLAUDE.md section 5) | Explorium / Vibe Prospecting | Clay | engine class |
|---|---|---|---|
| hiring for sales roles | yes: `hiring_in_sales_department` event, with job_titles, job_count, event_time | yes in Clay search (jobs: job_title, job_posted_date, job_still_open). csv columns depend on the table | hiring_sales |
| new head of sales / growth | yes: `executive_joined_company` (full_name, job_role_title, event_time), `prospect_changed_role` | yes in search (experience start_date). csv depends on table | new_leader |
| funding round | yes: `new_funding_round` (funding_stage, amount_raised, event_time) | "Latest Funding" data point. date field not confirmed | funding |
| outbound tool in stack | technographics enrichment (no date, current) | technographics in search | outbound_tool |
| trade show | **no** | **no** | trade_show (needs a manual or scraped exhibitor list) |
| running paid ads | **no** | **no** | not built (needs an ad library source) |
| expansion | yes: `new_office` (office_location), `new_product` (product_name), `new_partnership` (partner_company) | "Recent News" data point, free text | expansion_office / expansion_product / partnership |
| sales team growing | yes: `increase_in_sales_department` (department_change) | headcount growth fields | sales_team_growth |
| public post about pipeline pain | no (LinkedIn posts enrichment exists, no topic filter) | no | manual only |
| likes / reactions | no | no | post_like (P.S. or LinkedIn only) |

notes
- Explorium events only cover about the last quarter. older history needs separate enrichments.
- every signal is stored with its source, source field and the date it happened. a dated signal
  with no date is expired on refresh, because rule 5 needs a date to prove it is fresh.
- trade shows and paid ads are missing from both sources. the amazon agency niche leans on trade
  shows, so it will run mostly on hiring and expansion signals until an exhibitor list source exists.
