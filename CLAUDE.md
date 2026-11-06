# DFY GTM Systems: Outbound Engine

This file is the project brain. Read it fully at the start of every session, then read `message-library.md`. Follow both unless Abdul says otherwise in chat.

## 1. Who we are, why we reach out, how we help

**Who we are.** Abdul Sami, founder of DFY GTM Systems. A GTM engineer who builds outbound systems in Clay for B2B companies and runs them for the client.

**Why we reach out.** Only because of something real and recent about the prospect (a signal). Every first email says what we saw and why that made us write now. No real signal and weak fit means we do not write.

**How we help, in plain words.** We build and run the whole system: find the right people, check the data, write each message from real facts, send from separate domains so the client's main domain stays safe, handle replies, and the client takes the calls. Core selling point: complete end to end workflow automation where leads auto enrich in Clay and push to the sender with zero manual work.

**What we are not.** Not a lead list seller. Not a mass blast. Never pitch "lead lists".

## 2. Business facts

- Owner: Abdul Sami. Positioning: "GTME & Clay Expert".
- Standard price: $2,000 per month flat per client, plus infrastructure (domains, mailboxes, tools, data credits) billed at cost.
- Markets: US, UK, Australia first. DACH is OFF by default (see Compliance).
- Goal this month: book calls and close 3 to 4 clients.
- Booking link: https://calendly.com/connectwith-abdulsa/new-meeting
- Website: dfygtmsystems.com

### Proof points (use only these, never invent new ones)
- Ran a cold email campaign for an Amazon agency: lead data from SmartScout and Jungle Scout, enrichment in Clay with Claygent for domains, decision makers and signals, email verification waterfall, personalized emails, sent through Smartlead.
- Abdul has stated bigger numbers (volume, reply rates, client counts). Do NOT put any specific number or client name into outbound copy unless Abdul confirms in chat that he can back it up and has permission. Default wording: "we did this for an amazon agency".
- Do NOT state timelines ("live in 2 weeks") in copy until Abdul confirms them. New sending domains need about 3 weeks of warmup, so speed claims can easily be false.

## 3. Hard rules (never break these)

1. DRY RUN by default. Nothing is sent, posted or written to an outside service unless the command has an explicit `--live` flag AND Abdul approved it in chat in this session.
2. Never send cold email from Abdul's main Gmail or main domain. Cold sending uses separate secondary domains and mailboxes only.
3. No LinkedIn automation of any kind. The LinkedIn module only produces a daily list for Abdul to send by hand (max 20 new connection requests per day).
4. Never auto reply to a human. Draft replies and wait for approval. Exception: honoring an unsubscribe, which is automatic and immediate.
5. Never invent facts about a prospect. Every personalized claim must trace to a real data field with a source and a date. If the data is weak, use the plain fallback line.
6. Never guess API endpoints, fields or limits for Clay, Vibe Prospecting, Spacemail, Smartlead, Instantly, verification tools or anything else. Read the official docs first and say which doc you used.
7. Secrets live in `.env` only. Never print, log or commit keys or passwords. Keep `.env` in `.gitignore`.
8. Ask before any action that spends money (API credits, verification, enrichment). Give a cost estimate first.
9. Respect the suppression list: existing clients, people already in conversation, unsubscribes, bounces, complaints, competitors, and any domain Abdul adds.
10. Stop and report if bounce rate, complaint rate or any error pattern crosses the thresholds in section 9.
11. Never fill a daily quota with weak leads. If fewer than the target qualify, send fewer.

## 4. How outbound works (research basis)

Summary of what current public sources say. Most numbers come from vendor blogs (Autobound, Prospeo, Smartlead, Instantly and similar), so treat them as direction, not promises. We measure our own results and trust those over any blog.

- **Signals beat generic outreach.** Signal based emails are reported to reply several times better than generic ones. Act on signals that are recent (ideally within 7 days, no more than 30) and specific enough to mention in one sentence.
- **Use at most two signals per email.** One is better. Stacking weak signals reads like a template.
- **Strongest signals for our offer:** hiring for sales or outbound roles, a new head of sales or growth, a recent funding round, an outbound tool in their tech stack. **Weakest:** likes and reactions on social posts. Use those only as a P.S. or on LinkedIn.
- **Short first emails.** 50 to 90 words, one clear ask. Longer emails reply less.
- **Timeline style asks** (a specific 15 minute slot) are reported to book more meetings than open problem questions. We A/B test this against our one page map offer.
- **Follow ups must add something new.** Most replies come within 3 days of an email, so space emails at least 3 days apart. Each follow up shares a new concrete detail. "Just bumping this" adds nothing and is banned.
- **Sequence length:** 4 emails. Reply rate falls at every step, and the first email gets the most replies.
- **Smaller, tighter lists reply better** than big loose ones. Quality over volume.
- **Deliverability:** SPF, DKIM and DMARC on every sending domain. Gmail and Yahoo expect spam complaints well under 0.1% and never 0.3%. Keep bounces under 2%. Warm new mailboxes for 3 weeks or more. A visible opt out line and a list-unsubscribe header are standard.
- **AI sounding copy gets ignored.** Patterns that read as generated: generic openers, vague value claims, soft personalization that fits anyone, multiple asks, perfectly even paragraphs, and near identical wording across thousands of sends.

