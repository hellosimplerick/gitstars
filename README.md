# GitHub Starred Repositories Report

This script exists to turn a long list of GitHub starred repositories into a single readable Markdown report.

GitHub already gives you useful repository views, including repository graphs and insights, Pulse, and community profile information:

- Repository graphs and insights: https://docs.github.com/articles/using-graphs
- Pulse: https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/using-pulse-to-view-a-summary-of-repository-activity
- Community profile: https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/accessing-a-projects-community-profile

Those GitHub features are helpful, but they are mostly designed for looking at one repository at a time. They do not give you a simple, exportable report across all of your starred repositories with a compact side-by-side summary.

This script fills that gap.

## Why This Exists

If you have a large starred list, it becomes hard to answer simple questions such as:

- Which of these repos still look active?
- Which ones seem widely adopted versus niche?
- Which starred projects should I revisit first?
- Which repos look interesting historically, but may now be archived or stale?

This script generates a Markdown report that helps you triage your starred repositories using GitHub-native data only.

It does not claim to perform true sentiment analysis.

Instead, it produces:

- A **Community Signal** summary based on GitHub metrics
- A **Repository Health Snapshot**
- A compact metrics line for each repository
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

- A report across **all** of your starred repositories
- A consistent per-repo snapshot in one document
- Simple, honest classification language like `Very strong community signal` or `Limited community signal`
- Basic repository health indicators such as archived status, fork status, license, topics, watchers, subscribers, and last pushed date
- An output format that is easy to review offline or feed into other lightweight workflows

In short: GitHub gives you the raw repository pages and insights. This script gives you a practical starred-repo review document.

## Target Audience

This may be genuinely useful for:

- developers with hundreds of starred repositories
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
python3 get_starred_repos_with_sentiment.py
```

## Output

The script generates:

```text
starred_repositories_complete.md
```

That Markdown file contains:

- report title
- timestamp
- repository name and GitHub link
- original description
- stars and language
- Community Signal summary
- raw GitHub metrics
- repository health indicators

## Notes

- The report is based on GitHub-native metrics only.
- The wording is intentionally conservative and does not claim external “community sentiment.”
- The script is meant to be simple, standalone, and easy to run.
