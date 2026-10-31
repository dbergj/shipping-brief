"""The written morning brief — the one place a language model is used.

Written for someone starting the day in a shipbroking office: what happened,
what it does to freight, and what is still running from previous days.

The thing that makes it more than a daily summary is **storylines**. Markets are
driven by situations that unfold over weeks — a pipeline attack, a canal
closure, a sanctions package — and a brief that only sees today forgets them by
tomorrow. So a small file of running stories is carried in and out of every
call: the model may open a story, add today's development to one, or let one go
quiet, and the next morning it sees them again.

Guardrails, because a confident brief that invents causes is worse than none:

  1. It sees only data this repository collected. No browsing, no recall from
     training. If the oil price failed to fetch, it is told so.
  2. A storyline may only be opened or updated from a headline present in
     today's data, and must name the source. No new story from memory.
  3. Linking a price move to an event is allowed only when the headline is in
     front of it, and must be hedged. Otherwise it says the data does not show
     why.
  4. When a running story has no news today, `latest` is null. Saying "tensions
     continue" without a source is exactly the invention this forbids.
  5. Ageing stories out is done in code, not by the model.

If ANTHROPIC_API_KEY is unset or the call fails, the collector falls back to the
deterministic one-line summary and the dashboard is unaffected.
"""
import json
import os
import re

MODEL = "claude-opus-5"
MAX_TOKENS = 4000

# Keeps cost predictable: the input is dominated by headlines.
MARKET_HEADLINES = 20
COMPANY_HEADLINES = 12
SUMMARY_CHARS = 240

SYSTEM = """You write the morning brief for a shipping market dashboard.

Your reader is a trainee shipbroker starting their day. They are bright but new: \
they do not yet know what TCE, contango or a Capesize is unless you tell them. \
They need to walk into the office knowing what happened, what it does to freight, \
and what is still running from previous days.

ABSOLUTE RULES — these matter more than style:

1. Use ONLY the data given to you in this message. You have no other \
information. Do not use anything you remember about these companies, prices, \
vessels or events from training. Do not browse. If something is not in the data, \
you do not know it.

2. Never invent a reason for a price move. You may connect a move to a headline \
ONLY if that headline appears in the data, and you must hedge: "this may \
reflect", "one likely factor". If nothing in the data explains a move, say so: \
"the data here does not show why".

3. Never state a number that is not in the data. Never estimate, extrapolate or \
round into a different figure. If a number is marked NO DATA, say it is \
unavailable or leave it out.

3a. DATES ARE NOT OPTIONAL. Every figure in the data carries the date it was \
observed, and that date is part of the fact. A figure is only today's if it is \
marked OBSERVED TODAY. For anything else you MUST name its date in the same \
sentence as the number — "Brent was $117.19 on Friday", "the Capesize index \
stood at 3,726 on Monday" — and you must NOT write that it rose, fell, moved \
or reacted "today". It did not. You do not know what it did today.

3b. Never build a causal link between a headline and a figure observed on a \
different day, and never lead the brief with a figure that is not today's. A \
LAGGED or STALE number is background, not news. If no price in the data is \
from today, say that plainly — "no benchmark price has refreshed since Friday" \
— rather than narrating an old one as if it just happened.

4. No investment advice, no price predictions, no "you should".

5. If the data is thin, write a shorter brief. Padding with generalities is \
worse than four honest sentences.

OUTPUT — return exactly two blocks and nothing else:

<brief>
Plain text. No markdown, no headings, no bullets. Four short paragraphs, 320-420 \
words in total.

Paragraph 1 — Lead: the single most important thing in today's data and why it \
matters to someone chartering or fixing ships.

Paragraph 2 — Prices and rates: what moved, by how much, and the likely reasons \
where a headline supports one. Name the figures. Where a move is unexplained by \
the data, say that.

Paragraph 3 — Running stories: what is still live from previous days and whether \
it developed today. If a story had no news today, say it is unchanged rather \
than implying movement.

Paragraph 4 — What to watch: one or two concrete things, and why they connect to \
what happened. No predictions about price levels.

Explain any shipping term the first time you use it, in a few words inside the \
sentence.
</brief>

<storylines>
A JSON array of the running stories AFTER today's update. Rules:
- Carry forward every story given to you unless it should close.
- Open a NEW story only for a significant, ongoing situation that today's \
headlines actually report — a disruption, conflict, regulation, sanction, \
closure, or structural shift that will still matter next week. A single \
company's earnings is not a storyline. Maximum 6 stories; if you would exceed \
that, close the least consequential.
- For each story set "latest" to what today's data added, or null if nothing new.
- "status": "active" if it moved today or in the last few days, "watch" if it is \
quiet but unresolved, "closed" if resolved or no longer relevant.
- "sources" must quote the exact headline text from the data that supports the \
story or today's update. Never cite something not in the data.

Each object:
{"id": "short-kebab-case-id", "title": "short title",
 "status": "active|watch|closed",
 "what": "1-2 sentences on the situation",
 "why_it_matters": "1-2 sentences on what it does to shipping specifically",
 "latest": "what today added, or null",
 "watch": ["metric ids from the data, e.g. brent, vlcc_tce"],
 "sources": ["exact headline text from the data"]}
</storylines>"""


