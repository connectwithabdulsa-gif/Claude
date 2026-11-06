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
| 4 | message writer + QA | |
| 5 | sequences, scheduler, simulator | |
| 6 | replies | |
| 7 | linkedin list, reports, a/b | |
| 8 | real senders (approval needed) | |

`archive/` holds the earlier starter kit. it is superseded and breaks some current rules, so don't copy from it.
