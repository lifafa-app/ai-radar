"""AI Radar: collects the last day's AI model and tool news, ranks it for the channel,
writes digests/YYYY-MM-DD.md and emails it. Standard library only.

Environment (all optional; missing pieces are skipped):
  GEMINI_API_KEY        Google AI Studio key (free tier works)
  GEMINI_MODEL          ranking model, default gemini-3.8-flash
  GEMINI_SEARCH_MODEL   web-search pass, default gemini-2.5-flash (free tier includes Google Search)
  GMAIL_USER            Gmail address that sends the digest
  GMAIL_APP_PASSWORD    16-character Gmail app password
  RADAR_TO              where to send it (comma-separated); defaults to GMAIL_USER
  LOOKBACK_HOURS        how far back to look, default 30

Usage:
  python radar.py              full run
  python radar.py --dry-run    no email
  python radar.py --no-llm     skip Gemini, use keyword ranking
"""

import argparse
import html
import json
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from pathlib import Path

from sources import AI_KEYWORDS, LAUNCH_WORDS, SOURCES

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state" / "seen.json"
DIGEST_DIR = ROOT / "digests"
IST = timezone(timedelta(hours=5, minutes=30))
UA = "Mozilla/5.0 (compatible; ai-radar/1.0)"
ATOM = "{http://www.w3.org/2005/Atom}"
AI_RE = re.compile(AI_KEYWORDS, re.I)
LAUNCH_RE = re.compile(LAUNCH_WORDS, re.I)

SECTIONS = ["Model launches & updates", "Apps & tools", "Benchmarks & leaderboards",
            "Research worth explaining", "Business & industry"]


# ---------------------------------------------------------------- fetching

def fetch(url, timeout=25):
    if "reddit.com" in url:
        time.sleep(3)  # Reddit rate-limits bursts of anonymous requests
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def clean(text, limit=300):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        d = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def norm_url(url):
    p = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(p.query)
                                    if not k.lower().startswith(("utm_", "ref", "source"))])
    return urllib.parse.urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), query, ""))