## 5. Signals playbook

Detect each signal from a real data field. Store the signal, its source, its date and the exact text or value. Check which signal types Clay and Vibe Prospecting actually provide before building anything.

| Signal | Where to find it | Use window | Why it matters to us | Strength |
|---|---|---|---|---|
| Hiring for sales or outbound roles (SDR, BDR, AE, growth, demand gen) | Company careers page, job boards, LinkedIn jobs, Clay or Vibe job data | Post still open, first seen within 30 days | They are about to spend on outbound and a new hire takes time to ramp | High |
| New head of sales, growth or marketing | LinkedIn, Clay or Vibe role change data | Joined within 90 days, best within 30 | New leaders move fast on pipeline | High |
| Funding round announced | Vibe Prospecting events, news, Crunchbase style sources | Within 60 days | Founder led sales gets stretched after a round | Medium to high |
| Outbound tools in their stack (Smartlead, Instantly, Apollo, Outreach and similar) | Tech stack data in Clay or Vibe. Only use if the field confirms it | Current | They already do outbound and may have gaps in data and follow up | Medium |
| Trade show exhibitor or attendee | Exhibitor lists, event sites | Event within 60 days before or after | Manual outreach at shows does not scale. Strong for Amazon and ecommerce agencies | Medium to high |
| Running paid ads for lead generation | Ad libraries, ad data | Active now | They already pay for leads and may want a cheaper channel | Medium |
| Expansion: new office, state, service line, new website | News, careers page, company site | Within 60 days | New market with no pipeline yet | Medium |
| Public post about pipeline or lead gen pain | LinkedIn, manual only | Within 14 days | Direct pain, but tiny sample and manual | Use for tier A LinkedIn notes |
| Likes or reactions on posts | LinkedIn | Within 24 hours | Weak on its own | Low, P.S. only |

Signal rules:
- One sentence in the email, plain statement of what we saw.
- Always follow it with why it matters to them, in one short sentence.
- Skip the signal if it is older than its window. Use the plain fallback instead.
- Never mention anything that is not in the signal field (no "loved your blog post").

## 6. Offers

Default call to action in email 1 is the free one page map (Offer 0). Offers marked NEEDS DECISION are not used in any copy until Abdul approves the exact wording and numbers in chat.

**Offer 0: free system map (ready).** After a positive reply, Abdul builds a one page Miro map of how outbound would run for that company: who gets targeted, which signals, how messages are written, how sending works, how replies reach them. No cost to them. It shows the work before they pay.

**Offer 1: standard engagement (ready).** $2,000 per month flat for setup, running, optimization and weekly reporting. Infrastructure billed at cost. Month to month with 30 days notice. Explain infra cost before the fee on calls, and cut scope before cutting price.

**Offer 2: capped meeting guarantee (NEEDS DECISION).** Example shape: "if we do not book N qualified meetings in the first 90 days after sending goes live, we keep running month 4 at no fee." Conditions must be written down: what counts as a qualified meeting (fits the agreed ICP and shows up), client approves ICP and offer within a set number of days, client takes the calls, minimum send volume is reached, client side tool or domain problems are excluded. Never offer an uncapped "free until X meetings". Guarantees attract buyers who ask for refunds, so only offer it where we can realistically deliver.

**Offer 3: hybrid (NEEDS DECISION).** Smaller retainer plus a fee per qualified meeting. Only for clients who insist on performance pricing. It squeezes margin when deliverability slips.

**Offer 4: sample run (use sparingly).** For warm tier A leads only, Abdul runs a small proof of work (for example 20 well matched targets with the signal behind each). Costs time and credits, so Abdul approves each one.

## 7. Writing rules (anti AI)

The goal is a message that reads like a founder typing it in two minutes.