def _dateline(row):
    """The phrase that stops a dated figure being narrated as today's move.

    Every number handed to the model carries this. Rate rows used to carry no
    date at all, and metrics carried a warning only when a fetch had failed —
    so a correct-but-four-days-old Brent print arrived looking exactly like a
    live one, and was duly written up as having fallen that morning.
    """
    when = row.get("as_of_label") or "date unknown"
    if row.get("observed_today"):
        return f"OBSERVED TODAY ({when})"

    sessions = row.get("age_sessions")
    if sessions == 0:
        behind = " (the current trading session — the market has not printed a newer one)"
    elif isinstance(sessions, int) and sessions > 0:
        behind = f", {sessions} trading session{'s' if sessions != 1 else ''} ago"
    else:
        behind = ""

    if row.get("freshness") == "stale":
        return (f"STALE: last observed {when}{behind}; this morning's collection "
                f"could not refresh it. NOT today's figure — do not say it moved today")
    if row.get("freshness") == "lagged":
        return (f"NOT TODAY'S: observed {when}{behind}. The source published this "
                f"and nothing newer. Name this date if you use the number")
    return f"observed {when}{behind}. Name this date if you use the number"


def _fmt_metrics(metrics):
    lines = []
    for m in metrics:
        if m.get("value") is None:
            why = " (no free source exists)" if m.get("no_free_source") else ""
            lines.append(f"- {m['label']} [{m['id']}]: NO DATA TODAY{why}")
            continue
        chg = m.get("change")
        if chg is None:
            move = "change unknown"
        elif chg == 0:
            move = "unchanged"
        else:
            prev = m["value"] - chg
            pct = (chg / prev * 100) if prev else 0
            move = f"{'up' if chg > 0 else 'down'} {abs(chg):,.2f} ({pct:+.1f}%)"
        if m.get("change_since_label") and chg not in (None,):
            move += f" versus {m['change_since_label']}"
        lines.append(f"- {m['label']} [{m['id']}]: {m['value']:,.2f} {m['unit']}, "
                     f"{move} — {_dateline(m)}")
    return "\n".join(lines) or "- (no price data available today)"


def _fmt_rates(rates):
    active = [r for r in rates if r.get("value") is not None]
    if not active:
        return ("- No freight route rates available. Official Baltic Exchange route data is "
                "a paid product this dashboard does not subscribe to, so the per-vessel TCE "
                "rows are inactive. Do not speculate about their level or direction.")
    return "\n".join(f"- {r['label']} [{r['id']}]: {r['value']:,.0f} {r['unit']} "
                     f"— {_dateline(r)}" for r in active)


