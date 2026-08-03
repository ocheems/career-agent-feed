# Security notes

This repository is safe to be **public** because no credentials are ever committed —
they live only in **GitHub Secrets** (encrypted; workflows can decrypt at run time,
nothing else can). The layers below make that "safe" hold up in practice.

## 1 — Nothing credential-shaped can be pushed
- **GitHub secret scanning + push protection** should be enabled in the repo
  (**Settings → Code security**). This is the primary defence.
- `.gitignore` blocks common credential filenames (`.env`, `*.key`, `secrets.*`,
  `credentials.*`, `config.local.*`, `config.private.*`) and generated runtime
  files (`effective_config.yaml`, `public/index.html`).
- `.github/workflows/secrets-guard.yml` runs on every push/PR and fails the
  workflow if a credential-shaped string (Adzuna key value, Apify token, AWS key,
  GitHub PAT, Slack token, `.env`-style `KEY=<long value>` lines) appears in any
  tracked file. Test suite in `scripts/build_documents.py`? no — the guard's
  regex is self-tested in review.

## 2 — Only the owner / collaborators can trigger a scrape
`.github/workflows/on-request.yml` has an `authorise` job that runs first. It
checks the issue author's permission on the repo:

- **Owner** or **admin/maintain/write** collaborator → allowed; the `build` job
  runs, scrapes, deploys, and comments the feed URL.
- **Anyone else** → the workflow posts a polite "not authorised" comment,
  closes the issue, and stops **before** anything spends Apify credit or
  deploys Pages.

This prevents random visitors from opening spam issues to burn your Apify balance.

## 3 — Nothing raw from a submitted issue reaches the public feed
`apply_form.py` only extracts the specific fields defined by the Issue Form
(region, work modes, keywords, etc.) — it never echoes the raw issue body into
the config or the rendered HTML. The Issue Form itself carries a banner warning
users not to paste personal data (this repo and its issues are public).

## 4 — Damage control if a key does leak
Set hard spend caps at the provider so the worst case is bounded:
- **Apify → Settings → Usage → Limits:** set a **monthly cap** (e.g. $5).
- Adzuna's free tier has natural rate limits; monitor the daily runs.
- If a key ever leaks: regenerate it at the provider (Adzuna's dashboard or
  Apify → Settings → Integrations) and update the corresponding secret in
  **Settings → Secrets and variables → Actions**. Takes ~2 minutes each.

## Recommended repo settings (one-time, in the GitHub UI)
- **Settings → Code security:** enable **Secret scanning** and **Push protection**.
- **Settings → Actions → General → Workflow permissions:**
  "Read repository contents and packages permissions" (least privilege).
  Individual `permissions:` blocks in each workflow file grant only what they need.
- **Settings → Actions → General:** *uncheck* "Allow GitHub Actions to create and
  approve pull requests".
- **Settings → Actions → General → Fork pull request workflows from outside
  collaborators:** "Require approval for all outside collaborators".