def from_rss(src):
    root = ET.fromstring(fetch(src["url"]))
    items = []
    for it in root.findall(".//item")[:50]:
        items.append({
            "title": clean(it.findtext("title"), 200),
            "url": (it.findtext("link") or "").strip(),
            "published": parse_date(it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date")),
            "snippet": clean(it.findtext("description")),
        })
    for it in root.findall(f".//{ATOM}entry")[:50]:
        link = it.find(f"{ATOM}link[@rel='alternate']")
        if link is None:
            link = it.find(f"{ATOM}link")
        items.append({
            "title": clean(it.findtext(f"{ATOM}title"), 200),
            "url": link.get("href", "").strip() if link is not None else "",
            "published": parse_date(it.findtext(f"{ATOM}published") or it.findtext(f"{ATOM}updated")),
            "snippet": clean(it.findtext(f"{ATOM}summary") or it.findtext(f"{ATOM}content")),
        })
    return items


def from_page(src):
    page = fetch(src["url"]).decode("utf-8", "ignore")
    pattern = re.compile(src["pattern"])
    found = {}
    for href, inner in re.findall(r'<a[^>]+href="([^"#?]+)"[^>]*>(.*?)</a>', page, re.S | re.I):
        path = href if href.startswith("/") else urllib.parse.urlsplit(href).path
        if not pattern.match(path):
            continue
        title = clean(inner, 160)
        url = src["base"] + path
        if len(title) > len(found.get(url, "")):
            found[url] = title
    return [{"title": t or u.rsplit("/", 1)[-1].replace("-", " "), "url": u, "published": None, "snippet": ""}
            for u, t in found.items()]


def from_hn(_src):
    since = int(time.time()) - 86400
    data = json.loads(fetch("https://hn.algolia.com/api/v1/search?tags=story"
                            f"&numericFilters=created_at_i>{since},points>40&hitsPerPage=60"))
    items = []
    for h in data.get("hits", []):
        if not AI_RE.search(h.get("title", "")):
            continue
        items.append({
            "title": h["title"],
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
            "published": datetime.fromtimestamp(h["created_at_i"], timezone.utc),
            "snippet": f"{h.get('points', 0)} points, {h.get('num_comments', 0)} comments on Hacker News",
        })
    return items


def from_hf_trending(src):
    models = json.loads(fetch(src["url"]))
    return [{"title": f"Trending on Hugging Face: {m['id']}", "url": f"https://huggingface.co/{m['id']}",
             "published": None, "snippet": f"{m.get('likes', 0)} likes, pipeline: {m.get('pipeline_tag', 'n/a')}"}
            for m in models]


FETCHERS = {"rss": from_rss, "page": from_page, "hn": from_hn, "hf_trending": from_hf_trending}


def collect(seen, lookback_hours):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    items, failed, baseline = [], [], []
    for src in SOURCES:
        try:
            raw = FETCHERS[src["kind"]](src)
        except Exception as e:  # one bad source never stops the run
            failed.append(f"{src['name']} ({type(e).__name__})")
            continue
        # Undated sources: the first time we see a source, record its links without reporting them.
        first_visit = src["kind"] in ("page", "hf_trending") and not any(
            v.get("source") == src["name"] for v in seen.values())
        if first_visit:
            baseline.append(src["name"])
        for it in raw:
            if not it["url"] or not it["title"]:
                continue
            key = norm_url(it["url"])
            if key in seen:
                continue
            if it["published"] and it["published"] < cutoff:
                continue
            if src.get("filter") and not AI_RE.search(it["title"] + " " + it["snippet"]):
                continue
            seen[key] = {"date": datetime.now(IST).strftime("%Y-%m-%d"), "source": src["name"]}
            if first_visit:
                continue
            it.update(source=src["name"], tier=src["tier"])
            items.append(it)
    return items, failed, baseline


# ---------------------------------------------------------------- Gemini

def gemini(model, body, key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    data = json.dumps(body).encode()
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                out = json.loads(r.read())
            parts = out["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < 2:
                time.sleep(25 * (attempt + 1))
                continue
            raise RuntimeError(f"Gemini {model} HTTP {e.code}: {e.read()[:300]!r}") from e


def extract_json(text, opener):
    closer = "]" if opener == "[" else "}"
    start, end = text.find(opener), text.rfind(closer)
    if start == -1 or end == -1:
        raise ValueError("no JSON in model output")
    return json.loads(text[start:end + 1])


def search_pass(key, model, today):
    prompt = (
        f"Today is {today} (India time). Using Google Search, find the most important news from the last 24 hours "
        "about AI models, chatbots, AI assistants and AI tools: new model releases, major product updates, "
        "benchmark or leaderboard changes, pricing changes, and notable new AI apps. Cover OpenAI, Anthropic, "
        "Google Gemini, Meta, xAI, Mistral, DeepSeek, Qwen, Perplexity, Microsoft Copilot, Apple and notable startups. "
        'Return ONLY a JSON array of up to 12 objects: {"title": str, "url": str, "source": str, "summary": str}. '
        "Only include items you found in search results; no speculation."
    )
    text = gemini(model, {"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}]}, key)
    found = extract_json(text, "[")
    return [{"title": clean(f.get("title"), 200), "url": f.get("url", ""), "published": None,
             "snippet": clean(f.get("summary")), "source": f"Search: {f.get('source', 'web')}", "tier": 2}
            for f in found if f.get("title")]


RANK_PROMPT = """You are the research editor for a short-form video channel that explains AI models and AI tools
to a broad audience: which AI to use and why, what just launched, how models compare, what benchmarks mean.
Scope: LLMs, chatbots, AI assistants, model releases, AI apps and developer tools, benchmarks, pricing.
Out of scope unless huge: chips and hardware, stock moves, politics and regulation, generic funding news.

Today is {today}. Below are news items collected in the last day (JSON). Using ONLY facts present in these
items (never invent numbers, dates or features), produce this JSON:

{{
  "summary": "2-3 sentences on what matters today",
  "two_reel_day": true or false (true only for a major launch from a top lab or a big shift people will search for today),
  "two_reel_reason": "one line, empty if false",
  "picks": [   // exactly 3, best video topics first
    {{
      "title": "working video title",
      "why_now": "why this matters today",
      "hook": "the first spoken line, under 12 words, a question or a striking number",
      "angle": "the take that makes it more than news",
      "format": "news-short | explainer | head-to-head | concept",
      "theme": "dark for breaking launches, light for explainers and comparisons",
      "length": "45-75s or 2-3min",
      "key_facts": ["facts from the items"],
      "sources": ["urls from the items"],
      "verify": ["claims to double-check before scripting"]
    }}
  ],
  "sections": {{
    "Model launches & updates": [{{"title": "", "one_line": "", "url": "", "source": "", "importance": 1-5}}],
    "Apps & tools": [],
    "Benchmarks & leaderboards": [],
    "Research worth explaining": [],
    "Business & industry": []
  }}
}}

Skip irrelevant items entirely. Merge duplicates about the same story and keep the best source.

ITEMS:
{items}"""


def rank_with_gemini(items, key, model, today):
    slim = [{"title": i["title"], "source": i["source"], "url": i["url"], "snippet": i["snippet"][:280],
             "published": i["published"].isoformat() if i["published"] else None} for i in items[:180]]
    body = {
        "contents": [{"parts": [{"text": RANK_PROMPT.format(today=today, items=json.dumps(slim, ensure_ascii=False))}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3},
    }
    return extract_json(gemini(model, body, key), "{")


def rank_fallback(items):
    big = re.compile(r"\b(OpenAI|GPT[-\w.]*|ChatGPT|Anthropic|Claude|Sonnet|Opus|Gemini|Grok|Llama|DeepSeek|"
                     r"Qwen|Mistral|Perplexity|Copilot)\b", re.I)

    def score(i):
        s = {1: 6, 2: 3, 3: 1}.get(i["tier"], 1)
        s += 4 if LAUNCH_RE.search(i["title"]) else 0
        s += 6 if big.search(i["title"]) else (2 if AI_RE.search(i["title"]) else 0)
        s -= 3 if re.search(r"\b(SDK|ADK|Kotlin|webinar|case study)\b", i["title"], re.I) else 0
        return s
    ranked = sorted(items, key=score, reverse=True)
    sections = {name: [] for name in SECTIONS}
    for i in ranked[:40]:
        name = ("Model launches & updates" if LAUNCH_RE.search(i["title"]) and i["tier"] == 1
                else "Research worth explaining" if i["source"] in ("Hugging Face blog", "Hugging Face trending")
                else "Business & industry" if re.search(r"\b(raise[sd]?|funding|acquir|IPO|valuation|prospectus)\b", i["title"], re.I)
                else "Apps & tools")
        sections[name].append({"title": i["title"], "one_line": i["snippet"][:160], "url": i["url"],
                               "source": i["source"], "importance": min(5, max(1, score(i) // 2))})
    picks = [{"title": i["title"], "why_now": "Top-scored item (keyword ranking; Gemini was not used).",
              "hook": "", "angle": "", "format": "news-short", "theme": "dark", "length": "45-75s",
              "key_facts": [i["snippet"][:200]] if i["snippet"] else [], "sources": [i["url"]], "verify": []}
             for i in ranked[:3]]
    return {"summary": f"{len(items)} new items collected. Ranked by keywords because Gemini was not available.",
            "two_reel_day": False, "two_reel_reason": "", "picks": picks, "sections": sections}


# ---------------------------------------------------------------- output

def render_md(r, day, stats):
    out = [f"# AI Radar · {day.strftime('%a %d %b %Y')}", ""]
    if r.get("two_reel_day"):
        out += [f"> **2-reel day:** {r.get('two_reel_reason', '')}", ""]
    out += [r.get("summary", ""), "", "## Video picks", ""]
    for n, p in enumerate(r.get("picks", []), 1):
        out += [f"### {n}. {p.get('title', '')}",
                f"- **Format:** {p.get('format', '')} · {p.get('length', '')} · {p.get('theme', '')} theme",
                f"- **Hook:** {p.get('hook', '')}", f"- **Angle:** {p.get('angle', '')}",
                f"- **Why now:** {p.get('why_now', '')}"]
        out += [f"- Fact: {f}" for f in p.get("key_facts", [])]
        out += [f"- Verify: {v}" for v in p.get("verify", [])]
        out += [f"- Source: {s}" for s in p.get("sources", [])]
        out.append("")
    for name in SECTIONS:
        rows = r.get("sections", {}).get(name) or []
        if not rows:
            continue
        out += [f"## {name}", ""]
        for i in sorted(rows, key=lambda x: -int(x.get("importance", 0) or 0)):
            out.append(f"- **{i.get('title', '')}** ({i.get('source', '')}): {i.get('one_line', '')} {i.get('url', '')}")
        out.append("")
    out += ["---", stats]
    return "\n".join(out)


def render_html(r, day, stats):
    e = html.escape
    css_card = "border:1px solid #dde2ea;border-radius:10px;padding:14px 16px;margin:0 0 12px;background:#ffffff"
    parts = [f'<div style="font-family:Segoe UI,Arial,sans-serif;max-width:640px;margin:0 auto;color:#0b1220;background:#f4f5f8;padding:18px">',
             f'<div style="font:600 12px monospace;letter-spacing:.12em;color:#5a6270;text-transform:uppercase">AI Radar · {day.strftime("%a %d %b %Y")}</div>',
             f'<p style="font-size:16px;line-height:1.5">{e(r.get("summary", ""))}</p>']
    if r.get("two_reel_day"):
        parts.append(f'<div style="{css_card};border-color:#d13b2a;color:#b3261e"><b>2-reel day.</b> {e(r.get("two_reel_reason", ""))}</div>')
    parts.append('<h2 style="font-size:18px;margin:20px 0 10px">Video picks</h2>')
    for n, p in enumerate(r.get("picks", []), 1):
        facts = "".join(f"<li>{e(f)}</li>" for f in p.get("key_facts", []))
        verify = "".join(f"<li>{e(v)}</li>" for v in p.get("verify", []))
        srcs = " · ".join(f'<a href="{e(s)}" style="color:#1f4fe0">source {k}</a>' for k, s in enumerate(p.get("sources", []), 1))
        parts.append(
            f'<div style="{css_card}"><div style="font:600 11px monospace;color:#5a6270;text-transform:uppercase">'
            f'Pick {n} · {e(p.get("format", ""))} · {e(p.get("length", ""))} · {e(p.get("theme", ""))} theme</div>'
            f'<div style="font-size:17px;font-weight:700;margin:4px 0 6px">{e(p.get("title", ""))}</div>'
            + (f'<div><b>Hook:</b> {e(p.get("hook", ""))}</div>' if p.get("hook") else "")
            + (f'<div><b>Angle:</b> {e(p.get("angle", ""))}</div>' if p.get("angle") else "")
            + f'<div style="color:#5a6270">{e(p.get("why_now", ""))}</div>'
            + (f'<ul style="margin:8px 0;padding-left:18px">{facts}</ul>' if facts else "")
            + (f'<div style="color:#9a620a"><b>Verify:</b></div><ul style="margin:4px 0;padding-left:18px">{verify}</ul>' if verify else "")
            + f'<div style="font-size:13px">{srcs}</div></div>')
    for name in SECTIONS:
        rows = r.get("sections", {}).get(name) or []
        if not rows:
            continue
        lis = "".join(
            f'<li style="margin:0 0 8px"><a href="{e(i.get("url", ""))}" style="color:#0b1220;font-weight:600">{e(i.get("title", ""))}</a>'
            f' <span style="color:#5a6270">({e(i.get("source", ""))})</span><br><span style="color:#3b4250">{e(i.get("one_line", ""))}</span></li>'
            for i in sorted(rows, key=lambda x: -int(x.get("importance", 0) or 0)))
        parts.append(f'<h2 style="font-size:16px;margin:18px 0 8px">{e(name)}</h2><ul style="padding-left:18px;margin:0">{lis}</ul>')
    parts.append(f'<p style="font-size:12px;color:#5a6270;margin-top:24px">{e(stats)}</p></div>')
    return "".join(parts)


def send_email(subject, text_body, html_body):
    user, pw = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (user and pw):
        print("Email skipped: GMAIL_USER / GMAIL_APP_PASSWORD not set")
        return
    to = [a.strip() for a in os.environ.get("RADAR_TO", user).split(",") if a.strip()]
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, f"AI Radar <{user}>", ", ".join(to)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, pw)
        s.sendmail(user, to, msg.as_string())
    print(f"Email sent to {', '.join(to)}")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="don't send email")
    ap.add_argument("--no-llm", action="store_true", help="skip Gemini")
    args = ap.parse_args()

    day = datetime.now(IST)
    today = day.strftime("%A %d %B %Y")
    seen = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    cutoff = (day - timedelta(days=21)).strftime("%Y-%m-%d")
    seen = {k: v for k, v in seen.items() if v.get("date", "") >= cutoff}

    items, failed, baseline = collect(seen, int(os.environ.get("LOOKBACK_HOURS", "30")))
    key = None if args.no_llm else os.environ.get("GEMINI_API_KEY")
    notes = []
    if key:
        try:
            extra = search_pass(key, os.environ.get("GEMINI_SEARCH_MODEL", "gemini-2.5-flash"), today)
            fresh = [x for x in extra if x["url"] and norm_url(x["url"]) not in {norm_url(i["url"]) for i in items}]
            items += fresh
            notes.append(f"search pass added {len(fresh)}")
        except Exception as e:
            notes.append(f"search pass failed ({e})")
    print(f"Collected {len(items)} new items; failed: {failed or 'none'}")

    result = None
    if key and items:
        try:
            result = rank_with_gemini(items, key, os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"), today)
        except Exception as e:
            notes.append(f"Gemini ranking failed, used keyword ranking ({e})")
    if result is None:
        result = rank_fallback(items)

    stats = (f"{len(items)} new items from {len(SOURCES) - len(failed)}/{len(SOURCES)} sources."
             + (f" Unreachable: {', '.join(failed)}." if failed else "")
             + (f" First run for: {', '.join(baseline)} (links recorded, reported from tomorrow)." if baseline else "")
             + (f" Notes: {'; '.join(notes)}." if notes else ""))

    DIGEST_DIR.mkdir(exist_ok=True)
    stem = day.strftime("%Y-%m-%d")
    md = render_md(result, day, stats)
    (DIGEST_DIR / f"{stem}.md").write_text(md, encoding="utf-8")
    (DIGEST_DIR / f"{stem}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    page = render_html(result, day, stats)
    (DIGEST_DIR / f"{stem}.html").write_text(page, encoding="utf-8")
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(seen, indent=1, sort_keys=True))
    print(f"Wrote digests/{stem}.md")

    top = (result.get("picks") or [{}])[0].get("title", "no picks today")
    subject = f"AI Radar · {day.strftime('%a %d %b')} · {top}" + (" · 2-reel day" if result.get("two_reel_day") else "")
    if not args.dry_run:
        send_email(subject, md, page)
    return 0


if __name__ == "__main__":
    sys.exit(main())
