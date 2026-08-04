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
    # Cap at the hard per-run ceiling AND at the form's requested max_jobs — never fetch (or bill
    # for) more LinkedIn jobs than the user actually asked to see. At least 1 so a run isn't empty.
    cap = max(1, min(int(cfg.get("max_linkedin", 20)), int(cfg.get("max_jobs", 20))))

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

# Words too generic to count as a keyword signal on their own.
STOPWORDS = {"and", "the", "of", "for", "with", "to", "a", "an", "in", "on", "amp", "or",
             "at", "by", "as", "is", "our", "your", "you", "we", "&", "per", "via", "e.g",
             "using", "based", "across", "into", "within", "role", "team", "work", "working"}


def _tokens(text):
    """Lowercase word/acronym tokens, e.g. 'Retrieval-Augmented Generation (RAG)' ->
    {'retrieval','augmented','generation','rag'}. Keeps 2-char signals like 'ai','ml'."""
    return {w for w in re.findall(r"[a-z0-9+#]+", (text or "").lower()) if len(w) >= 2}


def _signal_terms(cfg):
    """Distinct meaningful tokens drawn from the user's target titles + keyword themes.
    Long phrases contribute their component words, so a job that says 'we build RAG
    pipelines and agentic automation' still scores against 'Retrieval-Augmented
    Generation (RAG)' and 'Agentic Workflows & AI Automation'. Cached on cfg."""
    cached = cfg.get("_signal_terms")
    if cached is not None:
        return cached
    terms = set()
    for phrase in list(cfg.get("target_titles", [])) + list(cfg.get("keyword_themes", [])):
        terms |= _tokens(phrase)
    terms -= STOPWORDS
    cfg["_signal_terms"] = terms
    return terms


def _excluded(text, cfg):
    """True if any exclude term appears as a WHOLE word/phrase. Word boundaries stop
    'Intern' from matching 'international'/'internal', which used to wipe out the feed."""
    for e in cfg.get("exclude_keywords", []):
        e = (e or "").strip().lower()
        if e and re.search(rf"(?<![a-z0-9]){re.escape(e)}(?![a-z0-9])", text):
            return True
    return False


def score(job, cfg):
    job_tokens = _tokens(f"{job['title']} {job['description']} {' '.join(job['tags'])}")
    # +1 per distinct keyword/title word the job actually mentions
    sc = len(_signal_terms(cfg) & job_tokens)
    # strong bonus if a full target title appears verbatim in the posting
    t = f"{job['title']} {job['description']}".lower()
    if any(x.lower() in t for x in cfg.get("target_titles", [])):
        sc += 3
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
# Extra location spellings that should also count as being in-country.
COUNTRY_ALIASES = {"de": ["deutschland"], "at": ["österreich"], "ch": ["schweiz"],
                   "nl": ["nederland", "the netherlands"], "gb": ["uk", "england", "scotland", "wales"],
                   "us": ["usa", "united states of america"]}


def in_region(job, cfg, mode):
    """True if the job belongs in the requested region.

    A specific country was requested (region != REMOTE):
      • remote roles are location-flexible → always kept (they can be done from anywhere);
      • on-site / hybrid roles must actually be in that country (or the given city),
        otherwise a US/UK office job would leak into a Germany search.
    Unknown/blank locations are kept rather than guessed away.
    """
    region = (cfg.get("region") or "").lower()
    if not region or region == "remote":
        return True                      # global search — no country gate
    if mode == "remote":
        return True                      # remote is location-independent
    if job.get("source") in ("Adzuna", "LinkedIn"):
        return True                      # already queried against the requested country
    loc = (job["location"] or "").lower()
    if not loc or "remote" in loc or "anywhere" in loc:
        return True                      # location not concrete enough to reject on
    if cfg.get("location") and cfg["location"].lower() in loc:
        return True                      # matches the requested city
    country = COUNTRY_NAME.get(region, "")
    names = [country] + COUNTRY_ALIASES.get(region, [])
    if any(n and n in loc for n in names):
        return True
    return bool(re.search(rf"\b{re.escape(region)}\b", loc))   # e.g. "Berlin, DE"


