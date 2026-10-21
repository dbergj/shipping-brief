# Shipping Morning Brief

A daily shipping dashboard that explains itself. Oil, gas and fuel prices, freight
signals, and company news from the stock exchanges — with every piece of jargon
clickable, so you can learn the market while reading it.

Built for a shipping student, not a trader. No subscriptions, no paid APIs.

---

## What you actually have to do

**Every morning:** open your bookmark. That's it. The page updates itself at about
half past six, Norwegian time, before you wake up.

**Never:** you don't have to type in numbers, run anything, or touch any code.

### One-time setup

You've already made the repository. Two settings left, both just clicking:

1. On your repository page, click **Settings** (top row) → **Actions** → **General**
   in the left sidebar. Scroll down to **Workflow permissions**, choose
   **Read and write permissions**, and click **Save**.
   *(This lets the morning job save the data it collects. Without it the job runs
   but can't save anything.)*

2. Click **Settings** → **Pages** in the left sidebar. Under **Branch**, choose
   **main** and the **/ (root)** folder, then click **Save**. Wait two minutes and
   refresh — GitHub shows you a link like `https://dbergj.github.io/shipping-brief`.
   **That's your dashboard. Bookmark it.**

### Checking it worked

Click the **Actions** tab at the top of your repository. You'll see a list of runs
called "Morning brief". A green tick means it worked. Click **Run workflow** on the
right to make it run immediately instead of waiting until tomorrow morning.

If something looks wrong, the page itself tells you: open **Data sources and status**
at the bottom, which lists exactly which sources worked and which didn't.

### Optional: the written morning brief

The dashboard can open with a few paragraphs explaining what happened and why —
written each morning by Claude from the numbers and headlines on the page. It is
the only part of this project that uses AI, and it is strictly fenced in: it sees
only the data this repository collected, it is told never to invent a reason for a
price move, and it must say "the data here does not show why" when the cause isn't
in front of it. It never replaces the numbers; it sits above them, labelled.

To switch it on:

1. Get an API key at <https://console.anthropic.com>. This one costs money —
   roughly **$1-2 a month** at one brief a day. Everything else here is free.
2. In your repository: **Settings** → **Secrets and variables** → **Actions** →
   **New repository secret**.
3. Name it `ANTHROPIC_API_KEY`, paste the key, click **Add secret**.

Leave it unset and the dashboard works exactly as before, with the one-line
automatic summary instead. Nothing breaks.

### Optional: better oil and gas prices

The dashboard reads oil and gas prices from Yahoo Finance, which is free but
sometimes refuses automated requests. There's a free official backup from the US
government that kicks in automatically if you set it up:

1. Go to <https://www.eia.gov/opendata/register.php> and enter your email. They send
   you a key instantly.
2. In your repository, click **Settings** → **Secrets and variables** → **Actions** →
   **New repository secret**.
3. Name it `EIA_API_KEY`, paste the key into the value box, and click **Add secret**.

You never see the key again and it isn't visible to anyone else.

---

## How to use the dashboard

- **Underlined words** are explained — click any of them. The explanation also says
  *why it matters*, which is usually the part that teaches you something. Company
  names are underlined too, in both news columns, with a solid rather than dotted
  line; clicking one opens what that company does and what drives it.
- **Every headline carries a summary**, so you can read the page without opening
  the articles.
- **Click a KPI tile** to see what that number moves and why. Brent → bunker prices
  → tanker earnings, with the reasoning spelled out.
- **Click a company ticker** (the little blue chips) for what the company does, where
  it sits in the market, what makes it money, and who to compare it with.
- **Explain mode** (top right) adds a plain-English note to every panel. Turn it off
  once you don't need it.
- **The Learn section** lets you browse all 78 terms and 40 companies directly.

### Why some rows are greyed out

The freight rate table shows vessel classes and routes with no numbers. That's
deliberate and honest: official Baltic Exchange rate data costs £3,420 a year. The
rows are kept because knowing what a *VLCC on TD3C* is matters whether or not you
can see today's price, and each one explains itself when clicked. If a number ever
becomes available, the row switches on by itself.

The free daily freight signals that *do* work — the Baltic Dry Index headline, and
the dry bulk and tanker freight ETFs — are in the table below them.

### Stale and no data

Every number carries its own source and timestamp.

- **stale** — this morning's update couldn't reach that source, so you're seeing the
  last good value. Hover it to see when it's from.
- **no data** — there's no earlier value to fall back on either.

Nothing is ever estimated or invented to fill a gap. A missing number stays missing.

---

## Where the data comes from

| What | Source | Cost |
|---|---|---|
| Brent, WTI, gas, coal, product prices | Yahoo Finance, US EIA as backup | free |
| Written morning brief | Claude API (optional) | ~$1-2/month |
| Bunker fuel prices | Ship & Bunker | free |
| Baltic Dry Index (headline only) | Hellenic Shipping News | free |
| Company announcements | Euronext Oslo Børs NewsWeb API | free |
| US company filings (6-K, 8-K) | SEC EDGAR | free |
| Trade press headlines | gCaptain, Splash247, Hellenic, Maritime Executive, Seatrade, MarineLink, LNG Prime, Bunkerspot | free |
| Baltic sub-indices, per-route TCE | Baltic Exchange | £3,420/yr — not used |

### Why there is no TradeWinds

TradeWinds is the obvious Norwegian-view source and it has no public feed — it is
a subscription product from NHST, and its `/rss` path serves the website rather
than articles. The same is true of Upstream, ShippingWatch, Finansavisen and
Dagens Næringsliv. Skipsrevyen, Sysla and maritime.no publish no feed at all. E24
does publish one, but its terms of use explicitly forbid supplying the content to
a language model, which the written brief would do — so it is deliberately left
out. All of this is recorded in `collect/registry.py` under `NO_PUBLIC_FEED` so
nobody re-tests it hoping for a different answer.

**If you have BI access, check whether the library licenses TradeWinds or
Clarksons.** That would be the single biggest upgrade available to this project,
and it would cost you nothing.

What the dashboard does instead:

- **Oslo Børs filings are the Norwegian view** — official disclosure from the
  Norwegian-listed owners, which is the primary source TradeWinds itself reports on.
- Stories touching Norway or the Nordics are tagged **Nordic** wherever they
  appear, using a keyword list covering Norwegian ports, owners and companies.
- Each publication is capped so the most prolific one cannot crowd out the rest,
  and the column is interleaved across sources.

---

## How it works

```
.github/workflows/brief.yml   the 05:30 UTC alarm clock
collect/run.py                fetches everything, writes a snapshot, commits it
collect/registry.py           the one file listing every metric and source
collect/sources/              one small adapter per source
collect/brief.py              the only place AI is used — writes the morning brief
content/                      the glossary, company cards and causal map
data/latest.json              what the page reads
data/history/                 one file per day — this is the historical record
data/sec_cache.json           filing headlines already read, so we fetch each once
index.html + site/            the page itself
```

Design rules that keep it from breaking:

- **Python standard library only.** No dependencies to install or rot.
- **A failing source never blanks a number.** The previous value is carried forward
  and flagged stale. A bad day degrades; it doesn't break.
- **Nothing is invented.** No estimates, no model-generated figures. The glossary and
  company cards are hand-written and stored as plain files you can correct yourself.
  The one AI-written element is labelled as such, is fed only this repository's own
  data, and is instructed to admit when the data does not explain a move.
- **The commit history is the database.** Each run saves a dated snapshot, so the
  historical record builds itself with no database to run.

### Running it yourself

```bash
python3 collect/run.py --dry-run   # show what it finds, write nothing
python3 collect/run.py             # write the snapshot
python3 -m http.server 8777        # then open http://localhost:8777
```

Set `SHIPDASH_CACHE_MIN=30` while editing the page to cache responses and avoid
hammering sources that rate-limit.

---

Not investment advice. Data is from public sources, each credited on the page; please
keep usage personal and educational.
