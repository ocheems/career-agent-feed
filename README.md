# Job Feed

A self-hosted, daily-updating job feed. A scheduled GitHub Action pulls postings from several
job APIs, filters them by **region, location, work-mode, language, freshness and keyword-fit**,
and publishes a single static page on **GitHub Pages** with each job's full description embedded.

You drive it entirely from a **GitHub Issue form** — no code or config editing required — or let
it refresh on a daily schedule. The published page is designed to be read by an LLM assistant
(each job carries `data-*` attributes and a full-text description) so you can tailor a CV and
cover letter against a chosen posting.

> This repository contains only the **discovery** half (the feed). Document tailoring is done
> separately by an assistant/skill that reads the published feed.

---

## How it works

```
 Issue form  ──submit──▶  GitHub Actions  ──▶  Job APIs  ──▶  filter/rank  ──▶  GitHub Pages
 (your search)            (apply_form.py)      (5 sources)   (scrape.py)        (index.html)
      ▲                                                                             │
      └───────────────  edit the issue to re-run  ◀──── comment with feed URL ◀─────┘
```

1. You submit a **Job Search Request** issue (a structured form).
2. `apply_form.py` converts your answers into an effective config.
3. `scrape.py` queries the job sources, filters and ranks the results, and writes `index.html`.
4. The page deploys to GitHub Pages; a comment on your issue links to it.

---

## Quick start

You need free accounts at [Adzuna](https://developer.adzuna.com/) and [Apify](https://apify.com/)
for full coverage (both have free tiers). The feed still works without them on the no-key sources.

1. **Create your own copy.** Click **Use this template → Create a new repository** (or fork).
2. **Enable Pages:** *Settings → Pages → Source: **GitHub Actions***.
3. **Enable Actions:** open the **Actions** tab and confirm workflows may run.
4. **Add API keys as secrets** — *Settings → Secrets and variables → Actions → New repository secret*:

   | Secret | Where to get it | Required? |
   | --- | --- | --- |
   | `ADZUNA_APP_ID` | [developer.adzuna.com](https://developer.adzuna.com/) | For region/city coverage |
   | `ADZUNA_APP_KEY` | same | For region/city coverage |
   | `APIFY_TOKEN` | [apify.com](https://apify.com/) → Settings → Integrations | For LinkedIn coverage |
   | `THEIRSTACK_API_KEY` | [theirstack.com](https://theirstack.com/) | Optional |

   Missing keys are skipped gracefully — the feed still builds from the free sources.
5. **Harden the repo** (recommended for public repos): follow [`SECURITY.md`](SECURITY.md).
6. **Generate a feed:** open **Issues → New issue → 🔎 Job Search Request**, fill it in, submit.
   In ~1 minute a comment appears with your feed URL, e.g. `https://<user>.github.io/<repo>/`.

---

## Two ways to generate a feed

- **Issue form (recommended).** Submitting or editing a *Job Search Request* issue runs the
  pipeline with the values you entered. No files to edit. Only the repository owner and
  collaborators can trigger a run (see [`SECURITY.md`](SECURITY.md)).
- **Daily schedule.** `.github/workflows/daily.yml` runs at 06:00 UTC using the committed
  `config.yaml`. You can also trigger it manually from **Actions → Daily Job Feed → Run workflow**.

---

## Configuration reference (`config.yaml`)

The Issue form overrides these per-run; `config.yaml` holds the defaults for the scheduled run.

| Key | Meaning |
| --- | --- |
| `region` | ISO-2 country code (e.g. `DE`, `NL`, `GB`), or `REMOTE` for global remote-first. |
| `location` | Optional city for on-site/hybrid searches (e.g. `Berlin`). Blank = whole country. |
| `distance_km` | Search radius around `location` (location-aware sources). |
| `work_modes` | Any of `remote`, `hybrid`, `onsite`. Jobs outside these modes are dropped. |
| `target_titles` | Titles you're targeting; a match gives a large fit-score boost. |
| `search_terms` | Keywords passed to search-capable sources. |
| `keyword_themes` | Strengths/skills; each match raises a job's fit score. |
| `exclude_keywords` | Postings containing any of these are dropped. |
| `language.require_english_posting` | If `true`, keep only English-language postings. |
| `language.max_german_level` | Drop roles requiring German above this (`none`…`C2`). |
| `min_score` | Minimum fit score to keep a job (quality gate). |
| `max_age_days` | Freshness window; sources are queried newest-first and stale posts dropped. |
| `max_jobs` | Hard cap on the number of jobs published. |
| `sources` | Which sources to query (see below). |
| `max_linkedin` | Hard per-run cap on LinkedIn results (cost ceiling). |

---

## Job sources & cost

| Source | Key needed | Coverage | Cost |
| --- | --- | --- | --- |
| **Adzuna** | free `app_id` + `app_key` | Region + city + on-site/hybrid, salary data | Free |
| **Arbeitnow** | none | EU/Germany board, many English + visa roles | Free |
| **Remotive** | none | Curated remote roles (keyword-searchable) | Free |
| **RemoteOK** | none | Global remote-first | Free |
| **Jobicy** | none | Remote roles with industry tags | Free |
| **LinkedIn** (via Apify) | `APIFY_TOKEN` | LinkedIn job listings | ~$1 / 1,000 jobs, hard-capped |
| **TheirStack** | `THEIRSTACK_API_KEY` | LinkedIn/Indeed/Glassdoor + ATS aggregate | Optional, metered |

**LinkedIn note.** LinkedIn has no official job-search API; listings come via an Apify actor that
runs on Apify's infrastructure (your own LinkedIn account is never used). `max_linkedin` caps each
run at the platform level, and you should also set a monthly usage limit in your Apify account.

Running only the free sources costs nothing. Adding LinkedIn at ~50 jobs/day is roughly
$1.50/month and often covered by Apify's free monthly credit.

---

## Security

This repository is safe to be public — **no credentials are ever committed**; API keys live only
in GitHub Secrets. Additional hardening (secret scanning, a credential-guard workflow, an
authorised-user gate on the Issue trigger) is documented in [`SECURITY.md`](SECURITY.md). Please
read it before making a repository public.

---

## Local development

```bash
pip install -r requirements.txt

# Run with the committed defaults
python scrape.py --config config.yaml --out public/index.html

# Simulate an Issue-form submission
ISSUE_BODY="$(cat sample_issue.md)" python apply_form.py --base config.yaml --out effective_config.yaml
python scrape.py --config effective_config.yaml --out public/index.html
```

`effective_config.yaml` and `public/index.html` are generated artifacts and are git-ignored.

---

## Repository layout

```
.github/
  ISSUE_TEMPLATE/job-search-request.yml   # the search form
  workflows/daily.yml                      # scheduled run
  workflows/on-request.yml                 # runs on issue submit (with an authorised-user gate)
  workflows/secrets-guard.yml              # fails any push containing a credential-shaped string
scrape.py            # fetch, filter, rank, render the feed
apply_form.py        # turn a submitted Issue form into an effective config
config.yaml          # default parameters for the scheduled run
requirements.txt
SECURITY.md
```

---

## Legal note

Only sources that expose an API or an authorised aggregator are used. Direct scraping of job
boards that prohibit it (e.g. logging into LinkedIn and scraping) is intentionally **not** done,
as it violates their terms and risks account bans. Respect each provider's terms of service and
rate limits.