def passes(job, cfg):
    text = f"{job['title']} {job['description']} {' '.join(job['tags'])}".lower()
    if _excluded(text, cfg):
        return None
    mode = work_mode(job)
    if mode not in [m.lower() for m in cfg.get("work_modes", ["remote", "hybrid", "onsite"])]:
        return None
    if not in_region(job, cfg, mode):
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

def _fit_label(sc):
    sc = int(sc or 0)
    if sc >= 6:
        return "Strong match"
    if sc >= 4:
        return "Good match"
    return "Fair match"


def _region_label(cfg):
    r = (cfg.get("region") or "").lower()
    if not r or r == "remote":
        return "Global / Remote"
    return COUNTRY_NAME.get(r, cfg.get("region", "")).title()


def render_html(jobs, cfg):
    now = dt.datetime.utcnow().strftime("%d %b %Y, %H:%M UTC")
    lang = cfg.get("language", {}) or {}

    # Human-readable summary of the search (so the page explains itself).
    pills = [("Region", _region_label(cfg))]
    if cfg.get("location"):
        pills.append(("Location", f"{cfg['location']} +{cfg.get('distance_km', 30)}km"))
    pills.append(("Work mode", ", ".join(m.capitalize() for m in cfg.get("work_modes", [])) or "Any"))
    pills.append(("Freshness", f"≤ {cfg.get('max_age_days', 21)} days"))
    if lang.get("require_english_posting"):
        pills.append(("Language", "English only"))
    if lang.get("max_german_level"):
        pills.append(("German", f"≤ {lang.get('max_german_level')}"))
    pills.append(("Min fit", str(cfg.get("min_score", 1))))
    if "linkedin" in (cfg.get("sources") or []):
        pills.append(("LinkedIn", "included"))
    pills_html = "".join(
        f'<span class="pill"><span class="pill-k">{html.escape(k)}</span>'
        f'<span class="pill-v">{html.escape(str(v))}</span></span>'
        for k, v in pills
    )

    cards = []
    for i, j in enumerate(jobs):
        mode = j.get("_mode", "")
        desc = html.escape(j["description"]) or "(no description provided — open the apply link)"
        cards.append(f"""
    <article class="job" id="job-{i}"
             data-title="{html.escape(j['title'], quote=True)}"
             data-company="{html.escape(j['company'], quote=True)}"
             data-location="{html.escape(j['location'], quote=True)}"
             data-mode="{mode}" data-score="{j.get('_score','') or 0}"
             data-language="{j.get('_lang','')}" data-source="{j['source']}"
             data-posted-ts="{j.get('_ts','') or 0}" data-posted="{j.get('_age','')}"
             data-url="{html.escape(j['url'], quote=True)}">
      <div class="job-head">
        <div>
          <h2>{html.escape(j['title'])}</h2>
          <p class="company">{html.escape(j['company'])}</p>
        </div>
        <a class="apply" href="{html.escape(j['url'], quote=True)}" target="_blank" rel="noopener">Apply&nbsp;↗</a>
      </div>
      <div class="badges">
        <span class="badge mode-{mode}">{mode.capitalize() or 'N/A'}</span>
        <span class="badge fit">★ {_fit_label(j.get('_score'))}</span>
        <span class="badge fresh">🕒 {html.escape(j.get('_age',''))}</span>
        <span class="badge loc">📍 {html.escape(j['location'])}</span>
        <span class="badge src">{html.escape(j['source'])}</span>
      </div>
      <details class="jd-wrap">
        <summary>Job description</summary>
        <div class="jd">{desc}</div>
      </details>
    </article>""")

    empty_state = "" if jobs else """
    <div class="empty">
      <h2>No roles matched this search</h2>
      <p>The filters may be too tight. Edit your Job Search Request issue and try:</p>
      <ul>
        <li>Add <strong>Remote</strong> or <strong>Hybrid</strong> to work modes</li>
        <li>Lower the <strong>minimum fit score</strong> (e.g. 1&ndash;2)</li>
        <li>Widen the <strong>freshness window</strong> (e.g. 21&ndash;30 days)</li>
        <li>Broaden your <strong>keywords</strong> or <strong>target titles</strong></li>
      </ul>
    </div>"""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Feed — {now}</title>
