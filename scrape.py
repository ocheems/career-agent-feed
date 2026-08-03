#!/usr/bin/env python3
"""
scrape.py — pull jobs from job APIs, filter by region / location / work-mode /
language / keyword-fit, and publish a single self-contained index.html with each
job's FULL description embedded. GitHub Actions runs this daily; Pages hosts it.

The Career Tailor skill reads this HTML: each job is <article class="job"> with
data-* attributes (title, company, location, work-mode, score, language) and a
<div class="jd"> holding the complete description.

Sources:
  adzuna    region + city + on-site/hybrid coverage   (FREE: app_id + app_key)
  arbeitnow EU/Germany board, English + visa roles     (no key)
  remotive  remote-first, searchable                   (no key)
  remoteok  remote-first                               (no key)
  jobicy    remote-first                               (no key)
  linkedin  LinkedIn jobs via Apify actor, capped       (APIFY_TOKEN; ~$1/1000, hard maxItems cap)
  theirstack LinkedIn/Indeed/Glassdoor + ATS (optional, paid)  THEIRSTACK_API_KEY

Run:  python scrape.py --config config.yaml --out public/index.html
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.request
import urllib.parse

try:
    import yaml
except ImportError:
    print("pip install pyyaml", file=sys.stderr)
    raise

UA = {"User-Agent": "career-agent-feed/1.0 (personal job search)"}
TIMEOUT = 30


# ----------------------------- fetch utils -----------------------------------

def get_json(url, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def post_json(url, payload, headers=None, timeout=TIMEOUT):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={**UA, "Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def strip_html(x):
    x = re.sub(r"<[^>]+>", " ", x or "")
    x = html.unescape(x)
    return re.sub(r"\s+", " ", x).strip()


def s(v, default=""):
    if v is None:
        return default
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return str(v).strip()


def norm(title=None, company=None, location=None, url=None, tags=None,
         description=None, source=None, date=None):
    if isinstance(tags, str):
        tags = [tags]
    return {
        "title": s(title), "company": s(company), "location": s(location, "Remote"),
        "url": s(url), "tags": [str(t).lower() for t in (tags or []) if t],
        "description": strip_html(s(description)) if description else "",
        "source": source, "date": s(date),
    }


def parse_ts(datestr) -> int:
    """Best-effort parse of any source's date into a Unix timestamp (0 if unknown).
    Handles epoch seconds/millis, ISO 8601, plain YYYY-MM-DD, and 'N days ago'."""
    x = str(datestr or "").strip()
    if not x:
        return 0
    if re.fullmatch(r"\d{13}", x):
        return int(x) // 1000
    if re.fullmatch(r"\d{10}", x):
        return int(x)
    try:
        return int(dt.datetime.fromisoformat(x.replace("Z", "+00:00")).timestamp())
    except ValueError:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", x)
    if m:
        try:
            return int(dt.datetime(*map(int, m.groups()), tzinfo=dt.timezone.utc).timestamp())
        except ValueError:
            pass
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    low = x.lower()
    if any(w in low for w in ("just now", "today", "hour", "minute", "moment")):
        return int(now)
    if "yesterday" in low:
        return int(now - 86400)
    m = re.search(r"(\d+)\s*(day|week|month)", low)
    if m:
        mult = {"day": 86400, "week": 604800, "month": 2592000}[m.group(2)]
        return int(now - int(m.group(1)) * mult)
    return 0


def recency_bonus(ts: int, now: float) -> int:
    """Small ranking boost so fresher jobs surface first (0 if age unknown)."""
    if not ts:
        return 0
    age_days = (now - ts) / 86400
    if age_days <= 2:
        return 3
    if age_days <= 7:
        return 2
    if age_days <= 14:
        return 1
    return 0


def human_age(ts: int, now: float) -> str:
    if not ts:
        return "date n/a"
    d = int((now - ts) / 86400)
    if d <= 0:
        return "today"
    if d == 1:
        return "1d ago"
    return f"{d}d ago"


# ----------------------------- sources ---------------------------------------

def src_adzuna(cfg):
    app_id = os.getenv(cfg.get("adzuna_app_id_env", "ADZUNA_APP_ID"))
    app_key = os.getenv(cfg.get("adzuna_app_key_env", "ADZUNA_APP_KEY"))
    region = (cfg.get("region") or "DE").lower()
    if not app_id or not app_key or region == "remote":
        if region != "remote":
            print("  adzuna skipped: set ADZUNA_APP_ID / ADZUNA_APP_KEY", file=sys.stderr)
        return []
    out = []
    what_or = " ".join(cfg.get("search_terms") or cfg.get("target_titles") or [])
    params = {
        "app_id": app_id, "app_key": app_key, "results_per_page": 50,
        "what_or": what_or, "content-type": "application/json",
        "max_days_old": int(cfg.get("max_age_days", 21)), "sort_by": "date",  # newest first
    }
    if cfg.get("location"):
        params["where"] = cfg["location"]
        params["distance"] = int(cfg.get("distance_km", 30))
    url = f"https://api.adzuna.com/v1/api/jobs/{region}/search/1?" + urllib.parse.urlencode(params)
    for j in get_json(url).get("results", []):
        out.append(norm(j.get("title"), (j.get("company") or {}).get("display_name"),
                        (j.get("location") or {}).get("display_name"), j.get("redirect_url"),
                        [(j.get("category") or {}).get("label")], j.get("description"),
                        "Adzuna", j.get("created", "")))
    return out


def src_arbeitnow(_):
    out = []
    for j in get_json("https://www.arbeitnow.com/api/job-board-api").get("data", []):
        tags = list(j.get("tags") or []) + list(j.get("job_types") or [])
        if j.get("remote"):
            tags.append("remote")
        out.append(norm(j.get("title"), j.get("company_name"), j.get("location"),
                        j.get("url"), tags, j.get("description"), "Arbeitnow",
                        j.get("created_at", "")))
    return out


def src_remotive(cfg):
    out = []
    for kw in (cfg.get("search_terms") or [""]):
        u = "https://remotive.com/api/remote-jobs"
        if kw:
            u += "?" + urllib.parse.urlencode({"search": kw})
        for j in get_json(u).get("jobs", []):
            out.append(norm(j.get("title"), j.get("company_name"),
                            j.get("candidate_required_location"), j.get("url"),
                            list(j.get("tags") or []) + ["remote"], j.get("description"),
                            "Remotive", j.get("publication_date", "")))
    return out


def src_remoteok(_):
    out = []
    for j in get_json("https://remoteok.com/api"):
        if not isinstance(j, dict) or "position" not in j:
            continue
        out.append(norm(j.get("position"), j.get("company"), j.get("location") or "Remote",
                        j.get("url"), list(j.get("tags") or []) + ["remote"],
                        j.get("description"), "RemoteOK", j.get("date", "")))
    return out


def src_jobicy(_):
    out = []
    for j in get_json("https://jobicy.com/api/v2/remote-jobs?count=50").get("jobs", []):
        out.append(norm(j.get("jobTitle"), j.get("companyName"), j.get("jobGeo") or "Remote",
                        j.get("url"), [j.get("jobIndustry"), "remote"],
                        j.get("jobDescription") or j.get("jobExcerpt"), "Jobicy",
                        j.get("pubDate", "")))
    return out


def src_linkedin(cfg):
    """LinkedIn jobs via the Apify curious_coder actor. Cost is bounded by `max_linkedin`,
    enforced at the Apify platform level with &maxItems (a true per-run ceiling, ~$1/1000)."""
    token = os.getenv(cfg.get("apify_token_env", "APIFY_TOKEN"))
    if not token:
        print("  linkedin skipped: set APIFY_TOKEN", file=sys.stderr)
        return []
    actor = cfg.get("linkedin_actor", "curious_coder~linkedin-jobs-scraper")
    cap = int(cfg.get("max_linkedin", 50))

    # Build a LinkedIn jobs search URL from the same config the rest of the feed uses.
    kw = " ".join(cfg.get("search_terms") or cfg.get("target_titles") or ["operations"])
    # f_TPR = time window (seconds); sortBy=DD = date-descending (newest first)
    window = int(cfg.get("max_age_days", 21)) * 86400
    params = {"keywords": kw, "f_TPR": f"r{window}", "sortBy": "DD"}
    region = (cfg.get("region") or "").lower()
    if cfg.get("location") and region != "remote":
        params["location"] = cfg["location"]
    elif region and region != "remote":
        params["location"] = COUNTRY_NAME.get(region, cfg["region"])
    wt = {"onsite": "1", "remote": "2", "hybrid": "3"}   # LinkedIn f_WT work-type codes
    modes = [wt[m] for m in cfg.get("work_modes", []) if m in wt]
    if modes:
        params["f_WT"] = ",".join(modes)
    search_url = "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)

    endpoint = (f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
                f"?token={token}&maxItems={cap}&timeout=240")
    payload = {"urls": [search_url], "count": cap, "rows": cap, "scrapeCompany": False}
    try:
        items = post_json(endpoint, payload, timeout=250)
    except Exception as e:
        print(f"  linkedin skipped: {e}", file=sys.stderr)
        return []

    out = []
    for j in (items or [])[:cap]:
        if not isinstance(j, dict):
            continue
        out.append(norm(
            j.get("title") or j.get("jobTitle"),
            j.get("companyName") or j.get("company") or (j.get("companyDetails") or {}).get("name"),
            j.get("location") or j.get("jobLocation") or j.get("formattedLocation") or "",
            j.get("jobUrl") or j.get("link") or j.get("url"),
            (j.get("skills") or []) + [j.get("workType") or j.get("workplaceType") or ""],
            j.get("descriptionText") or j.get("description") or j.get("descriptionHtml"),
            "LinkedIn",
            j.get("postedAt") or j.get("publishedAt") or j.get("postedTime") or j.get("listedAt") or "",
        ))
    return out


def src_theirstack(cfg):
    key = os.getenv("THEIRSTACK_API_KEY")
    if not key:
        return []
    region = (cfg.get("region") or "DE").upper()
    payload = {"page": 0, "limit": int(cfg.get("theirstack_limit", 25)),
               "job_title_or": cfg.get("target_titles", []), "posted_at_max_age_days": 3,
               "include_total_results": False}
    if region != "REMOTE":
        payload["job_country_code_or"] = [region]
    try:
        data = post_json("https://api.theirstack.com/v1/jobs/search", payload,
                         headers={"Authorization": f"Bearer {key}"})
    except Exception as e:
        print(f"  theirstack skipped: {e}", file=sys.stderr)
        return []
    out = []
    for j in data.get("data", []):
        out.append(norm(j.get("job_title"), (j.get("company_object") or {}).get("name") or j.get("company"),
                        j.get("location") or "Remote", j.get("url") or j.get("final_url"),
                        j.get("technology_slugs"), j.get("description"), "TheirStack",
                        j.get("date_posted", "")))
    return out


SOURCES = {"adzuna": src_adzuna, "arbeitnow": src_arbeitnow, "remotive": src_remotive,
           "remoteok": src_remoteok, "jobicy": src_jobicy, "linkedin": src_linkedin,
           "theirstack": src_theirstack}


# ----------------------------- classifiers -----------------------------------

HYBRID = ["hybrid", "hybrides", "teilweise remote", "remote-friendly", "flexible working",
          "days in office", "days per week in", "partly remote", "home office possible"]
REMOTE = ["fully remote", "100% remote", "remote-first", "work from home", "work from anywhere",
          "home office", "homeoffice", "telecommute", "remote", "anywhere"]
ONSITE = ["on-site", "onsite", "on site", "vor ort", "in-office", "in office", "im büro",
          "presence required", "relocation"]


def work_mode(job):
    t = f"{job['title']} {job['description']} {' '.join(job['tags'])}".lower()
    if any(k in t for k in HYBRID):
        return "hybrid"
    if any(k in t for k in REMOTE):
        return "remote"
    if any(k in t for k in ONSITE):
        return "onsite"
    # default: a concrete city implies on-site; remote boards imply remote
    return "remote" if job["source"] in ("Remotive", "RemoteOK", "Jobicy") else "onsite"


def is_english(text):
    if not text:
        return True
    try:
        from langdetect import detect
        return detect(text[:1500]) == "en"
    except Exception:
        t = " " + text.lower() + " "
        en = sum(t.count(f" {w} ") for w in ["the", "and", "for", "with", "you", "we", "to", "role", "team"])
        de = sum(t.count(f" {w} ") for w in ["und", "der", "die", "das", "mit", "für", "wir", "sie", "eine", "im"])
        return en >= de


LEVEL = {"none": 0, "a1": 1, "a2": 2, "b1": 3, "b2": 4, "c1": 5, "c2": 6}
GERMAN_HIGH = ["verhandlungssicher", "fließend deutsch", "fließende deutsch", "sehr gute deutschkenntnisse",
               "muttersprache", "native german", "native-level german", "fluent german", "fluent in german",
               "excellent german", "deutsch auf muttersprachniveau", "verhandlungssichere deutsch"]


def german_ok(text, max_level):
    """False if the posting demands German above the candidate's cap."""
    if not max_level:
        return True
    cap = LEVEL.get(str(max_level).lower(), 3)
    t = text.lower()
    for lvl, val in (("c2", 6), ("c1", 5), ("b2", 4)):
        if val > cap and (f"deutsch {lvl}" in t or f"german {lvl}" in t or
                          f"{lvl} deutsch" in t or f"{lvl} german" in t or f"{lvl}-level german" in t):
            return False
    if cap < 4 and any(p in t for p in GERMAN_HIGH):   # candidate ≤ B1, role wants fluent German
        return False
    return True


