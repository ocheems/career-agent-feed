#!/usr/bin/env python3
"""
apply_form.py — turn a submitted GitHub Issue Form (the Job Search Request) into an
effective config.yaml that scrape.py runs with. Merges the form values over the base
config.yaml so anything the form doesn't set keeps its committed default.

Usage (in CI):
    ISSUE_BODY="$(cat issue_body.md)" python apply_form.py \
        --base config.yaml --out effective_config.yaml

The issue body is GitHub's rendered markdown: each field is a "### Label" heading followed
by the value (dropdown = the chosen text; checkboxes = "- [x] Option"; textarea = lines;
empty optional = "_No response_"). We key on the exact labels defined in the issue form.
"""

import argparse
import os
import re
import sys

try:
    import yaml
except ImportError:
    print("pip install pyyaml", file=sys.stderr)
    raise

PAREN_CODE = re.compile(r"\(([A-Za-z]{2,6})\)")


def split_sections(body: str) -> dict:
    out, cur, buf = {}, None, []
    for line in (body or "").splitlines():
        m = re.match(r"^\s*###\s+(.*)$", line)
        if m:
            if cur is not None:
                out[cur] = "\n".join(buf).strip()
            cur, buf = m.group(1).strip(), []
        else:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf).strip()
    return out


def clean(v: str) -> str:
    v = (v or "").strip()
    return "" if v in ("_No response_", "_No response_.") else v


def lines(v: str) -> list:
    return [x.strip("-* \t") for x in clean(v).splitlines() if x.strip("-* \t")]


def checked(v: str) -> list:
    out = []
    for ln in (v or "").splitlines():
        m = re.match(r"^\s*-\s*\[[xX]\]\s*(.+)$", ln)
        if m:
            out.append(m.group(1).strip())
    return out


def region_code(v: str, default="DE") -> str:
    m = PAREN_CODE.search(clean(v))
    return m.group(1).upper() if m else default


def to_int(v, default):
    try:
        return int(re.sub(r"[^0-9]", "", clean(v)))
    except (ValueError, TypeError):
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="config.yaml")
    ap.add_argument("--out", default="effective_config.yaml")
    args = ap.parse_args()

    with open(args.base) as f:
        cfg = yaml.safe_load(f) or {}

    # SAFETY: only PARSED, expected fields from `body` ever reach the effective
    # config or the rendered HTML feed. Never echo the raw ISSUE_BODY into any
    # output — the issue is public and a hostile submitter could paste anything.
    body = os.getenv("ISSUE_BODY", "")
    if not body:
        print("No ISSUE_BODY provided; writing base config unchanged.", file=sys.stderr)
        with open(args.out, "w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
        return

    s = split_sections(body)

    # ── region / location ──
    cfg["region"] = region_code(s.get("Region (country)", ""), cfg.get("region", "DE"))
    loc = clean(s.get("City or location (optional)", ""))
    cfg["location"] = loc

    # ── work modes ──
    modes = [m.lower() for m in checked(s.get("Work modes", ""))]
    modes = [{"on-site": "onsite"}.get(m, m) for m in modes]
    cfg["work_modes"] = modes or ["remote", "hybrid", "onsite"]

    # ── titles / keywords / excludes ──
    titles = lines(s.get("Target job titles (one per line)", ""))
    if titles:
        cfg["target_titles"] = titles
        cfg["search_terms"] = titles[:4]
    kws = lines(s.get("Must-have keywords / your strengths (one per line)", ""))
    if kws:
        cfg["keyword_themes"] = kws
    exc = lines(s.get("Words to exclude (optional, one per line)", ""))
    if exc:
        cfg["exclude_keywords"] = exc

    # ── language ──
    lang = cfg.get("language") or {}
    lang["require_english_posting"] = clean(s.get("Only show English-language job postings?", "Yes")).lower().startswith("y")
    lvl = clean(s.get("Your German level", "B1")) or "B1"
    # Convert "No German" form label back to internal "none" representation
    lvl = "none" if lvl.lower() == "no german" else lvl
    lang["max_german_level"] = lvl
    cfg["language"] = lang
    cfg["apply_language"] = clean(s.get("Application language", "English")) or "English"

    # ── LinkedIn on/off toggle ──
    include_linkedin = clean(s.get("Include LinkedIn jobs?", "Yes")).lower().startswith("y")
    sources = list(cfg.get("sources") or [])
    if include_linkedin and "linkedin" not in sources:
        sources.append("linkedin")
    if not include_linkedin:
        sources = [x for x in sources if x != "linkedin"]
    cfg["sources"] = sources

    # ── quality gate / freshness / cap ──
    cfg["min_score"] = to_int(s.get("Minimum fit score (quality gate)", ""), cfg.get("min_score", 3))
    cfg["max_age_days"] = to_int(s.get("Only jobs posted within", ""), cfg.get("max_age_days", 21))
    cfg["max_jobs"] = to_int(s.get("Maximum number of jobs to show", ""), cfg.get("max_jobs", 40))

    with open(args.out, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)

    print(f"Effective config written to {args.out}:", file=sys.stderr)
    print(f"  region={cfg['region']} location={cfg['location'] or '(any)'} "
          f"modes={cfg['work_modes']} english_only={lang['require_english_posting']} "
          f"german<= {lvl} min_score={cfg['min_score']} max_jobs={cfg['max_jobs']} "
          f"sources={cfg['sources']}", file=sys.stderr)


if __name__ == "__main__":
    main()
