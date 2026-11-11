# sourcing local business leads with ratings and review counts

checked 11 november 2026, against yelp's own live docs and terms. the question asked:
can we pull ~2000 us leads (roofing, medspas, chiropractors, plumbing, hvac) filtered to
4.0+ stars and 30+ reviews, with business name, category, views, location, phone, website
and yelp url, and keep them as a cold outreach list.

## answer on yelp: no. their terms forbid it.

the api is now the "yelp places api". the call limits are fine for 2000 rows. the terms are not.
four separate clauses in the api terms of use block this use, quoted from
terms.yelp.com/developers/ :

- **storage, clause 5a.** "cache, record, pre-fetch, or otherwise store any portion of the
  Yelp Content for a period longer than twenty-four (24) hours from receipt of the Yelp
  Content, or attempt or provide a means to execute any scraping or 'bulk download'
  operations". a stored lead list is storage well past 24 hours. the only carve out is
  storing yelp business ids "solely for back-end matching purposes".
- **own database, clause 5b.** may not "use it to update or create your own database of
  business listing information, unless such modification is for non-commercial analysis".
  a lead table is exactly a database of business listing information.
- **commercial use, clause 5d.** may not "use Yelp Content for any commercial purpose
  without the express written consent of Yelp".
- **the license grant itself** only covers "(ii) display the Yelp Content on Your Site",
  and adds "You may not use the API, Yelp Content, or Yelp Brand Features for any other
  purpose without Yelp's prior written consent."

the non-commercial carve out does not help us. it allows aggregate analysis only, "so long
as the underlying Yelp Content is only displayed in the aggregate as an analytical output,
and not individually", and defines non-commercial use as use that "does not generate
promotional or monetary value for the creator or the user".

scraping yelp.com directly is covered by the same clause 5a wording and is independently
blocked in practice: www.yelp.com returns http 403 to automated requests from this session.

yelp points commercial users at insights-api.yelp.com. that is a sales conversation, not a
self serve key, and pricing is not published.

## two fields are not available from yelp at any price

- **views / page views.** no view count field exists anywhere in the yelp api schema.
  grepping the full business search spec for any `*view*` field returns only `review_count`,
  `review_snippet` and `reviews`. yelp shows page view stats only to the claimed business
  owner inside yelp for business. plan without this column.
- **the business's own website.** also absent from the yelp business schema. yelp returns
  `url`, which is the yelp listing page, not the company's site.

## what yelp's api does return, and its real limits

useful for reference if we ever license it properly.

| item | value |
|---|---|
| endpoint | `GET /v3/businesses/search` |
| `limit` per call | max 50, default 20 |
| `offset` | max 1000, so about 1000 distinct results per unique query |
| fields | id, alias, name, url (yelp page), rating, review_count, price, phone, display_phone, categories (alias + title), location (address1-3, city, state, zip_code, country), coordinates |
| min rating / min review filter | none. server side filtering does not exist, filter locally |
| `sort_by` | best_match, rating, review_count, distance. docs call it "a suggestion (not strictly enforced)", and the rating sort is a bayesian adjusted value, not raw rating |
| `radius` | max 40000 m (25 miles) |
| trial | 5000 calls over 30 days; starter trial capped at 300 calls per 24 hours |
| paid | 30000 calls per month, 5000 per day, raise only via their sales team |

so volume was never the blocker. 2000 rows needs roughly 100 to 150 calls. the terms are
the blocker.

## alternatives, first pass (google now checked, see above)

none of the below has been checked against its own terms yet. do that before building.

- **licensed local business data providers** (data axle, melissa, versium and similar). they
  sell local business records for marketing use, and the licence is the point: lead gen is a
  permitted use rather than a breach. ratings and review counts vary by provider, so confirm
  those two fields exist before buying.
- **google places api (new).** returns rating, user rating count, phone, website and
  category, which covers more of the brief than yelp does. but google maps platform terms
  carry their own caching and no-database restrictions that may bite the same way. must read
  their terms before writing a line of code.
- **paid scraper platforms** (apify, outscraper, serpapi). they will return yelp and google
  maps data including yelp urls. they shift the compliance question onto their own terms
  rather than removing it. read what their licence actually permits for the output.

our existing explorium pipeline cannot do this job at all: its filter schema has no rating
field and no review count field. it is a b2b company database, not a local listings one.

## answer on google places: also no. same outcome, blunter wording.

checked the live google maps platform terms at cloud.google.com/maps-platform/terms.
section 3.2.3 (a), titled "No Scraping":

> "Customer will not export, extract, or otherwise scrape Google Maps Content for use
> outside the Services. For example, Customer will not: (i) pre-fetch, index, store,
> reshare, or rehost Google Maps Content outside the services; (ii) bulk download google
> maps tiles, street view images, geocodes, directions, distance matrix results, roads
> information, places information, elevation values, and time zone details; (iii) copy and
> save business names, addresses, or user reviews; or (iv) use Google Maps Content with
> text-to-speech services."

clause (iii) describes the deliverable exactly: copying and saving business names,
addresses and review data. clause (i) blocks storing it outside their services. so an
exported lead csv is out, even though google does return the business website that yelp
withholds.

paying for the api does not buy this right. the restriction sits in the platform terms that
apply to every paid customer, and there is no stored-list tier.

## openstreetmap: licence is fine, data does not fit, and it is unreachable from here

osm is odbl licensed, so commercial lead gen is permitted with attribution. it carries
roofers, plumbers, hvac contractors and chiropractors with phone and website tags. but it
has no star rating and no review count at all, so it cannot satisfy the 4.0+ / 30+ filter
that defines this icp. separately, overpass-api.de resolves but resets the tls connection
from this container, which public overpass instances commonly do for cloud egress ranges.

## the conclusion across all three

the 4.0+ stars and 30+ reviews filter is the whole problem. ratings and review counts only
exist on the review platforms, and both review platforms contractually forbid extracting
them into a stored list. the open dataset that permits extraction has no ratings. there is
no configuration of free or self-serve apis that produces this asset legitimately.

so the filter has to be bought from someone who already holds the licence to resell it.

## what to actually do

ranked by how clean the licence is.

1. **licensed us local business data providers.** data axle, melissa, versium. they sell
   local business records explicitly for marketing use, which is the point: lead gen is a
   permitted use under the contract rather than a breach of it. confirm in writing that
   rating and review count are included fields before paying, because coverage varies and
   several of these compile firmographics without review data.
2. **review-data resellers.** some vendors licence google review data for resale. ask
   directly whether their contract permits storing it and contacting the listed business.
   get it in the order form, not a sales call.
3. **paid scraper platforms.** apify, outscraper, serpapi. widely used by agencies and they
   will hand over yelp urls and review counts. understand what this is: it moves the
   liability to their terms and their relationship with the platforms, it does not make the
   underlying extraction permitted. abdul's call, not a default.

whichever is chosen, it drops into the existing `DataSource` adapter in
`src/engine/ingest/base.py` the same way the explorium source does, so the scoring,
dedupe, suppression and sequencing all work unchanged.
