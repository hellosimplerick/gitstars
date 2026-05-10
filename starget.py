#!/usr/bin/env python3
"""
Script to fetch multiple GitHub repository categories and generate a markdown
report with GitHub-native community signals and repository health snapshots.
This is a standalone script that does everything from scratch.

Usage:
    python3 starget.py

Requirements:
    - GitHub CLI (gh) installed and authenticated
    - Python 3.x
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Set


REPO_JQ = (
    ".[] | {full_name: .full_name, description: .description, html_url: .html_url, "
    "stargazers_count: .stargazers_count, language: .language, fork: .fork, "
    "private: .private}"
)
REQUEST_DELAY_SECONDS = float(os.getenv("STARGET_REQUEST_DELAY_SECONDS", "0.40"))
MAX_RETRIES = int(os.getenv("STARGET_MAX_RETRIES", "4"))
INITIAL_BACKOFF_SECONDS = float(os.getenv("STARGET_INITIAL_BACKOFF_SECONDS", "15"))
MAX_BACKOFF_SECONDS = float(os.getenv("STARGET_MAX_BACKOFF_SECONDS", "120"))
CACHE_PATH = os.getenv("STARGET_CACHE_PATH", ".starget-cache.json")
CACHE_TTL_SECONDS = int(os.getenv("STARGET_CACHE_TTL_SECONDS", str(24 * 60 * 60)))
_last_request_started_at: Optional[float] = None


def print_fatal_error(message: str, details: Optional[List[str]] = None) -> None:
    """Print a clear fatal error message and exit."""
    print("\nERROR: " + message, file=sys.stderr)
    if details:
        for detail in details:
            print(f"  - {detail}", file=sys.stderr)
    sys.exit(1)


def ensure_gh_is_ready() -> None:
    """Verify GitHub CLI is installed and authenticated before doing any work."""
    if shutil.which("gh") is None:
        print_fatal_error(
            "GitHub CLI ('gh') is not installed or is not available on your PATH.",
            [
                "Install GitHub CLI from https://cli.github.com/ or your system package manager.",
                "After installing, confirm it works by running: gh --version",
                "Then authenticate by running: gh auth login",
            ],
        )

    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stdout.strip():
            print("GitHub CLI authentication looks good.")
        return
    except subprocess.CalledProcessError as e:
        error_output = (e.stderr or e.stdout or "").strip()
        print_fatal_error(
            "GitHub CLI is installed, but you are not authenticated.",
            [
                "Run: gh auth login",
                "After logging in, verify your session with: gh auth status",
                f"GitHub CLI said: {error_output or 'authentication status check failed'}",
            ],
        )
    except Exception as e:
        print_fatal_error(
            "Unable to verify GitHub CLI authentication status.",
            [
                "Try running: gh auth status",
                f"Underlying error: {str(e)}",
            ],
        )


def wait_for_request_slot() -> None:
    """Pace GitHub API requests to stay polite by default."""
    global _last_request_started_at

    if REQUEST_DELAY_SECONDS <= 0:
        _last_request_started_at = time.monotonic()
        return

    now = time.monotonic()
    if _last_request_started_at is not None:
        elapsed = now - _last_request_started_at
        remaining = REQUEST_DELAY_SECONDS - elapsed
        if remaining > 0:
            time.sleep(remaining)

    _last_request_started_at = time.monotonic()


def is_rate_limit_error(error_text: str) -> bool:
    """Detect primary or secondary rate limiting responses from GitHub."""
    normalized = error_text.lower()
    indicators = [
        "rate limit",
        "secondary rate limit",
        "abuse detection",
        "retry-after",
        "too many requests",
        "http 403",
        "http 429",
    ]
    return any(indicator in normalized for indicator in indicators)


def is_transient_error(error_text: str) -> bool:
    """Detect transient failures worth retrying."""
    normalized = error_text.lower()
    indicators = [
        "timed out",
        "timeout",
        "connection reset",
        "connection refused",
        "temporary failure",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
        "http 502",
        "http 503",
        "http 504",
    ]
    return any(indicator in normalized for indicator in indicators)


def format_command(args: List[str]) -> str:
    """Format a command list for readable logging."""
    return " ".join(shlex.quote(arg) for arg in args)


def run_gh_command(command: List[str]) -> str:
    """Run a GitHub CLI command with pacing and limited retry/backoff."""
    max_attempts = max(1, MAX_RETRIES + 1)
    command_display = format_command(command)

    for attempt in range(1, max_attempts + 1):
        wait_for_request_slot()

        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=True
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            error_text = ((e.stderr or "") + "\n" + (e.stdout or "")).strip()
            retryable = is_rate_limit_error(error_text) or is_transient_error(error_text)

            if retryable and attempt < max_attempts:
                backoff_seconds = min(
                    INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1)),
                    MAX_BACKOFF_SECONDS,
                )
                print(
                    (
                        f"GitHub request was throttled or transiently failed. "
                        f"Backing off for {backoff_seconds:.0f}s before retry "
                        f"{attempt + 1}/{max_attempts}."
                    ),
                    file=sys.stderr,
                )
                time.sleep(backoff_seconds)
                continue

            print(f"Error running command: {command_display}", file=sys.stderr)
            print(f"Error: {error_text}", file=sys.stderr)
            return ""

    return ""


def parse_json_lines(output: str) -> List[Dict]:
    """Parse newline-delimited JSON records."""
    items = []
    for line in output.strip().split("\n"):
        if not line.strip():
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def load_disk_cache() -> Dict[str, Dict]:
    """Load the on-disk repo metadata cache."""
    if not CACHE_PATH:
        return {}

    if not os.path.exists(CACHE_PATH):
        return {}

    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
        if isinstance(cache, dict):
            return cache
    except (OSError, json.JSONDecodeError) as e:
        print(f"Warning: unable to load cache file {CACHE_PATH}: {e}", file=sys.stderr)

    return {}


def save_disk_cache(cache: Dict[str, Dict]) -> None:
    """Persist the repo metadata cache to disk atomically."""
    if not CACHE_PATH:
        return

    temp_path = f"{CACHE_PATH}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
        os.replace(temp_path, CACHE_PATH)
    except OSError as e:
        print(f"Warning: unable to save cache file {CACHE_PATH}: {e}", file=sys.stderr)


def is_cache_entry_fresh(entry: Dict) -> bool:
    """Return whether a cached repo metadata entry is still fresh."""
    if CACHE_TTL_SECONDS <= 0:
        return False

    fetched_at = entry.get("fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return False

    return (time.time() - fetched_at) < CACHE_TTL_SECONDS


def get_cached_signal(repo_name: str, disk_cache: Dict[str, Dict]) -> Optional[Dict]:
    """Return cached signal data when available and fresh."""
    entry = disk_cache.get(repo_name)
    if not isinstance(entry, dict):
        return None

    signal = entry.get("signal")
    if not isinstance(signal, dict):
        return None

    if not is_cache_entry_fresh(entry):
        return None

    return signal


def update_disk_cache(repo_name: str, signal: Dict, disk_cache: Dict[str, Dict]) -> None:
    """Update the on-disk cache entry for one repository."""
    disk_cache[repo_name] = {
        "fetched_at": time.time(),
        "signal": signal,
    }


def dedupe_repos(repos: List[Dict]) -> List[Dict]:
    """Deduplicate repositories by full name while preserving first-seen order."""
    seen: Set[str] = set()
    deduped = []

    for repo in repos:
        full_name = repo.get("full_name")
        if not full_name or full_name in seen:
            continue
        seen.add(full_name)
        deduped.append(repo)

    return deduped


def merge_repo_records(base_repo: Dict, incoming_repo: Dict) -> Dict:
    """Merge two repo records, preferring non-empty incoming values."""
    merged = dict(base_repo)
    for key, value in incoming_repo.items():
        if value not in (None, "", []):
            merged[key] = value
    return merged


def fetch_rest_repos(path: str, label: str) -> List[Dict]:
    """Fetch repositories from a REST endpoint using a shared repo shape."""
    print(f"Fetching {label} from GitHub...")

    command = ["gh", "api", path, "--paginate", "--jq", REPO_JQ]
    output = run_gh_command(command)

    repos = dedupe_repos(parse_json_lines(output))
    print(f"Found {len(repos)} {label}.")
    return repos


def get_current_user_login() -> str:
    """Get the authenticated GitHub username."""
    output = run_gh_command(["gh", "api", "user", "--jq", ".login"])
    login = output.strip()
    if not login:
        print_fatal_error(
            "Unable to determine the authenticated GitHub username.",
            [
                "Try running: gh auth status",
                "Then verify the API works with: gh api user",
            ],
        )
    return login


def get_starred_repos() -> List[Dict]:
    """Get the list of starred repositories with basic details."""
    return fetch_rest_repos("user/starred", "starred repositories")


def get_owned_repos() -> List[Dict]:
    """Get repositories owned by the authenticated user."""
    return fetch_rest_repos("user/repos?affiliation=owner&per_page=100", "owned repositories")


def get_watched_repos() -> List[Dict]:
    """Get repositories watched by the authenticated user."""
    return fetch_rest_repos("user/subscriptions", "watched repositories")


def get_contributed_repos() -> List[Dict]:
    """Get repositories the authenticated user has contributed to."""
    print("Fetching contributed repositories from GitHub...")

    query = (
        "query($endCursor: String) { "
        "viewer { "
        "repositoriesContributedTo(first: 100, includeUserRepositories: true, after: $endCursor) { "
        "nodes { "
        "nameWithOwner description url stargazerCount isFork isPrivate "
        "primaryLanguage { name } "
        "} "
        "pageInfo { hasNextPage endCursor } "
        "} "
        "} "
        "}"
    )
    jq = (
        ".data.viewer.repositoriesContributedTo.nodes[] | "
        "{full_name: .nameWithOwner, description: .description, html_url: .url, "
        "stargazers_count: .stargazerCount, language: .primaryLanguage.name, "
        "fork: .isFork, private: .isPrivate}"
    )
    command = ["gh", "api", "graphql", "--paginate", "-f", f"query={query}", "--jq", jq]
    output = run_gh_command(command)

    repos = dedupe_repos(parse_json_lines(output))
    print(f"Found {len(repos)} contributed repositories.")
    return repos


def filter_repos_by_owner_prefix(repos: List[Dict], owner_login: str) -> List[Dict]:
    """Return repositories owned by the given login."""
    owner_prefix = f"{owner_login}/"
    return [
        repo
        for repo in repos
        if isinstance(repo.get("full_name"), str)
        and repo["full_name"].startswith(owner_prefix)
    ]


def safe_int(value) -> int:
    """Convert a value to int when possible, otherwise return 0."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_github_datetime(date_str: Optional[str]) -> Optional[datetime]:
    """Parse a GitHub ISO timestamp safely."""
    if not date_str:
        return None

    try:
        return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def format_date(date_str: Optional[str]) -> str:
    """Format a GitHub date string as YYYY-MM-DD when possible."""
    parsed = parse_github_datetime(date_str)
    if not parsed:
        return "Unknown"
    return parsed.strftime("%Y-%m-%d")