def _fmt_news(items, limit):
    out = []
    for it in items[:limit]:
        head = f"- [{it.get('source', '?')}]"
        if it.get("ticker"):
            head += f" ({it.get('company') or it['ticker']})"
        head += f" {it.get('title', '')}"
        if it.get("summary"):
            head += f"\n    {it['summary'][:SUMMARY_CHARS]}"
        out.append(head)
    return "\n".join(out) or "- (no headlines retrieved)"


def _fmt_storylines(stories):
    if not stories:
        return "(none yet — this is the first brief, or no story has been opened)"
    out = []
    for s in stories:
        out.append(json.dumps({
            "id": s.get("id"), "title": s.get("title"), "status": s.get("status"),
            "what": s.get("what"), "why_it_matters": s.get("why_it_matters"),
            "days_running": s.get("days_running"),
            "last_update": s.get("last_update"),
            "watch": s.get("watch", []),
        }, ensure_ascii=False))
    return "\n".join(out)


def _freshness_summary(snapshot):
    """State up front which figures are today's, before any number is shown.

    The dating rules below are easy to state and easy for a model to lose track
    of halfway down a long list of prices. This puts the answer at the top as a
    fact: these ids are today's, these are not. If none are, it says so, which
    is the case the old prompt handled worst — it had no way to express "no
    price has refreshed since Friday" and so narrated Friday as today.
    """
    rows = [r for r in list(snapshot.get("metrics", [])) + list(snapshot.get("rates", []))
            if r.get("value") is not None]
    today = sorted(r["id"] for r in rows if r.get("observed_today"))
    behind = sorted((r.get("age_sessions") or 0, r["id"]) for r in rows
                    if not r.get("observed_today"))
    lines = []
    if today:
        lines.append("Observed TODAY (these are today's figures, safe to call today's): "
                     + ", ".join(today))
    else:
        lines.append("NOTHING in the price or rate data was observed today. Not one "
                     "figure below is today's. Say so in the brief, and attribute "
                     "every number you use to its own date.")
    if behind:
        lines.append("NOT today's, so name the date beside the figure "
                     "(sessions behind, then id): "
                     + ", ".join(f"{'same session' if n == 0 else n}:{i}"
                                 for n, i in behind))
        lines.append("For every id in that second list you must name its date beside "
                     "the number and must not say it moved, rose or fell today.")
    return "\n".join(lines)


def build_prompt(snapshot, company_news, market_news, storylines):
    return "\n".join([
        f"Today is {snapshot.get('generated_at', '')[:10]}.",
        "",
        "WHICH FIGURES ARE TODAY'S — read this before using any number",
        _freshness_summary(snapshot),
        "",
        "PRICES AND INDICES (each line states when it was observed —",
        "only lines marked OBSERVED TODAY are today's figures)",
        _fmt_metrics(snapshot.get("metrics", [])),
        "",
        "FREIGHT RATES (same rule: the date on the line is the date of the figure)",
        _fmt_rates(snapshot.get("rates", [])),
        "",
        "RUNNING STORIES FROM PREVIOUS DAYS",
        "These were opened on earlier mornings. Carry them forward, update any that",
        "today's headlines develop, and close any that are resolved or stale.",
        _fmt_storylines(storylines),
        "",
        "COMPANY ANNOUNCEMENTS (official exchange filings)",
        _fmt_news(company_news, COMPANY_HEADLINES),
        "",
        "MARKET HEADLINES (trade press)",
        _fmt_news(market_news, MARKET_HEADLINES),
        "",
        "Write the brief and return the updated storylines, from this data alone.",
    ])


def _extract(tag, text):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    return m.group(1).strip() if m else None


