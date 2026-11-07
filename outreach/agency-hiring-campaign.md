# Campaign: agencies hiring creatives (from 24hrs_244_jobs.csv)

Status: DRAFT. Nothing sent. Waiting for Abdul to pick the offer and approve the copy.
Prospect list (with emails) lives in `data/agency-hiring-list.csv` (gitignored, never committed).

## The list
- 254 rows, 122 companies, all US, all hiring in the last 24 hours (SimplyHired, Jobright, Indeed, LinkedIn).
- Almost every job is a creative role: graphic designer, video editor, content creator, UX.
- Fit for our offer:
  - **30 marketing, ad, design and media agencies** (best fit, this campaign).
  - 5 staffing firms (niche 4, separate copy later).
  - 6 tech companies (weak fit, skip for now).
  - 75 retail, med spa, school, photographer and other local businesses (they don't buy outbound, skip).
- Signal: "hiring a {role}" from the job post, first seen in the last 24 hours. It's a real field with a source and a date.

## Why this angle works
An agency hiring a designer or editor is adding delivery capacity. Each new hire costs payroll every month, and the work to keep them busy has to come from somewhere. Most small agencies win clients through referrals, which they can't plan around. Our system is the second channel that fills that capacity. The email names what we saw, then says why it matters to them. No guessing about their business.

## Offers (pick one for email 1)

**A. The first 20 (recommended).** "Want me to send the first 20 companies the system would go after for {Company}, with the reason for each?"
- Why: it shows the work instead of talking about it, it's easy to say yes to, and it opens the call.
- Cost: about 20 to 40 Clay credits per positive reply (only for people who say yes). Clay credits are at zero right now.
- This is Offer 4 (sample run) used as the email 1 ask. It isn't selling a lead list: it's the first output of their system.

**B. The free one page map (Offer 0).** "Want a one page map of how this would run for {Company}?"
- Why: no credits needed. It's lower value than A, so expect fewer replies.

**C. 15 minute timeline ask.** "Open to 15 minutes Tuesday or Wednesday?"
- Why: CLAUDE.md says timeline asks book more meetings in vendor data. Test it against A.

**D. Pilot guarantee (NEEDS ABDUL'S DECISION, not in any copy).** Example shape: "If the system doesn't book X qualified calls in the first 60 days after sending goes live, month 3 is free." Only after Abdul sets X and the conditions in CLAUDE.md section 6.

Price never goes in email 1. "$2,000 a month, infra at cost" is said on the call.

## Email 1 (three variants, rotate, 50 to 90 words)

**V1, capacity (offer A)**
Subject: `{Company} and the new {role}`
```
Hi {First},

Saw {Company} is hiring a {role}. More people on delivery means you need more clients coming in to keep them busy.

I build outbound systems in Clay for agencies. It finds companies that look like your best clients, writes to each one from a real reason, and sends from separate domains, so you only take the calls. We did this for an Amazon agency.

Want me to send the first 20 companies it would go after for {Company}?
```

**V2, referrals (offer A)**
Subject: `new clients for {Company}`
```
Hi {First},

Saw the {role} post at {Company}. Growing the team is the part you control. Filling their week with client work is harder when most new clients come from referrals.

I set up the system that finds the right companies, writes to them and sends it, so a second channel runs next to referrals without you prospecting.

Want the first 20 companies it would target for {Company}, with the reason for each?
```

**V3, short with a timeline ask (offer C)**
Subject: `{role} hire at {Company}`
```
Hi {First},

Saw {Company} is hiring a {role}. When agencies add delivery people, the gap is usually new client flow, not talent.

I build outbound systems in Clay that bring agencies new clients without the founder prospecting. Built and run for you.

Open to 15 minutes Tuesday or Wednesday to see if it fits?
```

## Follow ups (each one adds something new, 3+ days apart)

**Day 3, how it picks (same thread)**
```
One detail on how it works. It only writes to a company when there's a reason, like a new marketing hire or a launch. No reason, no email. That's why the replies come from people who need help now.

Want the first 20 for {Company}?
```

**Day 7, the proof (same thread)**
```
For the Amazon agency we ran this for, the setup was: lead data from SmartScout and Jungle Scout, enrichment in Clay for the right decision maker and a reason to write, email verification, every email written from those facts, sent through Smartlead.

Same build works for {Company}, just different data sources. Happy to map it on one page.
```

**Day 14, close the loop (new subject: `closing the loop`)**
```
Last one from me. If referrals keep {Company} full, you don't need this. If you want a second channel that runs on its own, reply "20" and I'll send the list.
```

## Filled examples (real rows, real job posts)

**Jeff, Huemor (Pittsburgh). Hiring: digital brand and experience designer. V1**
Subject: `Huemor and the new designer`
```
Hi Jeff,

Saw Huemor is hiring a digital brand and experience designer. More people on delivery means you need more clients coming in to keep them busy.

I build outbound systems in Clay for agencies. It finds companies that look like your best clients, writes to each one from a real reason, and sends from separate domains, so you only take the calls. We did this for an Amazon agency.

Want me to send the first 20 companies it would go after for Huemor?
```

**Emilee, Trekk (Rockford, IL). Hiring: graphic designer. V2**
Subject: `new clients for Trekk`
```
Hi Emilee,

Saw the graphic designer post at Trekk. Growing the team is the part you control. Filling their week with client work is harder when most new clients come from referrals.

I set up the system that finds the right companies, writes to them and sends it, so a second channel runs next to referrals without you prospecting.

Want the first 20 companies it would target for Trekk, with the reason for each?
```

**Evan, SocialScience (Nashville). Hiring: video editor, performance creative. V3**
Subject: `video editor hire at SocialScience`
```
Hi Evan,

Saw SocialScience is hiring a video editor for performance creative. When agencies add delivery people, the gap is usually new client flow, not talent.

I build outbound systems in Clay that bring agencies new clients without the founder prospecting. Built and run for you.

Open to 15 minutes Tuesday or Wednesday to see if it fits?
```

Footer on every email:
```
Abdul
DFY GTM Systems
{physical address}
Not for you? Just say so and I'll stop.
```

## Before anything is sent
1. **Physical address.** All 30 are in the US. US law (CAN-SPAM) needs a postal address in every commercial email. A USPS PO box or a virtual mailbox from a registered mail receiving agency counts (about $10 to $20 a month). No address, no US send.
2. **Proof.** The website shows a "Performance Marketing Agencies" campaign (4.42% reply rate). That's the perfect proof for this list. Abdul has to confirm it's real and that he can use it, then it replaces "We did this for an Amazon agency".
3. **Volume.** These go from Abdul's main Gmail by his choice, so 15 a day at most, sent by hand from drafts. A secondary domain is the safe move for anything bigger.