def classify_community_signal(repo_data: Dict) -> str:
    """Classify a repository using GitHub-native metrics only."""
    stars = safe_int(repo_data.get("stargazers_count"))
    forks = safe_int(repo_data.get("forks_count"))
    open_issues = safe_int(repo_data.get("open_issues_count"))
    archived = bool(repo_data.get("archived", False))
    disabled = bool(repo_data.get("disabled", False))
    pushed_at = parse_github_datetime(repo_data.get("pushed_at"))

    if disabled:
        return (
            "Disabled project. GitHub indicates the repository is disabled, so it "
            "is unlikely to be an active destination for contributors."
        )

    if archived:
        return (
            "Archived project. Useful historically, but likely not actively "
            "maintained."
        )

    stale = False
    if pushed_at:
        stale = (datetime.utcnow() - pushed_at).days > 365

    if stars >= 10000 or (stars >= 5000 and forks >= 500):
        return (
            "Very strong community signal. High stars and meaningful fork activity "
            "suggest broad developer interest."
        )

    if stars >= 1000 or forks >= 100:
        if stale:
            return (
                "Strong community signal historically. The project has significant "
                "visibility, though recent push activity appears limited."
            )
        return (
            "Strong community signal. The project has significant visibility and "
            "developer interest."
        )

    if stars >= 100 or forks >= 20:
        if stale and open_issues > 100:
            return (
                "Moderate community signal. The project appears known within its "
                "niche, though maintenance signals look mixed."
            )
        return (
            "Moderate community signal. The project appears known within its niche."
        )

    if stale:
        return (
            "Limited community signal. The project may be niche or lightly adopted, "
            "and recent activity appears limited."
        )

    return (
        "Limited community signal. The project may be new, niche, inactive, or "
        "lightly adopted."
    )