- Voice reference, from Abdul's real messages: "hey jake, great to connect. yeah, absolutely." Casual, short, direct.
- Lowercase sentences. Capitalize first names, company names and proper nouns. First name only.
- Plain words. Say what we do the simplest way: "i set up the system that finds the right people, writes to them and sends it, so you only take the calls."
- No em dashes. No emojis. No exclamation marks. No dramatic colons.
- No stacks of three adjectives or three benefits. No "not just X but Y". No rhetorical question openers. No perfectly balanced paragraphs.
- Vary the sentence length. A fragment is fine. Do not make every email follow identical wording. Rotate 3 to 5 variants per signal so thousands of sends do not look alike.
- **Banned words and phrases:** hope this finds you well, i wanted to reach out, reaching out, i came across, i noticed that, touch base, circle back, quick question (as a subject), delve, leverage, unlock, elevate, seamless, robust, streamline, game changer, cutting edge, innovative, tailored, solutions, synergy, revolutionize, supercharge, empower, scale (as a verb), passionate, excited to, best in class, turnkey, AI powered, "just checking in", "just bumping this".
- Subject lines: plain and specific, 2 to 6 words, lowercase, under 50 characters. No tricks, no fake "re:" or "fwd:", no all caps.
- Email 1 is 50 to 90 words, plain text, no links, no images, no attachments, one ask.
- Description of Abdul: GTM Engineer who builds automation systems. Focus on Clay workflows, outbound automation, data pipelines. Company name is exactly "DFY GTM Systems" (lowercase "dfy gtm systems" in sign offs is fine).
- Every email footer: a plain opt out line ("not for you? just say so and i'll stop.") and the physical mailing address (placeholder `[ADD PHYSICAL ADDRESS]` until Abdul provides it. No live sending until it is filled).

**Pre send QA check for every message** (a second Claude pass, with a reason for each fail):
1. Would a person read this in 10 seconds and know why they got it?
2. Does it say what we saw and why now, from a real field?
3. Is there exactly one ask?
4. 90 words or fewer for email 1?
5. Any banned word or pattern?
6. Could this be sent to anyone without changes? If yes, fail it.
7. Is every fact traceable to a stored field?
Failed lines fall back to the plain line or are held for Abdul to review.

## 8. Niches (test in order, measure, double down)

Each niche is a hypothesis. Send at least 300 emails per niche before judging. Track reply rate, positive reply rate and calls booked per niche and per signal.

| # | Niche | Who to target | Main signals | Pain angle |
|---|---|---|---|---|
| 1 | Amazon and ecommerce agencies | Founder, owner, head of growth | Trade show activity, hiring account managers, new service launch | Clients come from referrals or manual outreach and it does not scale. We have a real case study. |
| 2 | B2B SaaS founders (seed to Series A) | Founder, CEO, head of growth | Funding, first sales hire, SDR or growth job posts | Founder is the only seller and pipeline stops when they stop. |
| 3 | Financial industry (mortgage brokers, wealth and advisory firms, insurance agencies, business lenders, fintech) | Owner, managing partner, head of growth | Hiring loan officers or advisors, running ads, new office or state | Paid leads are expensive, referrals are unpredictable. |
| 4 | Recruiting and staffing firms | Founder, managing partner, head of BD | Hiring recruiters or BD, new vertical | They need steady new client companies, not candidates. |
| 5 | IT services and MSPs | Founder, CEO, sales lead | Hiring sales, new service line, new region | Growth depends on referrals and a few partners. |
| 6 | Freight and logistics brokerages | Owner, head of sales | Hiring sales reps, new lanes or offices | Outbound heavy business with manual prospecting and rep turnover. |

Target rules:
- Company size roughly 2 to 200 employees.
- Decision maker only: founder, owner, CEO, managing partner, head of growth or sales. Skip role mailboxes (info@, sales@, hello@).
- Geography: US, UK, Australia. DACH off unless Abdul turns it on.
- Needs a real signal inside its window, or a strong ICP fit (tier C).

## 9. Scoring, tiers and sending rules

**Score (0 to 100):** ICP fit up to 40. Signal strength and freshness up to 35. Data quality (verified email, domain match, decision maker confirmed) up to 15. Geography up to 10.
- Tier A, 75 and above: custom signal line, full sequence, LinkedIn note too.
- Tier B, 55 to 74: signal line plus standard sequence.
- Tier C, 40 to 54: plain fallback line, sent last, lowest priority.
- Below 40: do not contact.

**Volume target:** up to 100 new qualified prospects a day once warmed up. Tier A and B go first. If fewer qualify, send fewer.

