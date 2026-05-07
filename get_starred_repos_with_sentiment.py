#!/usr/bin/env python3
"""
Script to fetch starred GitHub repositories and generate a markdown report with
GitHub-native community signals and repository health snapshots.
This is a standalone script that does everything from scratch.

Usage:
    python3 get_starred_repos_with_sentiment.py

Requirements:
    - GitHub CLI (gh) installed and authenticated
    - Python 3.x
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Dict, List, Optional


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


def run_gh_command(command: str) -> str:
    """Run a GitHub CLI command and return the output."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {command}", file=sys.stderr)
        print(f"Error: {e.stderr}", file=sys.stderr)
        return ""


def get_starred_repos() -> List[Dict]:
    """Get the list of starred repositories with basic details."""
    print("Fetching starred repositories from GitHub...")

    command = (
        'gh api user/starred --paginate --jq '
        '".[] | {full_name: .full_name, description: .description, html_url: .html_url, '
        'stargazers_count: .stargazers_count, language: .language}"'
    )

    output = run_gh_command(command)

    if not output:
        return []

    repos = []
    for line in output.strip().split("\n"):
        if line.strip():
            try:
                repo_data = json.loads(line)
                repos.append(repo_data)
            except json.JSONDecodeError:
                continue

    print(f"Found {len(repos)} starred repositories.")
    return repos


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


def get_community_signal(repo_name: str) -> Dict:
    """
    Fetch GitHub-native repository metrics and return a community signal snapshot.
    """
    default_result = {
        "summary": "Community signal data not available.",
        "metrics_line": (
            "**Metrics**: Unknown stars | Unknown forks | Unknown open issues | "
            "Watchers: Unknown | Subscribers: Unknown | Last pushed: Unknown"
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
        command = f"gh api repos/{repo_name} --jq '{{{fields}}}'"
        result = run_gh_command(command)

        if not result:
            return default_result

        repo_data = json.loads(result)

        stars = safe_int(repo_data.get("stargazers_count"))
        forks = safe_int(repo_data.get("forks_count"))
        open_issues = safe_int(repo_data.get("open_issues_count"))
        watchers = safe_int(repo_data.get("watchers_count"))
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

        return {
            "summary": classify_community_signal(repo_data),
            "metrics_line": (
                f"**Metrics**: {stars:,} stars | {forks:,} forks | "
                f"{open_issues:,} open issues | Watchers: {watchers:,} | "
                f"Subscribers: {subscribers:,} | Last pushed: {pushed_at}"
            ),
            "archived": "yes" if archived else "no",
            "fork": "yes" if is_fork else "no",
            "license": license_name,
            "topics": topics,
            "updated_at": updated_at,
        }
    except json.JSONDecodeError as e:
        return {
            **default_result,
            "summary": f"Could not parse repository metrics: {str(e)}",
        }
    except Exception as e:
        return {
            **default_result,
            "summary": f"Could not retrieve community signal: {str(e)}",
        }


def generate_markdown_report(repos: List[Dict]) -> str:
    """Generate a markdown report from repository data and GitHub metrics."""
    markdown = "# Starred GitHub Repositories\n\n"
    markdown += f"*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n"

    if not repos:
        markdown += "No starred repositories found.\n"
        return markdown

    repos.sort(key=lambda x: x.get("stargazers_count", 0), reverse=True)

    print("Generating markdown report with GitHub community signals...")
    for i, repo in enumerate(repos):
        full_name = repo.get("full_name", "Unknown")
        description = (
            repo.get("description", "No description available.")
            or "No description available."
        )
        html_url = repo.get("html_url", "#")
        stars = repo.get("stargazers_count", 0)
        language = repo.get("language", "Unknown") or "Unknown"

        markdown += f"## [{full_name}]({html_url})\n\n"

        signal_data = get_community_signal(full_name)
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
        markdown += f"**Stars**: {stars} | **Language**: {language}\n\n"
        markdown += "---\n\n"

        if (i + 1) % 10 == 0:
            print(f"Processed {i + 1}/{len(repos)} repositories...")

    return markdown


def main():
    """Main function to fetch starred repositories and generate the report."""
    print("GitHub Starred Repositories Analyzer")
    print("=" * 40)

    ensure_gh_is_ready()

    repos = get_starred_repos()

    if not repos:
        print("No repositories found or error occurred.", file=sys.stderr)
        sys.exit(1)

    markdown_content = generate_markdown_report(repos)

    output_file = "starred_repositories_complete.md"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(markdown_content)

    print(f"\nComplete markdown report generated: {output_file}")
    print(f"Total repositories processed: {len(repos)}")


if __name__ == "__main__":
    main()
