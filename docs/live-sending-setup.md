# Live sending on Spacemail: setup

Our own sender in place of Smartlead or Instantly. It sends from your Spacemail mailboxes, runs the 4 step sequence, reads replies over IMAP, stops anyone who replies, bounces or unsubscribes, and pauses a mailbox if bounces go above 3%.

## What I read (Spaceship's own pages)
- Mail server settings: mail.spacemail.com, IMAP 993 SSL, SMTP 465 SSL (587 is also listed). This matches your screenshot.
- [Sending limits](https://www.spaceship.com/knowledgebase/spacemail-email-limits/): 500 emails an hour per mailbox on paid plans, 20 an hour on trial. Spaceship can limit or suspend a mailbox for high volume or warm-up style sending. Our cap of 10 to 30 a day stays far below both.
- [DKIM](https://www.spaceship.com/knowledgebase/setup-dkim-record-spacemail/): TXT record at `spacemail._domainkey`. It's added automatically if the domain uses Spaceship nameservers.
- [SPF](https://www.spaceship.com/knowledgebase/spacemail-dns-setup-namecheap/): `v=spf1 include:spf.spacemail.com ~all`.
- [Starlight VM](https://www.spaceship.com/knowledgebase/starlight-vm-getting-started/): Spaceship's own Linux VPS, from about $4.90 a month, with Ubuntu 24.04 and a command line in the browser.

## 1. The machine (Spaceship Starlight VM)
Buy the smallest Starlight VM with Ubuntu 24.04. In Starlight Manager, open your VM, then Virtual Machine access, then Launch command line. Run:

```bash
sudo apt-get update && sudo apt-get install -y python3-venv git
git clone https://github.com/connectwithabdulsa-gif/claude.git engine && cd engine
git checkout claude/abdul-dfy-gtm-offer-5yw80i
python3 -m venv .venv && .venv/bin/pip install -e .
cp .env.example .env && nano .env
```

## 2. Passwords (only in `.env` on the VM, never in chat)
Add one line per mailbox. The name is the address in capitals, with `@ . -` turned into `_`:
```
SPACEMAIL_PASSWORD_ABDUL_YOURDOMAIN_COM=the-password
```
The `.env` file is never committed.

## 3. Config (I can do this part once you send the addresses)
- `config/sender.yaml`:
  - `physical_address`: needed for US prospects. Live sending is blocked until it's filled.
  - `alerts_to`: your Gmail, for paused mailboxes and new replies.
  - `own_addresses`: your Gmail plus one other address, used for test emails.
- Add each mailbox. `--aged` means a work mailbox already in real use, so the ramp starts today at 10 a day:
```bash
.venv/bin/engine mailboxes add you@yourdomain.com --aged
```
- Then check the domain's DNS:
```bash
.venv/bin/engine deliverability
```
  MX, SPF, DKIM and DMARC must all pass, or that mailbox won't send.

## 4. Tests to yourself first
```bash
.venv/bin/engine mailboxes test you@yourdomain.com --to yourgmail@gmail.com --live
```
Check that it lands in the inbox, not spam. Then open "Show original" and confirm SPF, DKIM and DMARC say PASS.

## 5. Go live (only after you say so in chat)
```bash
.venv/bin/engine go-live          # type SEND
.venv/bin/engine run              # dry view: every gate and mailbox status
crontab -e                        # add the line below
```
```
*/5 * * * * cd $HOME/engine && .venv/bin/engine deliverability >/dev/null 2>&1; .venv/bin/engine run --live >> data/run.log 2>&1
```
DNS is checked every tick, which is cheap. A tick sends at most 1 email per mailbox, so with the 5 minute cron each mailbox sends no more than one email every 5 minutes. Emails only go out inside the recipient's 9 to 5, Monday to Friday. New sequences start Tuesday to Thursday.

## Every day
```bash
.venv/bin/engine mailboxes list      # today's sends against the cap, bounce %, replies, paused or not
.venv/bin/engine replies inbox       # replies waiting for you. nothing is ever answered automatically
.venv/bin/engine report daily
.venv/bin/engine stop                # kill switch. engine start turns it off again
```

## Safety gates (checked on every tick)
Nothing sends unless all of these pass:
1. You ran `engine go-live`.
2. The kill switch is off.
3. The real physical address is in `config/sender.yaml`.
4. The mailbox is not on dfygtmsystems.com.
5. The domain's DNS passed within the last 24 hours.
6. The mailbox's password is in `.env`.
7. The mailbox isn't paused.
8. The day's cap isn't reached.
9. The lead is not suppressed and hasn't replied.