**Sending rules:**
- Secondary domains only, 2 to 3 mailboxes per domain, SPF, DKIM and DMARC checked before any live send. Add a list-unsubscribe header and a visible opt out line.
- Warm up for 3 weeks or more before real prospects.
- Default ramp per mailbox: start at 10 to 15 per day, add about 5 per week, cap at 30 per day. Vendors cite higher caps, but we start conservative. All numbers live in config.
- Sequence: 4 emails at day 0, 3, 7 and 14. Gaps never shorter than 3 days.
- Send in the prospect's local business hours, Tuesday to Thursday first, with random 3 to 8 minute delays.
- Stop a contact's sequence the moment they reply, bounce, unsubscribe or complain.
- Pause a mailbox or domain automatically and alert Abdul if bounce rate is over 3 percent on the last 200 sends, any spam complaint appears, or opens and replies suddenly drop.
- Targets to track (benchmarks to beat, not promises): bounce under 2 percent, complaints under 0.1 percent, positive reply rate above 1 percent.

## 10. Replies

Classify every reply into: interested, meeting_request, question, not_now, not_interested, wrong_person_referral, out_of_office, unsubscribe, auto_bounce, asks_if_automated.

- interested, meeting_request, question, asks_if_automated: alert Abdul immediately with a draft reply from `message-library.md`. Goal is to reply within 15 minutes during EST hours.
- not_now: tag a follow up date (30 to 60 days).
- wrong_person_referral: extract the new contact, add as a new lead linked to the referrer.
- unsubscribe or clear opt out: suppress immediately and permanently.
- out_of_office: pause and resume after the return date.

**Sales handoff.** After an interested reply: offer two time slots and the Calendly link. Before the call, prepare notes on their product, ICP and current outbound. On the call: understand the product first, walk through the Miro workflow board customized for them, explain the approach, explain infra cost before our fee, cut scope instead of price, try to close on the call, send the agreement the same day if they say they want to start.

## 11. Compliance (not legal advice, Abdul should confirm with a professional)

- US: honor opt outs fast, honest sender identity and subject lines (no fake "re:"), include a physical postal address.
- UK: B2B email to corporate addresses is generally allowed with a clear opt out, but sole traders and some partnerships are treated like consumers. Exclude personal and sole trader style addresses.
- DACH, especially Germany: cold email is tightly restricted. Excluded by default via `exclude_regions` in config.
- Keep a record of where each lead came from and when. Delete data on request.
- Do not scrape or store data in ways the source's terms forbid.

## 12. Tech and code conventions

- Python 3.11+, SQLite to start (easy move to Postgres later), CLI with Typer, config in YAML, secrets in `.env`.
- Anthropic API for writing, QA and reply classification. Strong model for personalization and QA, cheaper model for bulk classification. Read the official docs for current model names before coding.
- Every external service sits behind an adapter interface (`DataSource`, `Verifier`, `Sender`, `ReplySource`) so we can swap Clay, Vibe Prospecting, Spacemail, Smartlead, Instantly or other tools without rewriting the core.
- Spacemail settings (from Spaceship's help page): IMAP mail.spacemail.com port 993 SSL, SMTP mail.spacemail.com port 465 SSL (587 also listed), username is the full address. Credentials in `.env` only. A working connection check script already exists: `spacemail_check.py`.
- Idempotent jobs. Every action logged with timestamp and reason. Tests for scoring, dedupe, suppression, signal freshness, throttling and sequence logic.
- Small commits with clear messages. Never commit data files or secrets.

```
dfy-outbound-engine/
  CLAUDE.md
  message-library.md
  spacemail_check.py
  .env.example
  config/            niches.yaml, signals.yaml, scoring.yaml, sending.yaml, suppression.csv
  src/engine/
    ingest/  verify/  signals/  score/  personalize/  sequence/  send/  replies/  linkedin/  report/
  data/              gitignored
  tests/
```

## 13. Rhythm and how to work with Abdul

- Morning: `engine build-batch` (pull, detect signals, verify, score, write, QA). Abdul reviews tier A messages for 10 minutes.
- Send window: scheduler runs live only after approval and only in allowed hours.
- Afternoon: replies, calls, proposals within 24 hours.
- End of day: `engine report daily`. Weekly: `engine report weekly`. Change only one variable at a time (niche, signal, subject, first line, offer, ask) so results mean something.
- Plan before building. Build in phases and stop after each for review.
- Be direct. If an idea is risky (deliverability, compliance, cost), say so and suggest a safer way.
- Keep chat replies short. No em dashes.
- End every session by running `engine summary` and showing Abdul the "since last time" block. (A SessionEnd hook in `.claude/settings.json` also runs it and writes `data/summary-latest.txt`.)