# ----------------------------- score & filter --------------------------------

def score(job, cfg):
    t = f"{job['title']} {job['description']} {' '.join(job['tags'])}".lower()
    sc = 0
    if any(x.lower() in t for x in cfg.get("target_titles", [])):
        sc += 5
    sc += sum(1 for k in cfg.get("keyword_themes", []) if k.lower() in t)
    # region/location bonus
    region = (cfg.get("region") or "").lower()
    loc = job["location"].lower()
    if cfg.get("location") and cfg["location"].lower() in loc:
        sc += 2
    elif region and region != "remote" and (region in loc or COUNTRY_NAME.get(region, "") in loc):
        sc += 1
    return sc


COUNTRY_NAME = {"de": "germany", "at": "austria", "ch": "switzerland", "nl": "netherlands",
                "gb": "united kingdom", "uk": "united kingdom", "ie": "ireland", "us": "united states"}


def passes(job, cfg):
    text = f"{job['title']} {job['description']} {' '.join(job['tags'])}".lower()
    if any(e.lower() in text for e in cfg.get("exclude_keywords", [])):
        return None
    mode = work_mode(job)
    if mode not in [m.lower() for m in cfg.get("work_modes", ["remote", "hybrid", "onsite"])]:
        return None
    lang = cfg.get("language", {}) or {}
    english = is_english(job["description"] or job["title"])
    if lang.get("require_english_posting", False) and not english:
        return None
    if not german_ok(job["description"], lang.get("max_german_level")):
        return None
    # recency: drop stale posts (only when the date is actually known)
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    ts = parse_ts(job["date"])
    if ts and (now - ts) > int(cfg.get("max_age_days", 21)) * 86400:
        return None
    sc = score(job, cfg)
    if sc < cfg.get("min_score", 1):
        return None
    job["_mode"] = mode
    job["_score"] = sc
    job["_lang"] = "en" if english else "other"
    job["_ts"] = ts
    job["_age"] = human_age(ts, now)
    job["_rank"] = sc + recency_bonus(ts, now)   # fresher jobs rank higher
    return job