<style>
  :root {{
    --bg:#f6f7f9; --card:#fff; --text:#1a1d21; --muted:#5b6570; --border:#e4e7ec;
    --accent:#2563eb; --accent-ink:#fff; --chip:#eef1f5; --shadow:0 1px 3px rgba(0,0,0,.06);
    --remote:#15803d; --remote-bg:#e7f6ec; --hybrid:#1d4ed8; --hybrid-bg:#e8effd;
    --onsite:#c2410c; --onsite-bg:#fdece1;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg:#0e1116; --card:#161a21; --text:#e6e8eb; --muted:#9aa4b2; --border:#262c37;
      --accent:#3b82f6; --chip:#1e242e; --shadow:none;
      --remote:#4ade80; --remote-bg:#12321f; --hybrid:#60a5fa; --hybrid-bg:#152238;
      --onsite:#fb923c; --onsite-bg:#33200f;
    }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    background:var(--bg); color:var(--text); margin:0; }}
  a {{ color:var(--accent); }}
  .wrap {{ max-width:860px; margin:0 auto; padding:22px 16px 60px; }}
  h1 {{ font-size:24px; margin:0 0 2px; letter-spacing:-.02em; }}
  .sub {{ color:var(--muted); font-size:13px; margin:0 0 14px; }}
  .pills {{ display:flex; flex-wrap:wrap; gap:6px; margin:0 0 14px; }}
  .pill {{ display:inline-flex; border:1px solid var(--border); border-radius:7px;
    overflow:hidden; font-size:12px; background:var(--card); }}
  .pill-k {{ background:var(--chip); color:var(--muted); padding:3px 7px; }}
  .pill-v {{ padding:3px 8px; font-weight:600; }}
  .toolbar {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; position:sticky; top:0;
    background:var(--bg); padding:10px 0; z-index:5; border-bottom:1px solid var(--border); }}
  #q {{ flex:1 1 200px; min-width:140px; padding:8px 11px; border:1px solid var(--border);
    border-radius:8px; background:var(--card); color:var(--text); font-size:14px; }}
  #sort {{ padding:8px 10px; border:1px solid var(--border); border-radius:8px;
    background:var(--card); color:var(--text); font-size:13px; }}
  .mfilter {{ display:flex; gap:4px; }}
  .mbtn {{ padding:7px 11px; border:1px solid var(--border); border-radius:8px; cursor:pointer;
    background:var(--card); color:var(--muted); font-size:12px; font-weight:600; }}
  .mbtn.on {{ background:var(--accent); color:var(--accent-ink); border-color:var(--accent); }}
  .count {{ color:var(--muted); font-size:13px; margin:12px 2px 4px; }}
  .job {{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px;
    margin:12px 0; box-shadow:var(--shadow); transition:transform .08s ease, box-shadow .08s ease; }}
  .job:hover {{ transform:translateY(-1px); box-shadow:0 4px 14px rgba(0,0,0,.08); }}
  .job-head {{ display:flex; gap:12px; align-items:flex-start; justify-content:space-between; }}
  .job-head h2 {{ font-size:17px; margin:0 0 2px; line-height:1.3; }}
  .company {{ color:var(--muted); margin:0; font-size:14px; }}
  .apply {{ flex:none; text-decoration:none; background:var(--accent); color:var(--accent-ink);
    padding:8px 14px; border-radius:8px; font-size:13px; font-weight:600; white-space:nowrap; }}
  .apply:hover {{ opacity:.92; }}
  .badges {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }}
  .badge {{ font-size:12px; padding:3px 9px; border-radius:20px; background:var(--chip);
    color:var(--muted); font-weight:600; }}
  .badge.mode-remote {{ color:var(--remote); background:var(--remote-bg); }}
  .badge.mode-hybrid {{ color:var(--hybrid); background:var(--hybrid-bg); }}
  .badge.mode-onsite {{ color:var(--onsite); background:var(--onsite-bg); }}
  .jd-wrap {{ margin-top:12px; border-top:1px solid var(--border); padding-top:10px; }}
  .jd-wrap summary {{ cursor:pointer; color:var(--accent); font-size:13px; font-weight:600; list-style:none; }}
  .jd-wrap summary::-webkit-details-marker {{ display:none; }}
  .jd-wrap summary::before {{ content:"▸ "; }}
  .jd-wrap[open] summary::before {{ content:"▾ "; }}
  .jd {{ white-space:pre-wrap; font-size:13.5px; color:var(--text); margin-top:10px; max-height:340px;
    overflow:auto; background:var(--bg); border:1px solid var(--border); border-radius:8px; padding:12px; }}
  .empty {{ text-align:center; padding:44px 20px; color:var(--muted); }}
  .empty h2 {{ color:var(--text); }}
  .empty ul {{ display:inline-block; text-align:left; margin-top:8px; }}
  footer {{ color:var(--muted); font-size:12px; text-align:center; margin-top:32px;
    border-top:1px solid var(--border); padding-top:14px; }}
  .hidden {{ display:none !important; }}