def get_community_signal(
    repo_name: str,
    cache: Dict[str, Dict],
    disk_cache: Dict[str, Dict],
) -> Dict:
    """
    Fetch GitHub-native repository metrics and return a community signal snapshot.
    """
    if repo_name in cache:
        return cache[repo_name]

    cached_signal = get_cached_signal(repo_name, disk_cache)
    if cached_signal is not None:
        cache[repo_name] = cached_signal
        return cached_signal

    default_result = {
        "summary": "Community signal data not available.",
        "metrics_line": (
            "**Metrics**: Unknown stars | Unknown forks | Unknown open issues | "
            "Stars API watchers: Unknown | Notification subscribers: Unknown | "
            "Last pushed: Unknown"
        ),
        "archived": "Unknown",
        "fork": "Unknown",
        "license": "None",
        "topics": [],
        "updated_at": "Unknown",
    }

    try:
        fields = (
            "stargazers_count, forks_count, open_issues_count, watchers_count, "
            "subscribers_count, archived, disabled, fork, pushed_at, updated_at, "
            "license, topics"
        )
        command = ["gh", "api", f"repos/{repo_name}", "--jq", f"{{{fields}}}"]
        result = run_gh_command(command)

        if not result:
            cache[repo_name] = default_result
            update_disk_cache(repo_name, default_result, disk_cache)
            return default_result

        repo_data = json.loads(result)

        stars = safe_int(repo_data.get("stargazers_count"))
        forks = safe_int(repo_data.get("forks_count"))
        open_issues = safe_int(repo_data.get("open_issues_count"))
        stars_api_watchers = safe_int(repo_data.get("watchers_count"))
        subscribers = safe_int(repo_data.get("subscribers_count"))
        archived = bool(repo_data.get("archived", False))
        is_fork = bool(repo_data.get("fork", False))
        pushed_at = format_date(repo_data.get("pushed_at"))
        updated_at = format_date(repo_data.get("updated_at"))

        license_info = repo_data.get("license") or {}
        if isinstance(license_info, dict):
            license_name = license_info.get("spdx_id") or license_info.get("name") or "None"
        else:
            license_name = "None"

        topics = repo_data.get("topics") or []
        if not isinstance(topics, list):
            topics = []

        signal = {
            "summary": classify_community_signal(repo_data),
            "metrics_line": (
                f"**Metrics**: {stars:,} stars | {forks:,} forks | "
                f"{open_issues:,} open issues | Stars API watchers: "
                f"{stars_api_watchers:,} | Notification subscribers: "
                f"{subscribers:,} | Last pushed: {pushed_at}"
            ),
            "archived": "yes" if archived else "no",
            "fork": "yes" if is_fork else "no",
            "license": license_name,
            "topics": topics,
            "updated_at": updated_at,
        }
        cache[repo_name] = signal
        update_disk_cache(repo_name, signal, disk_cache)
        return signal
    except json.JSONDecodeError as e:
        signal = {
            **default_result,
            "summary": f"Could not parse repository metrics: {str(e)}",
        }
        cache[repo_name] = signal
        update_disk_cache(repo_name, signal, disk_cache)
        return signal
    except Exception as e:
        signal = {
            **default_result,
            "summary": f"Could not retrieve community signal: {str(e)}",
        }
        cache[repo_name] = signal
        update_disk_cache(repo_name, signal, disk_cache)
        return signal


