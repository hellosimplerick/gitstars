# GitHub Repository Account Report

This script turns several GitHub repository relationships into a single readable Markdown report.

GitHub already gives you useful repository views, including repository graphs and insights, Pulse, and community profile information:

- Repository graphs and insights: https://docs.github.com/articles/using-graphs
- Pulse: https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/using-pulse-to-view-a-summary-of-repository-activity
- Community profile: https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/accessing-a-projects-community-profile

Those GitHub features are helpful, but they are mostly designed for looking at one repository at a time. They do not give you a simple, exportable report across the repositories you star, own, watch, and contribute to.

This script fills that gap.

## Why This Exists

If you have a large GitHub footprint, it becomes hard to answer simple questions such as:

- Which of these repos still look active?
- Which ones seem widely adopted versus niche?
- Which starred projects should I revisit first?
- Which repos do I own but never star?
- Which forks did I create and then ignore?
- Which repos am I watching or contributing to?
- Which repos look interesting historically, but may now be archived or stale?

This script generates a Markdown report that helps you triage those repositories using GitHub-native data only.

It does not claim to perform true sentiment analysis.

Instead, it produces:

- A **Community Signal** summary based on GitHub metrics
- A **Repository Health Snapshot**
- A compact metrics line for each repository
- Separate sections for starred, owned, watched, contributed, and account-only repos
- A single Markdown file you can read, search, save, or share

## What GitHub Already Does

GitHub already provides important repository-level signals such as:

- stars
- forks
- issues
- activity views
- contributor and network graphs
- community profile guidance

For many people, GitHub’s built-in views are enough.

## What This Report Adds

This script is useful because it adds a few things GitHub does not provide in one place by default:

- A report across multiple GitHub relationship categories in one document
- A consistent per-repo snapshot in one document
- Simple, honest classification language like `Very strong community signal` or `Limited community signal`
- Basic repository health indicators such as archived status, fork status, license, topics, watchers, subscribers, and last pushed date
- An output format that is easy to review offline or feed into other lightweight workflows

In short: GitHub gives you the raw repository pages and insights. This script gives you a practical account-level review document.

## Target Audience

This may be genuinely useful for:

- developers with hundreds of GitHub repos across stars, watches, forks, and contributions
- people curating reading lists or project shortlists
- engineers doing lightweight due diligence before revisiting a tool
- open source explorers who want a quick “what still looks alive?” report
- anyone who prefers a single Markdown artifact over clicking through many repo pages

It is less useful if you only have a very small starred list, or if you prefer to inspect each repository directly on GitHub.

## What The Script Uses

The script uses:

- Python 3
- GitHub CLI (`gh`)
- GitHub API data accessed through `gh api`

It does **not** use:

- a virtual environment
- extra Python packages
- AI or LLM APIs
- scraping
- API keys beyond your normal GitHub CLI authentication

## Rate Limiting And Politeness

This repo is intended for real use against GitHub's API, so the script is intentionally polite by default.

It now:

- spaces GitHub API requests by default
- retries rate-limited or transient failures with exponential backoff
- avoids concurrent request bursts
- caches per-repository enrichment data on disk so repeat runs are quieter

Default behavior:

- `0.40` seconds between GitHub API requests
- up to `4` retries after a failed request
- backoff starting at `15` seconds and capping at `120` seconds
- on-disk cache at `.starget-cache.json`
- cache TTL of `24` hours for repo enrichment data

You can override those defaults with environment variables:

```bash
STARGET_REQUEST_DELAY_SECONDS=0.75
STARGET_MAX_RETRIES=5
STARGET_INITIAL_BACKOFF_SECONDS=30
STARGET_MAX_BACKOFF_SECONDS=180
STARGET_CACHE_PATH=.starget-cache.json
STARGET_CACHE_TTL_SECONDS=86400
```

Example:

```bash
STARGET_REQUEST_DELAY_SECONDS=0.75 python3 starget.py
```

If you have a very large account, a slower delay is the safer default.

If you want the fewest repeated API calls possible, keep the cache enabled and avoid deleting the cache file between normal runs.

Please be careful and be nice:

- do not turn the delay down aggressively unless you understand the tradeoff
- do not run many copies of the script at once
- do not disable caching unless you have a real reason
- if GitHub starts rate-limiting you, stop and let the backoff work instead of hammering retry
- if your account has a very large number of repositories, prefer slower settings

## Requirements

You must have **GitHub CLI** installed and authenticated before running the script.

### 1. Install GitHub CLI

Official site:

- https://cli.github.com/

Official manual:

- https://cli.github.com/manual/

GitHub quickstart:

- https://docs.github.com/en/github-cli/github-cli/quickstart

If `gh` is not installed, the script will stop immediately and tell you exactly what to do.

### 2. Authenticate GitHub CLI

Run:

```bash
gh auth login
```

You can verify your login with:

```bash
gh auth status
```

If you are not logged in, the script will stop with a clear error message and tell you to authenticate first.

## How To Run It

There is no setup beyond having `python3` and `gh`.

Run:

```bash
python3 starget.py
```

## Output

The script generates:

```text
yourrepos.md
```

That Markdown file contains:

- report title
- timestamp
- a table of contents
- working guidance for both humans and bots
- a bot usage protocol with retrieval order and decision rules
- prompt starter blocks for repo-first prompting
- a bot-first shortlist for fast downstream retrieval
- a category index that links into a deduplicated repo catalog
- starred repositories
- repos you own but never starred
- forks you created but never starred
- repos you watch
- external repos you contributed to
- your own repos you worked on
- repos in your account that are not already explained by starring, watching, or contributing
- public/private grouping inside each category
- repository name and GitHub link
- relationship badges such as starred, owned, watched, contributed, fork, and private/public
- original description
- stars and language
- Community Signal summary
- raw GitHub metrics, including a clearer distinction between star counts and notification subscribers
- repository health indicators
- a summary section at the end

## Notes

- The report is based on GitHub-native metrics only.
- The wording is intentionally conservative and does not claim external “community sentiment.”
- The script is meant to be simple, standalone, and easy to run.
- The script paces requests and backs off on rate-limit style failures, but larger accounts will still generate more API traffic than smaller ones.
- The disk cache reduces repeated GitHub API calls across runs, but stale or disabled caching will increase traffic.