def dedupe(jobs):
    seen, out = set(), []
    for j in jobs:
        k = (j["title"].lower(), j["company"].lower())
        if k not in seen:
            seen.add(k)
            out.append(j)
    return out


# ----------------------------- render HTML -----------------------------------

def render_html(jobs, cfg):
    now = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lang = cfg.get("language", {}) or {}
    scope = f"{cfg.get('region','?')}"
    if cfg.get("location"):
        scope += f" · {cfg['location']} (+{cfg.get('distance_km',30)}km)"
    scope += f" · modes: {', '.join(cfg.get('work_modes', []))}"
    if lang.get("require_english_posting"):
        scope += f" · English only, German ≤ {lang.get('max_german_level','—')}"

    rows = []
    for i, j in enumerate(jobs):
        rows.append(f"""
  <article class="job" id="job-{i}"
           data-title="{html.escape(j['title'], quote=True)}"
           data-company="{html.escape(j['company'], quote=True)}"
           data-location="{html.escape(j['location'], quote=True)}"
           data-mode="{j.get('_mode','')}" data-score="{j.get('_score','')}"
           data-language="{j.get('_lang','')}" data-source="{j['source']}"
           data-posted-ts="{j.get('_ts','')}" data-posted="{j.get('_age','')}"
           data-url="{html.escape(j['url'], quote=True)}">
    <h2>{html.escape(j['title'])} <span class="co">— {html.escape(j['company'])}</span></h2>
    <p class="meta"><span class="badge">{j.get('_mode','')}</span>
       <span class="badge">fit {j.get('_score','')}</span>
       <span class="badge fresh">🕒 {j.get('_age','')}</span>
       {html.escape(j['location'])} · {j['source']} · {html.escape(j['date'])}
       · <a href="{html.escape(j['url'], quote=True)}">apply link</a></p>
    <div class="jd">{html.escape(j['description']) or '(no description — open the apply link)'}</div>
  </article>""")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Feed — {now}</title>
