from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from delivery_archaeology.normalize import JiraIssue


DEFAULT_EXCLUDED_ISSUE_TYPES = {"epic", "sub-task", "subtask"}
PR_URL_RE = re.compile(r"https?://[^/\s]+/([^/\s]+)/([^/\s]+)/pull/(\d+)")


def issue_keys_for_repo_inference(
    issues: list[JiraIssue],
    *,
    issue_types: list[str] | None = None,
    max_issues: int = 100,
) -> list[str]:
    wanted_types = {issue_type.casefold() for issue_type in issue_types or []}
    candidates = []
    for issue in sorted(issues, key=lambda item: item.updated or item.created or datetime.min.replace(tzinfo=UTC), reverse=True):
        issue_type = (issue.issue_type or "").casefold()
        if wanted_types and issue_type not in wanted_types:
            continue
        if not wanted_types and issue_type in DEFAULT_EXCLUDED_ISSUE_TYPES:
            continue
        candidates.append(issue.key)
    return candidates[:max_issues]


def analyse_command_for_repos(projects: list[str], repos: list[str], days: int) -> str:
    lines = ["uv run delivery analyse \\"]
    lines.extend(f"  --jira-project {project} \\" for project in projects)
    lines.extend(f"  --repo {repo} \\" for repo in repos)
    lines.append(f"  --days {days}")
    return "\n".join(lines)


def infer_repos_from_jira_development_links(development_links: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    by_repo: dict[str, dict[str, Any]] = {}
    seen_prs: set[tuple[str, str]] = set()
    for issue_key, payload in development_links.items():
        urls = extract_pr_urls(payload)
        for url in urls:
            repo = repo_from_pr_url(url)
            if not repo:
                continue
            unique_key = (issue_key, url)
            if unique_key in seen_prs:
                continue
            seen_prs.add(unique_key)
            entry = by_repo.setdefault(repo, {
                "repository": repo,
                "pr_count": 0,
                "issue_keys": set(),
                "examples": [],
            })
            entry["pr_count"] += 1
            entry["issue_keys"].add(issue_key)
            if len(entry["examples"]) < 3:
                entry["examples"].append({"title": issue_key, "url": url})
    return [
        {
            "repository": repo,
            "pr_count": data["pr_count"],
            "issue_count": len(data["issue_keys"]),
            "issue_keys": sorted(data["issue_keys"]),
            "examples": data["examples"],
        }
        for repo, data in sorted(
            by_repo.items(),
            key=lambda item: (-item[1]["pr_count"], -len(item[1]["issue_keys"]), item[0].casefold()),
        )
    ]


def extract_pr_urls(value: Any) -> list[str]:
    urls: list[str] = []
    for text in _string_values(value):
        urls.extend(match.group(0) for match in PR_URL_RE.finditer(text))
    return sorted(set(urls))


def repo_from_pr_url(url: str) -> str | None:
    match = PR_URL_RE.search(url)
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        values: list[str] = []
        for nested in value.values():
            values.extend(_string_values(nested))
        return values
    if isinstance(value, list):
        values = []
        for nested in value:
            values.extend(_string_values(nested))
        return values
    return []
