# DFY GTM Systems: context for every session

read this before writing anything for abdul. everything public facing follows the voice rules below.

## who
- abdul, founder of DFY GTM Systems
- GTME (go to market engineer) and Clay expert
- sign off as `abdul`. first name only. no last name, no title in signatures.

## offer
end to end outbound automation, done for you.

- leads get sourced and auto-enriched in Clay
- they get scored, personalized and pushed to Instantly or Smartlead
- zero manual work for the client. no csv exports, no copy paste, no VA.

what the client gets:
- Clay tables built and maintained (sourcing, enrichment waterfall, icp scoring, AI personalization)
- sending infra set up (domains, inboxes, warmup, dns)
- sequences written and loaded
- auto push from Clay to Instantly or Smartlead, deduped
- replies routed so they only deal with interested people
- weekly tweaks based on reply data

## pricing
- $2k flat per month
- infra at cost (domains, inboxes, Clay credits, sending tool). no markup.
- say it plainly. never hide the price. never say "starting at".

## icp
agencies and service businesses that sell $2k+ clients.

- marketing, creative, seo, paid media, web/dev agencies
- b2b service businesses: consultants, fractional execs, recruiting firms, dev shops, bookkeeping/cfo firms
- 2 to 50 employees
- founder or ceo still owns sales
- average client worth $2k+ per month or $10k+ per project
- signals: referral dependent, has case studies, hiring sdrs, founder posting about pipeline, tried outbound before and stopped

not a fit:
- ecommerce, b2c, local retail
- offers under $2k
- teams with no case studies or proof yet

## writing rules (hard)
1. lowercase. everything. subject lines too. proper nouns of tools (Clay, Instantly, Smartlead, LinkedIn) can keep their caps.
2. short sentences. one idea per line.
3. casual. write like a text to a founder friend.
4. no em dashes. ever. use a period or a comma.
5. first name only. "abdul", never full name.
6. no fluff openers ("hope you're well", "i wanted to reach out").
7. one cta per message. soft ask.
8. cold emails under 90 words.

do:
> saw you just landed a fintech client. nice.
> most agencies i talk to get 80% of deals from referrals. is that you too?

don't:
> Hi John, I hope this email finds you well — I wanted to reach out regarding our innovative solution.

banned words: synergy, leverage, revolutionize, cutting edge, game changer, unlock, supercharge, seamless, delve, robust, "quick question" as a subject line.

## tool stack
- Clay (sourcing, enrichment, AI columns, routing)
- Instantly and Smartlead (sending)
- email waterfall inside Clay (multiple providers, then validation)

## repo map
- `outbound/sequences.md` cold email sequences + reply handlers
- `clay/table-blueprint.md` column by column Clay build
- `site/index.html` landing page
