"""The written morning brief — the one place a language model is used.

Everything else in this project is deterministic. This module asks Claude to
read the day's numbers and headlines and write a short explanation of what
happened, because "Brent fell 3%" is not useful to someone learning the market
and "here is why it probably fell, and what that does to tanker earnings" is.

Three rules make that safe rather than hand-wavy:

  1. It only sees data this repository actually collected. No web browsing, no
     recall from training. If today's oil price failed to fetch, the brief is
     told so and must say it doesn't know.
  2. It is instructed never to invent causation. Attributing a price move to a
     headline is only allowed when the headline is in front of it; otherwise it
     must say the move happened and that the cause isn't in the data.
  3. The output is clearly labelled as AI-written on the page, and the brief
     never replaces the raw numbers — it sits above them.

If ANTHROPIC_API_KEY is unset, or the call fails, the collector falls back to
the deterministic one-line summary. The dashboard never breaks for want of it.
"""
import json
import os
import textwrap

MODEL = "claude-opus-5"
MAX_TOKENS = 2000

SYSTEM = """You write the morning brief for a shipping market dashboard.

Your reader is a student who is interested in shipping but knows very little. \
They do not know what TCE, contango or a Capesize is unless you tell them. \
Write so that someone in their first month of learning understands, without \
being condescending.

ABSOLUTE RULES — these matter more than style:

1. Use ONLY the data given to you below. You have no other information. Do not \
use anything you remember about these companies, prices, or events from your \
training. Do not browse. If something is not in the data, you do not know it.

2. Never invent a reason for a price move. You may connect a move to a headline \
ONLY if that headline is in the data you were given and the link is plausible \
and stated as a possibility, not a fact. Use hedged language like "this may \
reflect" or "one likely factor". If nothing in the data explains a move, say \
so plainly: "the data here does not show why".

3. Never state a number that is not in the data. Never estimate or round into a \
different figure. If a number is marked stale or missing, either say it is \
unavailable or leave it out.

4. No investment advice, no price predictions, no "you should".

5. If the data is thin, write a short brief. Padding it out with generalities \
is worse than three honest sentences.

FORMAT — plain text, no markdown, no headings, no bullet points:

Write 2 or 3 short paragraphs, 180 words in total at most.

Paragraph 1: the single most important thing in today's data, and what it means.
Paragraph 2: what moved in prices or rates and the likely reasons, hedged as above.
Paragraph 3 (only if warranted): one thing a learner should watch next, and why \
it connects to what happened.

Explain any shipping term the first time you use it, in a few words inside the \
sentence. Prefer concrete cause-and-effect over adjectives."""


def _fmt_metrics(metrics):
    lines = []
    for m in metrics:
        if m.get("value") is None:
            lines.append(f"- {m['label']}: NO DATA TODAY (could not be fetched)")
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
        stale = " [STALE — carried over from an earlier day, not today's figure]" if m.get("stale") else ""
        lines.append(f"- {m['label']}: {m['value']:,.2f} {m['unit']}, {move}{stale}")
    return "\n".join(lines) or "- (no price data available today)"


def _fmt_rates(rates):
    active = [r for r in rates if r.get("value") is not None]
    if not active:
        return ("- No freight route rates are available. Official Baltic Exchange route "
                "data is a paid product and this dashboard does not subscribe, so the "
                "per-vessel TCE rows are inactive. Do not speculate about their level.")
    return "\n".join(f"- {r['label']}: {r['value']:,.0f} {r['unit']}" for r in active)


def _fmt_news(items, limit):
    out = []
    for it in items[:limit]:
        bits = [f"- [{it.get('source', '?')}]"]
        if it.get("ticker"):
            bits.append(f"({it['ticker']})")
        bits.append(it.get("title", ""))
        line = " ".join(bits)
        if it.get("summary"):
            line += f"\n    {it['summary'][:260]}"
        out.append(line)
    return "\n".join(out) or "- (no headlines retrieved)"


def build_prompt(snapshot, company_news, market_news):
    """Assemble the exact text the model will see.

    Built by joining blocks rather than with an indented f-string: the
    interpolated sections are already multi-line, so a dedent would only strip
    the first line's indentation and leave the rest ragged.
    """
    return "\n".join([
        f"Today is {snapshot.get('generated_at', '')[:10]}.",
        "",
        "PRICES AND INDICES COLLECTED TODAY",
        _fmt_metrics(snapshot.get("metrics", [])),
        "",
        "FREIGHT RATES",
        _fmt_rates(snapshot.get("rates", [])),
        "",
        "COMPANY ANNOUNCEMENTS (official exchange filings)",
        _fmt_news(company_news, 12),
        "",
        "MARKET HEADLINES (trade press)",
        _fmt_news(market_news, 18),
        "",
        "Write the morning brief from this data alone.",
    ])


def generate(snapshot, company_news, market_news, log=print, dry_run=False):
    """Return {"text", "model", "written_at"} or None if unavailable."""
    prompt = build_prompt(snapshot, company_news, market_news)

    if dry_run:
        log("  brief: dry run — prompt follows")
        log(textwrap.indent(prompt, "    "))
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
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            # Route a declined request to a capable substitute instead of
            # leaving the dashboard with no brief at all.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001 - never let the brief break the run
        log(f"  brief: generation failed — {type(exc).__name__}: {exc}")
        return None

    # A refusal returns HTTP 200 with no usable text, so check before reading.
    if getattr(response, "stop_reason", None) == "refusal":
        log("  brief: the request was declined by safety classifiers; skipping")
        return None

    text = "\n\n".join(
        b.text.strip() for b in response.content
        if getattr(b, "type", None) == "text" and getattr(b, "text", "").strip()
    ).strip()
    if not text:
        log("  brief: model returned no text")
        return None

    usage = getattr(response, "usage", None)
    if usage:
        log(f"  brief: written ({usage.input_tokens} in / {usage.output_tokens} out tokens)")
    return {
        "text": text,
        "model": getattr(response, "model", MODEL),
        "written_at": snapshot.get("generated_at"),
        "prompt_chars": len(prompt),
    }