def repo_name_set(repos: List[Dict]) -> Set[str]:
    """Return repository full names as a set."""
    return {
        repo.get("full_name")
        for repo in repos
        if repo.get("full_name")
    }


def filter_repos_excluding(repos: List[Dict], excluded_names: Set[str]) -> List[Dict]:
    """Filter repositories by full name exclusion."""
    return [
        repo
        for repo in repos
        if repo.get("full_name") and repo.get("full_name") not in excluded_names
    ]


def slugify(value: str) -> str:
    """Create a markdown anchor-friendly slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "repo"


def build_repo_catalog(sections: List[Dict]) -> Dict[str, Dict]:
    """Build a deduplicated repo catalog across all sections."""
    catalog: Dict[str, Dict] = {}
    for section in sections:
        for repo in section["repos"]:
            full_name = repo.get("full_name")
            if not full_name:
                continue
            if full_name in catalog:
                catalog[full_name] = merge_repo_records(catalog[full_name], repo)
            else:
                catalog[full_name] = dict(repo)
    return catalog


def build_relationship_map(sections: List[Dict]) -> Dict[str, List[str]]:
    """Map each repo full name to the section titles it belongs to."""
    relationships: Dict[str, List[str]] = {}
    for section in sections:
        title = section["title"]
        for repo in section["repos"]:
            full_name = repo.get("full_name")
            if not full_name:
                continue
            relationships.setdefault(full_name, [])
            if title not in relationships[full_name]:
                relationships[full_name].append(title)
    return relationships


def repo_anchor(full_name: str) -> str:
    """Return a stable markdown anchor for a repo profile."""
    return f"repo-{slugify(full_name)}"


def format_repo_badges(repo: Dict, relation_titles: List[str]) -> str:
    """Render compact badges describing repo state and relationships."""
    badges = []

    badges.append("`PRIVATE`" if repo.get("private") else "`PUBLIC`")
    if repo.get("fork"):
        badges.append("`FORK`")

    relation_badges = {
        "Starred Repositories": "`STARRED`",
        "Repos I Own But Never Starred": "`OWNED`",
        "Forks I Created But Never Starred": "`OWNED-FORK`",
        "Repos I Watch": "`WATCHED`",
        "External Repos I Contributed To": "`CONTRIBUTED`",
        "My Own Repos I Worked On": "`OWNED-CONTRIBUTED`",
        "Repos In My GitHub Account Just Because They Exist There": "`ACCOUNT-ONLY`",
    }
    for title in relation_titles:
        badge = relation_badges.get(title)
        if badge and badge not in badges:
            badges.append(badge)

    return " ".join(badges)


def repo_sort_key(repo: Dict) -> tuple:
    """Sort by stars descending, then repo name."""
    return (-safe_int(repo.get("stargazers_count")), repo.get("full_name", ""))


def render_repo_list_group(
    repos: List[Dict],
    relationship_map: Dict[str, List[str]],
    heading: str,
) -> str:
    """Render a compact grouped list of repos."""
    if not repos:
        return ""

    markdown = f"### {heading}\n\n"
    for repo in sorted(repos, key=repo_sort_key):
        full_name = repo.get("full_name", "Unknown")
        description = (
            repo.get("description", "No description available.")
            or "No description available."
        )
        short_description = description.replace("\n", " ").strip()
        language = repo.get("language", "Unknown") or "Unknown"
        stars = safe_int(repo.get("stargazers_count"))
        badges = format_repo_badges(repo, relationship_map.get(full_name, []))
        markdown += (
            f"- [{full_name}](#{repo_anchor(full_name)}) {badges} "
            f"Stars: {stars:,} | Language: {language} | {short_description}\n"
        )
    markdown += "\n"
    return markdown


def render_repo_section(title: str, repos: List[Dict], relationship_map: Dict[str, List[str]]) -> str:
    """Render one markdown section as a compact index into deduplicated profiles."""
    markdown = f"## {title}\n\n"

    if not repos:
        markdown += "No repositories found in this category.\n\n"
        return markdown

    public_repos = [repo for repo in repos if not repo.get("private")]
    private_repos = [repo for repo in repos if repo.get("private")]

    markdown += (
        f"Count: {len(repos)} total | {len(public_repos)} public | "
        f"{len(private_repos)} private\n\n"
    )
    markdown += render_repo_list_group(public_repos, relationship_map, "Public")
    markdown += render_repo_list_group(private_repos, relationship_map, "Private")
    return markdown


def render_repo_profile(
    repo: Dict,
    relation_titles: List[str],
    signal_cache: Dict[str, Dict],
    disk_cache: Dict[str, Dict],
) -> str:
    """Render the full profile block for one unique repo."""
    full_name = repo.get("full_name", "Unknown")
    description = (
        repo.get("description", "No description available.")
        or "No description available."
    )
    html_url = repo.get("html_url", "#")
    stars = safe_int(repo.get("stargazers_count"))
    language = repo.get("language", "Unknown") or "Unknown"
    badges = format_repo_badges(repo, relation_titles)

    markdown = f"### {full_name}\n\n"
    markdown += f"<a id=\"{repo_anchor(full_name)}\"></a>\n\n"
    markdown += f"**Repo**: [{full_name}]({html_url}) {badges}\n\n"
    markdown += f"**Relationships**: {', '.join(relation_titles)}\n\n"

    signal_data = get_community_signal(full_name, signal_cache, disk_cache)
    markdown += f"**Community Signal**: {signal_data['summary']}\n\n"
    markdown += f"{signal_data['metrics_line']}\n\n"
    markdown += (
        f"**Repository Health Snapshot**: Archived: {signal_data['archived']} | "
        f"Fork: {signal_data['fork']} | License: {signal_data['license']} | "
        f"Last updated: {signal_data['updated_at']}\n\n"
    )

    topics = signal_data.get("topics") or []
    if topics:
        markdown += f"**Topics**: {', '.join(topics)}\n\n"

    markdown += f"**Description**: {description}\n\n"
    markdown += f"**Local Snapshot**: Stars: {stars:,} | Language: {language}\n\n"
    markdown += "---\n\n"
    return markdown


def render_table_of_contents(sections: List[Dict]) -> str:
    """Render a markdown table of contents."""
    markdown = "## Table of Contents\n\n"
    markdown += "- [Working Guidance](#working-guidance)\n"
    markdown += "- [Bot Usage Protocol](#bot-usage-protocol)\n"
    markdown += "- [Prompt Starters](#prompt-starters)\n"
    markdown += "- [Bot First Shortlist](#bot-first-shortlist)\n"
    markdown += "- [Category Index](#category-index)\n"
    for section in sections:
        markdown += f"- [{section['title']}](#{slugify(section['title'])})\n"
    markdown += "- [Repository Profiles](#repository-profiles)\n"
    markdown += "- [Summary](#summary)\n\n"
    return markdown


def render_summary(sections: List[Dict], total_unique_repos: int) -> str:
    """Render the end-of-file summary."""
    markdown = "## Summary\n\n"
    markdown += f"- Unique repositories across all categories: {total_unique_repos}\n"
    for section in sections:
        markdown += f"- {section['title']}: {len(section['repos'])}\n"
    markdown += "\n"
    return markdown


def choose_shortlist_repos(sections: List[Dict], viewer_login: str) -> List[Dict]:
    """Choose a compact repo-first shortlist for downstream bots."""
    section_map = {section["title"]: section["repos"] for section in sections}
    eligible_titles = [
        "External Repos I Contributed To",
        "Repos I Watch",
        "Starred Repositories",
    ]

    candidates: Dict[str, Dict] = {}
    relation_map: Dict[str, Set[str]] = {}
    for title in eligible_titles:
        for repo in section_map.get(title, []):
            full_name = repo.get("full_name")
            if not full_name or full_name.startswith(f"{viewer_login}/"):
                continue
            if full_name not in candidates:
                candidates[full_name] = repo
            relation_map.setdefault(full_name, set()).add(title)

    def shortlist_score(repo: Dict) -> tuple:
        full_name = repo.get("full_name", "")
        relations = relation_map.get(full_name, set())
        description = (
            repo.get("description", "No description available.")
            or "No description available."
        ).strip()
        has_external_contributed = "External Repos I Contributed To" in relations
        has_watched = "Repos I Watch" in relations
        has_starred = "Starred Repositories" in relations
        has_overlap = sum(
            [has_external_contributed, has_watched, has_starred]
        )
        is_public = not bool(repo.get("private"))
        has_description = description != "No description available."
        stars = safe_int(repo.get("stargazers_count"))

        return (
            has_external_contributed,
            has_watched,
            has_starred,
            has_overlap,
            is_public,
            has_description,
            stars,
            repo.get("language") != "Unknown",
            full_name,
        )

    ranked = sorted(
        candidates.values(),
        key=shortlist_score,
        reverse=True,
    )
    return ranked[:20]


def render_shortlist(
    repos: List[Dict],
    relationship_map: Dict[str, List[str]],
) -> str:
    """Render a compact, high-signal shortlist near the top of the report."""
    markdown = "## Bot First Shortlist\n\n"
    markdown += (
        "This is the smallest high-signal candidate set for downstream bots. "
        "Start here before scanning the full category indexes.\n\n"
    )

    if not repos:
        markdown += "No shortlist candidates were found.\n\n"
        return markdown

    for repo in repos:
        full_name = repo.get("full_name", "Unknown")
        language = repo.get("language", "Unknown") or "Unknown"
        stars = safe_int(repo.get("stargazers_count"))
        description = (
            repo.get("description", "No description available.")
            or "No description available."
        ).replace("\n", " ").strip()
        badges = format_repo_badges(repo, relationship_map.get(full_name, []))
        markdown += (
            f"- [{full_name}](#{repo_anchor(full_name)}) {badges} "
            f"Stars: {stars:,} | Language: {language} | {description}\n"
        )

    markdown += "\n"
    return markdown


def render_bot_usage_protocol() -> str:
    """Render explicit instructions for downstream bots consuming the file."""
    markdown = "## Bot Usage Protocol\n\n"
    markdown += (
        "Use this file as a first-pass retrieval source before widening scope to the "
        "internet or generic package discovery.\n\n"
    )
    markdown += "### Retrieval Order\n\n"
    markdown += "1. Read `Working Guidance` and `Category Index` first.\n"
    markdown += "2. Prefer repositories tagged `STARRED`, `WATCHED`, or `CONTRIBUTED` as the first candidate set.\n"
    markdown += "3. Use `OWNED`, `OWNED-FORK`, and `ACCOUNT-ONLY` tags for internal tools, prior work, forks, or dormant assets.\n"
    markdown += "4. Open only the linked `Repository Profiles` that match the task.\n"
    markdown += "5. Expand to broader internet search only if this file does not provide a plausible starting set.\n\n"
    markdown += "### Decision Rules\n\n"
    markdown += "- If the task asks what to use, rank repos from this file before suggesting unfamiliar external repos.\n"
    markdown += "- If the task asks how to build something, inspect relevant repos from this file before proposing libraries from memory.\n"
    markdown += "- If multiple repos fit, bias toward repos with stronger relationship signals: `CONTRIBUTED`, `WATCHED`, `STARRED`, then weaker signals.\n"
    markdown += "- If a repo here is stale or archived, note that explicitly before recommending it.\n"
    markdown += "- If you leave this file and search externally, say why the local repo set was insufficient.\n\n"
    markdown += "### Output Contract For Bots\n\n"
    markdown += "- Name the repos from this file that you considered.\n"
    markdown += "- State why each chosen repo is relevant to the task.\n"
    markdown += "- State whether broader search was avoided or required.\n"
    markdown += "- Keep recommendations grounded in repository names and relationship tags from this report.\n\n"
    return markdown


def render_prompt_starters(viewer_login: str) -> str:
    """Render canned prompts that force repo-first reasoning."""
    markdown = "## Prompt Starters\n\n"
    markdown += (
        "These prompts are written so another bot starts with repositories from this "
        f"report for `{viewer_login}` before searching elsewhere.\n\n"
    )
    markdown += "```text\n"
    markdown += (
        "Use `yourrepos.md` as the primary source of candidate repositories. "
        "Start with repos tagged STARRED, WATCHED, or CONTRIBUTED. Only broaden "
        "to internet search if those repos do not provide a credible path.\n"
    )
    markdown += "```\n\n"
    markdown += "```text\n"
    markdown += (
        "I want to solve a problem using repositories I already know about first. "
        "Read `yourrepos.md`, identify the most relevant repos by relationship tag "
        "and profile details, and explain which ones you would inspect before "
        "looking for new tools online.\n"
    )
    markdown += "```\n\n"
    markdown += "```text\n"
    markdown += (
        "Given `yourrepos.md`, shortlist the best existing repos for this task. "
        "List the repo names, why they fit, what category they came from, and what "
        "extra search would still be needed after checking them.\n"
    )
    markdown += "```\n\n"
    markdown += "```text\n"
    markdown += (
        "Before recommending any new library or service, inspect the repos in "
        "`yourrepos.md` that are STARRED, WATCHED, CONTRIBUTED, or OWNED. Tell me "
        "which known repos should be checked first and why.\n"
    )
    markdown += "```\n\n"
    markdown += "```text\n"
    markdown += (
        "Use `yourrepos.md` as a retrieval index. Find the repo profiles most likely "
        "to help with this task, summarize the relevant ones, and only then decide "
        "whether external search is justified.\n"
    )
    markdown += "```\n\n"
    return markdown


def generate_markdown_report(
    viewer_login: str,
    sections: List[Dict],
    total_unique_repos: int,
    disk_cache: Dict[str, Dict],
) -> str:
    """Generate a markdown report from multiple repository categories."""
    relationship_map = build_relationship_map(sections)
    repo_catalog = build_repo_catalog(sections)
    unique_repos = sorted(repo_catalog.values(), key=repo_sort_key)

    markdown = f"# GitHub Repository Report for {viewer_login}\n\n"
    markdown += f"*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n"
    markdown += render_table_of_contents(sections)
    markdown += "## Working Guidance\n\n"
    markdown += (
        "This report groups repositories by how they relate to your GitHub account. "
        "Category sections are compact indexes. Full repository details appear once "
        "in the deduplicated `Repository Profiles` section.\n\n"
    )
    markdown += (
        "- For humans: scan the category index first, then jump to the repo profiles "
        "that look relevant.\n"
    )
    markdown += (
        "- For bots: prefer the repositories listed in this report as candidate tools, "
        "libraries, references, or prior art before expanding to broader internet search.\n"
    )
    markdown += (
        "- For prompting: mention the specific repo names and relationship tags from this "
        "file so follow-on prompts stay grounded in repositories you already know about.\n\n"
    )
    markdown += render_bot_usage_protocol()
    markdown += render_prompt_starters(viewer_login)
    markdown += render_shortlist(
        choose_shortlist_repos(sections, viewer_login),
        relationship_map,
    )

    markdown += "## Category Index\n\n"
    markdown += (
        "Each category groups repositories by relationship. Entries link to a single "
        "deduplicated profile later in the document.\n\n"
    )

    print("Generating markdown report with GitHub community signals...")
    for section in sections:
        markdown += render_repo_section(section["title"], section["repos"], relationship_map)

    markdown += "## Repository Profiles\n\n"
    markdown += (
        "Each repository appears once here, even if it belongs to multiple categories.\n\n"
    )

    signal_cache: Dict[str, Dict] = {}
    for i, repo in enumerate(unique_repos, start=1):
        full_name = repo.get("full_name", "Unknown")
        print(f"Rendering profile {i}/{len(unique_repos)}: {full_name}")
        markdown += render_repo_profile(
            repo,
            relationship_map.get(full_name, []),
            signal_cache,
            disk_cache,
        )

    markdown += render_summary(sections, total_unique_repos)
    markdown += "\n"
    return markdown


def main():
    """Main function to fetch repository categories and generate the report."""
    print("GitHub Repository Analyzer")
    print("=" * 40)
    print(
        "GitHub request pacing is enabled "
        f"({REQUEST_DELAY_SECONDS:.2f}s between requests, "
        f"up to {MAX_RETRIES} retries with backoff)."
    )
    if CACHE_PATH:
        cache_ttl_hours = CACHE_TTL_SECONDS / 3600 if CACHE_TTL_SECONDS > 0 else 0
        print(
            f"Disk cache enabled at {CACHE_PATH} "
            f"(TTL: {cache_ttl_hours:.1f} hours)."
        )

    ensure_gh_is_ready()
    disk_cache = load_disk_cache()

    viewer_login = get_current_user_login()
    starred_repos = get_starred_repos()
    owned_repos = get_owned_repos()
    watched_repos = get_watched_repos()
    contributed_repos = get_contributed_repos()

    starred_names = repo_name_set(starred_repos)
    watched_names = repo_name_set(watched_repos)
    contributed_names = repo_name_set(contributed_repos)
    own_contributed_repos = filter_repos_by_owner_prefix(contributed_repos, viewer_login)
    external_contributed_repos = filter_repos_excluding(
        contributed_repos, repo_name_set(own_contributed_repos)
    )

    owned_not_starred = filter_repos_excluding(owned_repos, starred_names)
    forks_created_not_starred = [
        repo for repo in owned_not_starred if bool(repo.get("fork"))
    ]
    account_only_exclusions = (
        starred_names
        | watched_names
        | contributed_names
    )
    account_only_repos = filter_repos_excluding(owned_repos, account_only_exclusions)

    sections = [
        {"title": "Starred Repositories", "repos": starred_repos},
        {"title": "Repos I Own But Never Starred", "repos": owned_not_starred},
        {"title": "Forks I Created But Never Starred", "repos": forks_created_not_starred},
        {"title": "Repos I Watch", "repos": watched_repos},
        {"title": "External Repos I Contributed To", "repos": external_contributed_repos},
        {"title": "My Own Repos I Worked On", "repos": own_contributed_repos},
        {
            "title": "Repos In My GitHub Account Just Because They Exist There",
            "repos": account_only_repos,
        },
    ]

    total_unique_repos = len(
        repo_name_set(
            starred_repos
            + owned_repos
            + watched_repos
            + contributed_repos
        )
    )

    markdown_content = generate_markdown_report(
        viewer_login, sections, total_unique_repos, disk_cache
    )

    output_file = "yourrepos.md"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(markdown_content)
    save_disk_cache(disk_cache)

    print(f"\nComplete markdown report generated: {output_file}")
    print(f"Total unique repositories seen across categories: {total_unique_repos}")


if __name__ == "__main__":
    main()