<style>
  body {{ font: 15px/1.5 system-ui, sans-serif; max-width: 820px; margin: 0 auto; padding: 20px; }}
  header {{ border-bottom: 2px solid #333; margin-bottom: 16px; }}
  .job {{ border: 1px solid #ddd; border-radius: 8px; padding: 14px 16px; margin: 14px 0; }}
  h2 {{ margin: 0 0 4px; font-size: 17px; }}
  .co {{ color: #555; font-weight: 400; }}
  .meta {{ color: #666; font-size: 13px; margin: 0 0 8px; }}
  .badge {{ background:#eee; border-radius: 4px; padding: 1px 6px; margin-right: 4px; font-size:12px; }}
  .badge.fresh {{ background:#e3f2e3; }}
  .jd {{ white-space: pre-wrap; font-size: 14px; color: #222; max-height: 220px; overflow: auto;
         background: #fafafa; padding: 10px; border-radius: 6px; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background:#111; color:#eee; }} .job {{ border-color:#333; }}
    .jd {{ background:#1b1b1b; color:#ddd; }} .meta,.co {{ color:#aaa; }} .badge {{ background:#333; }}
  }}
</style></head><body>
<header>
  <h1>Daily Job Feed</h1>
  <p>{len(jobs)} roles (freshest best-fit first, ≤ {cfg.get('max_age_days',21)} days old,
     capped at {cfg.get('max_jobs',40)}) · generated {now}</p>
  <p class="meta">Scope: {html.escape(scope)}</p>
  <p><em>Career Tailor skill: each job's full description is in its <code>.jd</code> block;
     work-mode, fit score and language are in <code>data-*</code> attributes.</em></p>
</header>
{''.join(rows)}
</body></html>"""


# ----------------------------- main ------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="public/index.html")
    args = ap.parse_args()
    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    raw = []
    for name in cfg.get("sources", ["adzuna", "arbeitnow", "remotive", "jobicy"]):
        fn = SOURCES.get(name)
        if not fn:
            continue
        try:
            got = fn(cfg)
            print(f"  {name}: {len(got)} jobs", file=sys.stderr)
            raw += got
        except Exception as e:
            print(f"  {name}: FAILED ({e})", file=sys.stderr)

    kept = [j for j in (passes(j, cfg) for j in raw) if j]
    # rank = fit score + recency boost; tie-break by newest posting first
    kept.sort(key=lambda j: (-j["_rank"], -j["_ts"]))
    kept = dedupe(kept)[: int(cfg.get("max_jobs", 40))]

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render_html(kept, cfg))
    print(f"Kept {len(kept)} of {len(raw)} raw jobs -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
