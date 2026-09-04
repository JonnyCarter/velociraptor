from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from delivery_archaeology.config import RAW_GITHUB_DIR


PR_FIELDS = [
    "number",
    "title",
    "body",
    "author",
    "createdAt",
    "updatedAt",
    "closedAt",
    "mergedAt",
    "state",
    "reviewDecision",
    "reviews",
    "commits",
    "additions",
    "deletions",
    "changedFiles",
    "headRefName",
    "baseRefName",
    "labels",
]


def run_gh(args: list[str]) -> Any:
    result = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def repos_for_org(org: str) -> list[dict[str, Any]]:
    return run_gh([
        "repo",
        "list",
        org,
        "--limit",
        "500",
        "--json",
        "name,updatedAt,isArchived",
    ]) or []


def cache_path_for_repo(repo: str, days: int) -> Path:
    owner, name = repo.split("/", 1)
    return RAW_GITHUB_DIR / owner / f"{name}_{days}d_prs.json"


def load_or_fetch_prs(repo: str, days: int, *, refresh: bool) -> list[dict[str, Any]]:
    path = cache_path_for_repo(repo, days)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    fields = ",".join(PR_FIELDS)
    since = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
    prs = run_gh([
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "all",
        "--limit",
        "1000",
        "--search",
        f"updated:>={since}",
        "--json",
        fields,
    ]) or []
    for pr in prs:
        pr["repository"] = repo
    path.write_text(json.dumps(prs, indent=2, sort_keys=True))
    return prs


def load_or_fetch_all_prs(repos: list[str], days: int, *, refresh: bool) -> list[dict[str, Any]]:
    prs: list[dict[str, Any]] = []
    for repo in repos:
        prs.extend(load_or_fetch_prs(repo, days, refresh=refresh))
    return prs


def relative_updated(value: str | None) -> str:
    if not value:
        return ""
    updated = datetime.fromisoformat(value.replace("Z", "+00:00"))
    days = max((datetime.now(UTC) - updated).days, 0)
    if days == 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"
