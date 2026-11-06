# Master prompt for Claude Code: build the DFY GTM Systems outbound engine

How to use: put `CLAUDE.md`, `message-library.md`, `spacemail_check.py` and `.env.example` in a folder called `dfy-outbound-engine`. Open a terminal there, run `claude`, then paste everything below the line.

---

You are my senior outbound engineer and a 10 year SDR and GTM engineer in one. I run DFY GTM Systems, a done for you outbound automation company. I need clients now. Read `CLAUDE.md` and `message-library.md` fully first and treat them as the rules of this project.

## The goal

Build a working outbound pipeline for my own company. It finds good prospects in several industries, detects real recent signals about them, writes short human sounding messages that say who we are, why we are writing now and how we can help, sends them safely, handles replies, and tells me every day what is working.

Target: up to 100 new qualified prospects a day once sending is warmed up, each getting a 4 email sequence, plus about 20 manual LinkedIn notes a day that I send by hand. If fewer than 100 qualify on a day, send fewer. Never fill the quota with weak leads.

I own the pipeline, data, logic and reports. The sending layer stays swappable. I use a Spaceship mailbox (Spacemail, SMTP and IMAP). Start with CSV import (Clay and Vibe Prospecting exports), email verification adapters, and a sender interface with a simulator. Add real sender adapters (Spacemail SMTP, Smartlead, Instantly) only after the core works and after reading each official doc.

## What makes the messages work (do not skip)

- Every first email says what we saw (a real signal) and why that made us write now, in one sentence, then why it matters to them, then who i am and what i do in plain words, then one ask.
- Max two signals per email, one is better. A signal older than its window is not used.
- Every follow up adds something new. No "just bumping this".
- Messages must not sound like AI. Follow the writing rules and banned list in `CLAUDE.md` section 7. Rotate 3 to 5 variants per signal so thousands of sends do not look identical.
- Every personalized fact must trace to a stored field with source and date. If it does not, use the plain fallback line.

## How to start

Do not write code yet. First:
1. Read both files and summarize in 10 lines what you understood, including the hard rules.
2. Ask me up to 8 questions about anything unclear. Examples: which verification tool I use, which sending domains and mailboxes exist, my physical address, which proof I am allowed to use publicly, whether to use the capped guarantee offer and its numbers, whether I want to state timelines in copy.
3. Show a phased plan with what I can test at the end of each phase.
Wait for my go.

## Phases (stop after each phase, show a demo, ask whether to continue)

**Phase 1: Foundation and data layer.** Project scaffold, config files, `.env.example`, SQLite schema (leads, companies, signals, messages, sends, replies, suppression, mailboxes, domains, events). CSV ingest with column mapping, dedupe by domain and email, source tracking, timestamps. Suppression checks on import and again right before every send. CLI: `engine import`, `engine stats`. Demo: import 50 fake leads, show dedupe and suppression.

**Phase 2: Verify, score, tier.** Verifier adapter interface with a fake for tests. Rules: skip role mailboxes, flag catch-all, require domain match. Scoring exactly as in `CLAUDE.md` section 9, weights in `config/scoring.yaml`, one line explaining each score. Tiers A, B, C and do not contact. Demo: score 50 leads in a table.

**Phase 3: Signal engine.** Define signals in `config/signals.yaml` from the table in `CLAUDE.md` section 5: name, source field, freshness window, strength, why now line. Detect signals from imported data, store value, source and date, expire stale ones, pick the best one or two per lead. First check which signal types Clay and Vibe Prospecting really provide and tell me what is missing. Do not invent fields. Demo: 30 leads with their picked signals and ages.

**Phase 4: Message writer with QA.** Use the Anthropic API to write email 1 from the signal, following `message-library.md` structure and variants. A second QA pass applies the 7 point check in `CLAUDE.md` section 7 and a banned word and pattern scan, with a reason for every fail. Failed messages fall back to the plain line or are held for my review. Estimate API cost before each batch and ask me first. Demo: 20 leads showing input fields, message, QA verdict, and a variant rotation report.

**Phase 5: Sequences, scheduler, simulator.** 4 email sequence at day 0, 3, 7, 14 (gaps never under 3 days) with the follow up templates from the library. Scheduler respecting per mailbox daily caps and ramp, recipient local business hours, Tuesday to Thursday preference, random 3 to 8 minute delays, stop on reply, bounce or unsubscribe. Auto pause rules for bounce and complaint thresholds. List-unsubscribe header and visible opt out line. A simulator sender that writes what would be sent and when. Demo: simulate 3 days, 3 mailboxes, 100 leads, show the calendar and caps respected.

**Phase 6: Replies.** Reply ingestion interface with a fake source, then Spacemail IMAP later. Claude classifier using the categories in `CLAUDE.md` section 10 with a confidence score. Low confidence goes to me. Alerts for interested replies with a draft from the reply scripts in the library. No auto reply, except immediate unsubscribe. Demo: 20 sample replies classified with drafts.

**Phase 7: LinkedIn list, reports, A/B tests.** `engine linkedin-daily` makes a markdown list of up to 20 tier A prospects with profile link, signal and a note under 200 characters, plus follow ups for people who accepted. Manual sending only. `engine report daily` and `engine report weekly`: sent, bounce rate, replies, positive replies, calls booked, split by niche, signal, variant and subject. Support the A/B tests in the library (one variable at a time, 300 sends minimum). Flag best and worst performers and suggest ONE thing to change.

**Phase 8: Real senders (only after I approve).** Read the official docs for the sender. Implement the Spacemail SMTP and IMAP adapter behind the existing interface (settings and a working check are in `spacemail_check.py` and `CLAUDE.md` section 12). Use `--live` and a hard daily cap. Start with test sends to my own addresses. Add a deliverability checklist command: SPF, DKIM, DMARC, MX and blacklist checks per sending domain, run before any live send.

## Working rules for you

- Everything stays in dry run until I say live.
- After each phase: run tests, show demo output, list what is not done, ask whether to continue.
- If you are unsure about an API, a legal point, a limit or a number, say so and read the docs. Never guess.
- If something I ask looks risky for deliverability, my accounts or compliance, tell me plainly and propose a safer way.
- Do not invent case studies, numbers or timelines. Use only the proof points in `CLAUDE.md`.
- Keep chat replies short and plain. No em dashes.

Begin with step 1 now.