</style></head>
<body>
<div class="wrap">
  <header>
    <h1>Job Feed</h1>
    <p class="sub">{len(jobs)} roles · newest &amp; best-fit first · generated {now}</p>
    <div class="pills">{pills_html}</div>
  </header>

  <div class="toolbar">
    <input id="q" type="search" placeholder="Search title or company…" aria-label="Search jobs">
    <div class="mfilter" id="mfilter">
      <button type="button" class="mbtn on" data-m="remote">Remote</button>
      <button type="button" class="mbtn on" data-m="hybrid">Hybrid</button>
      <button type="button" class="mbtn on" data-m="onsite">On-site</button>
    </div>
    <select id="sort" aria-label="Sort order">
      <option value="fit">Sort: Best fit</option>
      <option value="fresh">Sort: Newest</option>
    </select>
  </div>
  <p class="count" id="count"></p>

  <main id="list">{''.join(cards)}{empty_state}</main>

  <footer>
    Tip: paste this page's URL into the <strong>Career Tailor</strong> skill in Claude to tailor a
    CV &amp; cover letter for any role above. Each job keeps its full description and machine-readable
    <code>data-*</code> attributes for the skill.
  </footer>
</div>
<script>
(function(){{
  var list=document.getElementById('list');
  var cards=[].slice.call(list.querySelectorAll('.job'));
  var q=document.getElementById('q'), sortSel=document.getElementById('sort');
  var countEl=document.getElementById('count');
  var modes={{remote:true,hybrid:true,onsite:true}};
  function apply(){{
    var term=(q.value||'').toLowerCase(), shown=0;
    cards.forEach(function(c){{
      var t=(c.getAttribute('data-title')+' '+c.getAttribute('data-company')).toLowerCase();
      var m=c.getAttribute('data-mode');
      var ok=(term===''||t.indexOf(term)>-1)&&(modes[m]!==false);
      c.classList.toggle('hidden',!ok);
      if(ok) shown++;
    }});
    countEl.textContent=shown+' of '+cards.length+' roles shown';
  }}
  function sort(){{
    var by=sortSel.value;
    cards.slice().sort(function(a,b){{
      if(by==='fresh') return b.getAttribute('data-posted-ts')-a.getAttribute('data-posted-ts');
      return b.getAttribute('data-score')-a.getAttribute('data-score');
    }}).forEach(function(c){{ list.appendChild(c); }});
  }}
  q.addEventListener('input',apply);
  sortSel.addEventListener('change',function(){{ sort(); apply(); }});
  document.getElementById('mfilter').addEventListener('click',function(e){{
    var b=e.target.closest('.mbtn'); if(!b) return;
    var m=b.getAttribute('data-m'); modes[m]=!modes[m];
    b.classList.toggle('on',modes[m]); apply();
  }});
  apply();
}})();
</script>
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
