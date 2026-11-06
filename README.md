# DFY GTM Systems outbound engine

outbound pipeline for DFY GTM Systems. finds prospects, detects real signals, writes short messages, sends safely, handles replies, reports daily. **dry run by default.** nothing is sent unless a command has `--live` and abdul approved it in chat.

rules live in `CLAUDE.md` and `message-library.md`. the original build brief is in `docs/build-prompt.md`.
live site: [dfygtmsystems.com](https://dfygtmsystems.com)

## setup
```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # fill in later. never commit .env
```

## commands (so far)
```bash
engine find --list                        # configured niche searches
engine find amazon_agency --limit 50      # dry run: free counts + credit estimate
engine find amazon_agency --limit 5 --live --max-credits 60   # pulls, hard capped
engine import path/to/clay_export.csv     # backup path: csv import
engine verify                             # free email checks (add --offline to skip MX)
engine signals refresh                    # detect csv signals, expire stale ones
engine score                              # 0 to 100, tiers A / B / C / dnc
engine signals pick                       # best signal per lead + why-now lines
engine signals list                       # what each lead's email 1 will open with
engine write --limit 20                   # session mode (default): writes a json brief to data/exports
engine write --import data/exports/answer.json   # rule checks, fallback, footer, then review
engine review                             # each email next to its facts and rule result
engine review --approve all               # or --approve 3,4 / --hold 5 --note 'why'
engine write --limit 20 --fake            # full pipeline with the offline fake writer
engine write --limit 20 --yes --max-usd 3 # api mode only (writer_mode: api): real Claude calls, dollar cap
engine messages --status held             # what QA held back and why
engine variants                           # variant rotation report
engine mailboxes add you@second-domain.com --warmup-start 2026-10-01
engine simulate --days 3                  # scheduler + simulator sender, nothing sent
engine outcome <send id> reply            # or bounce / unsubscribe / complaint: stops + suppresses
engine summary                            # "since last time" first, then the whole pipeline
engine summary --no-save                  # peek without moving the baseline
engine dashboard                          # local html at data/dashboard.html: totals, prospect table,
                                          # filters, full history per person. never uploaded
engine dashboard --include-dry-run        # after going live, also show simulated sends
engine note 42 "called, wants the map"    # note on a prospect (id or email), shows in the dashboard
```

a `SessionEnd` hook in `.claude/settings.json` runs `engine summary` at the end of every Claude Code
session and writes `data/summary-latest.txt`. it does nothing if `.venv` is missing.

```bash
engine leads --tier A                     # best first, with a one line reason each
engine stats
engine suppress add rival.com -r competitor
engine suppress check someone@rival.com
python spacemail_check.py                 # login test only, never sends
```

## demo with fake data
```bash
python scripts/make_fake_leads.py data/demo
ENGINE_DB=data/demo/demo.db engine import data/demo/clay_export_1.csv
```
fake domains all end in `.test`, so they can never be real companies.

## status
| phase | what | status |
|---|---|---|
| 0 | scaffold, rules files, archive old drafts | done |
| 1 | sqlite data layer, Clay csv import, dedupe, suppression | done |
| 1.5 | lead finder via Explorium (Vibe Prospecting) API, source + date on every field | done, untested live |
| 2 | verify (free local checks), score, tier | done |
| 3 | signal engine: expiry, csv signals, best pick, why-now lines | done |
| 4 | email 1 writer + QA (rules + Claude pass), fallback, hold, cost cap | done, untested live |
| 4.5 | session writing mode: export brief, write in Claude Code, import, review | done |
| 5 | 4 step sequence, scheduler, caps + ramp, stop rules, auto pause, simulator | done |
| 6 | replies | |
| 7 | linkedin list, reports, a/b | |
| 8 | real senders (approval needed) | |

`archive/` holds the earlier starter kit. it is superseded and breaks some current rules, so don't copy from it.