def _parse_storylines(raw, log):
    """Parse the storylines block defensively.

    A malformed block must never cost us the prose brief, so failure here
    returns None and the caller keeps yesterday's stories unchanged.
    """
    if not raw:
        return None
    blob = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", blob, re.S)
        if not m:
            log("  brief: storylines block was not valid JSON; keeping yesterday's")
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            log("  brief: storylines block was not valid JSON; keeping yesterday's")
            return None
    if not isinstance(data, list):
        log("  brief: storylines block was not a list; keeping yesterday's")
        return None

    clean = []
    for s in data:
        if not isinstance(s, dict) or not s.get("id") or not s.get("title"):
            continue
        clean.append({
            "id": str(s["id"])[:60],
            "title": str(s["title"])[:120],
            "status": s.get("status") if s.get("status") in ("active", "watch", "closed") else "watch",
            "what": str(s.get("what") or "")[:600],
            "why_it_matters": str(s.get("why_it_matters") or "")[:600],
            "latest": (str(s["latest"])[:600] if s.get("latest") else None),
            "watch": [str(w)[:40] for w in (s.get("watch") or [])][:6],
            "sources": [str(x)[:300] for x in (s.get("sources") or [])][:4],
        })
    return clean[:6]


def _call(client, prompt, log):
    """Call the API, degrading through optional parameters if any are rejected.

    The refusal-fallback and effort parameters are worth having, but this runs
    unattended once a day — better to send a plainer request than to publish no
    brief because one optional field was not accepted.
    """
    base = dict(model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM,
                messages=[{"role": "user", "content": prompt}])

    attempts = [
        ("full", lambda: client.beta.messages.create(
            **base,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
        )),
        ("no-fallback", lambda: client.messages.create(
            **base, thinking={"type": "adaptive"}, output_config={"effort": "medium"})),
        ("plain", lambda: client.messages.create(**base)),
    ]
    last = None
    for name, call in attempts:
        try:
            response = call()
            if name != "full":
                log(f"  brief: used the '{name}' request shape")
            return response
        except Exception as exc:  # noqa: BLE001 - try the simpler shape next
            last = exc
            log(f"  brief: '{name}' request failed — {type(exc).__name__}: {str(exc)[:160]}")
    raise last


def generate(snapshot, company_news, market_news, storylines=None, log=print, dry_run=False):
    """Return {"text", "storylines", "model", "usage"} or None."""
    prompt = build_prompt(snapshot, company_news, market_news, storylines or [])

    if dry_run:
        log("  brief: dry run — prompt follows")
        log(prompt)
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("  brief: ANTHROPIC_API_KEY not set, skipping the written brief")
        return None
    try:
        import anthropic
    except ImportError:
        log("  brief: the `anthropic` package is not installed, skipping")
        return None

    try:
        client = anthropic.Anthropic()
        response = _call(client, prompt, log)
    except Exception as exc:  # noqa: BLE001 - never let the brief break the run
        log(f"  brief: generation failed — {type(exc).__name__}: {exc}")
        return None

    if getattr(response, "stop_reason", None) == "refusal":
        log("  brief: declined by safety classifiers; skipping")
        return None

    text = "\n\n".join(
        b.text.strip() for b in response.content
        if getattr(b, "type", None) == "text" and getattr(b, "text", "").strip()
    ).strip()
    if not text:
        log("  brief: model returned no text")
        return None

    body = _extract("brief", text) or text
    stories = _parse_storylines(_extract("storylines", text), log)

    usage = getattr(response, "usage", None)
    cost = None
    if usage:
        # Opus 5 list price, for the running total shown on the page.
        cost = round(usage.input_tokens / 1e6 * 5 + usage.output_tokens / 1e6 * 25, 4)
        log(f"  brief: {usage.input_tokens} in / {usage.output_tokens} out tokens "
            f"(about ${cost:.3f})")

    return {
        "text": body,
        "storylines": stories,
        "model": getattr(response, "model", MODEL),
        "written_at": snapshot.get("generated_at"),
        "cost_usd": cost,
    }
