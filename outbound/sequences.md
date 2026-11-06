# outbound sequences

ready to load into Instantly or Smartlead. merge vars come from the Clay table (see `clay/table-blueprint.md`).

vars used:
- `{{first_name}}`
- `{{company}}`
- `{{niche}}` short label from Clay AI column, e.g. "seo agency", "fractional cfo firm"
- `{{personalization_line}}` one line from Clay AI column
- `{{case_client}}` optional, a client name scraped from their site

rules: lowercase, no em dashes, under 90 words, one soft ask. sign off `abdul`.

---

## sequence a: marketing and creative agencies

**step 1, day 0**
subject: `{{company}} pipeline`

hey {{first_name}},

{{personalization_line}}

most agency founders i talk to get 80% of new clients from referrals. great until it isn't.

i build outbound systems that run on their own. leads get found and enriched in Clay, personalized, and pushed straight into Instantly. you just answer replies.

worth a look for {{company}}?

abdul

**step 2, day 3**
subject: re: `{{company}} pipeline`

{{first_name}}, quick add.

the part people hate about outbound is the busywork. pulling lists, cleaning emails, writing first lines.

none of that touches your team. it all runs in the background.

want me to send a 2 min loom of how it works?

abdul

**step 3, day 7**
subject: `ideal client for {{company}}`

if you could add 3 more clients like {{case_client}} next quarter, would that matter?

that's usually what one booked call a week turns into for a {{niche}}.

$2k a month flat. infra at cost. no long contract.

open to a 15 min chat?

abdul

**step 4, day 12**
subject: `closing the loop`

{{first_name}}, i'll stop here.

if outbound ever lands on the list, just reply "later" and i'll check back in a couple months.

abdul

---

## sequence b: b2b service businesses
(consultants, fractional execs, recruiting, dev shops, bookkeeping)

**step 1, day 0**
subject: `new clients for {{company}}`

hey {{first_name}},

{{personalization_line}}

you sell high ticket work. one new client probably covers a few months of anything you spend on growth.

i set up outbound that runs itself. Clay finds and enriches the right people, then it pushes into Smartlead or Instantly. zero manual work on your end.

open to seeing how it'd look for {{company}}?

abdul

**step 2, day 3**
subject: re: `new clients for {{company}}`

{{first_name}}, most {{niche}} owners still do sales between client work.

that's the problem. pipeline only happens when you have time.

this runs every day whether you're busy or not.

want the short breakdown?

abdul

**step 3, day 7**
subject: `the math`

simple version.

$2k a month for the system. infra at cost.

if your clients are worth $2k+ a month, one new deal pays for it. everything after is margin.

worth 15 min?

abdul

**step 4, day 12**
subject: `last one`

{{first_name}}, not going to keep filling your inbox.

if timing's off, reply "later" and i'll circle back.

abdul

---

## sequence c: tried outbound before, got burned

use when Clay flags: had sdr job posts in the past, uses a sending tool, or founder posted about outbound not working.

**step 1, day 0**
subject: `outbound round 2`

hey {{first_name}},

{{personalization_line}}

guessing you've tried outbound before. bought a list, hired a VA, maybe an agency. bounces, spam, nothing booked.

usually it's not outbound. it's bad data and no system.

i fix both. Clay waterfall for clean emails, real personalization, auto push to Instantly or Smartlead.

worth another shot?

abdul

**step 2, day 3**
subject: re: `outbound round 2`

{{first_name}}, the 3 things that usually killed it:

1. stale lists, high bounces
2. generic copy
3. someone had to babysit it

all 3 are automated now. nothing to babysit.

want me to show you?

abdul

**step 3, day 7**
subject: `no lock in`

flat $2k a month. infra at cost, no markup.

you own the domains, inboxes and Clay tables. if you leave, you keep everything.

up for a quick call?

abdul

**step 4, day 12**
subject: `all good`

{{first_name}}, i'll leave it here.

if you want to give outbound another run later, just reply and i'll pick it up.

abdul

---

## reply handlers

**interested**

nice. here's my calendar: {{calendar_link}}

grab any 15 min slot. i'll come with a rough build for {{company}}.

abdul

**"send more info"**

sure. short version:

- Clay finds and enriches your ideal buyers
- AI writes the first line
- it all pushes to Instantly or Smartlead on its own
- you get replies, nothing else

$2k a month flat, infra at cost.

easier to show than explain. 15 min this week? {{calendar_link}}

abdul

**"not now"**

totally fair. i'll check back in 60 days.

if something changes before then, just reply here.

abdul
