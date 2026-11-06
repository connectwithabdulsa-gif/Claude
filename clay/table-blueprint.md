# Clay table blueprint

the build behind the offer. leads come in, get enriched, scored, personalized and pushed to Instantly or Smartlead. no human touches a row.

two tables: **companies** then **people**. companies filters, people sends.

---

## table 1: companies

### 1. sourcing
pick one or stack them:
- Clay "find companies" with filters: industry = marketing & advertising, it services, management consulting, staffing & recruiting, accounting. headcount 2 to 50. country = your target geo.
- LinkedIn sales nav search url import
- csv of agency directories (clutch, agency spotter) if a client wants a niche

dedupe on `domain`.

### 2. enrichment columns
| column | source | notes |
|---|---|---|
| `domain` | sourcing | normalize, strip www |
| `company_name` | enrich company | clean name, drop "llc", "inc" |
| `headcount` | enrich company | |
| `industry` | enrich company | |
| `linkedin_url` | enrich company | |
| `site_text` | scrape website (home + /services + /pricing + /work) | feeds AI columns |
| `has_case_studies` | formula on site_text | contains "case study", "results", "our work" |
| `pricing_signal` | AI column | see prompt below |
| `hiring_sales` | job postings enrichment | sdr, bdr, account exec, "business development" |
| `sending_tool` | tech stack enrichment | instantly, smartlead, lemlist, apollo = tried outbound |

### 3. AI: pricing signal
```
read this website text: {{site_text}}

estimate the typical deal size for this company's clients.
answer with one of: under_2k, 2k_to_10k, over_10k, unknown.
look for retainers, project minimums, pricing pages, "starting at", enterprise logos.
return only the label.
```

### 4. AI: niche label
```
read this website text: {{site_text}}

describe what this company is in 2 to 4 lowercase words a founder would use.
examples: "seo agency", "fractional cfo firm", "shopify dev shop", "tech recruiting firm".
no punctuation. return only the label.
```

### 5. AI: icp score (0 to 100)
```
score this company 0 to 100 as a fit for done for you outbound.

fit = agency or b2b service business, 2 to 50 people, clients worth $2k+,
has proof (case studies, logos), founder likely still selling.
bad fit = ecommerce, b2c, local retail, offers under $2k, no proof.

data:
niche: {{niche_label}}
headcount: {{headcount}}
deal size: {{pricing_signal}}
case studies: {{has_case_studies}}
hiring sales: {{hiring_sales}}
site text: {{site_text}}

return only the number.
```

### 6. filter + send to people table
- run condition: `icp_score >= 70` and `pricing_signal != under_2k`
- "find people at company" with titles: founder, co-founder, ceo, owner, managing director, managing partner, president. limit 2 per company.
- write rows to table 2.

---

## table 2: people

### 1. person fields
`first_name`, `last_name`, `title`, `linkedin_url`, `domain`, `company_name`, plus lookups back to company row (`niche_label`, `icp_score`, `site_text`, `sending_tool`).

### 2. email waterfall
run providers in order, stop at first hit:
1. provider a (cheapest first)
2. provider b
3. provider c
4. pattern guess from domain

then **validate** (separate column). keep `valid` only. send `catch_all` to a separate low volume campaign or drop. never send `invalid` or `unknown`.

run condition on each step: previous step empty. saves credits.

### 3. AI: personalization line
```
you write the first line of a cold email for abdul.

rules:
- lowercase
- one sentence, under 20 words
- casual, like a text to a founder
- no em dashes, no flattery, no "i noticed", no "i came across"
- reference something specific and real from their site or linkedin
- if nothing specific, write about their niche instead

person: {{first_name}}, {{title}} at {{company_name}}
niche: {{niche_label}}
site text: {{site_text}}
linkedin headline: {{headline}}

return only the line.
```

add a formula column `personalization_line_clean` that strips em dashes, trailing quotes and forces lowercase.

### 4. AI: case client (optional)
```
from this website text, return the name of one client they've worked with.
if none found, return "a client like your best one".
text: {{site_text}}
```

### 5. sequence routing
formula column `campaign`:
- `sending_tool` not empty or past sdr hiring → `seq_c_burned`
- niche contains agency, marketing, creative, seo, design, media → `seq_a_agency`
- else → `seq_b_services`

### 6. push to sending tool
- Instantly: "add lead to campaign" action. map campaign by `campaign` column. pass custom vars `niche`, `personalization_line`, `case_client`, `company`.
- Smartlead: "add lead to campaign" action, same mapping.
- run condition: `email_status = valid` and `personalization_line_clean` not empty and `pushed_at` empty.
- write back `pushed_at` timestamp so rows never push twice.
- skip if domain already in a client do-not-contact list (lookup to a `dnc` table).

### 7. automation
- schedule sourcing table to refresh weekly
- auto-run every column on new rows
- auto-push on, so new leads land in campaigns with zero manual work

---

## infra
| item | setup | rough cost (passed through at cost) |
|---|---|---|
| secondary domains | 3 to 5 lookalike domains, forward to main site | ~$10 to $15 each per year |
| inboxes | 2 to 3 per domain, google or outlook | ~$3 to $7 each per month |
| dns | spf, dkim, dmarc on every domain | free |
| warmup | 14 to 21 days inside Instantly or Smartlead before sending | included in tool |
| sending tool | Instantly or Smartlead | ~$40 to $100/mo |
| Clay | plan sized to volume | depends on credits |

sending limits:
- 25 to 35 emails per inbox per day
- 10 to 15 inboxes = roughly 300 to 500 new leads a day
- keep bounce under 2%. if it spikes, pause and check the waterfall.

## weekly maintenance
- check reply rate by campaign and by niche
- kill the worst step 1 variant, write a new one
- tighten icp score threshold if positive reply rate drops
- refresh dnc list with current clients and open deals
