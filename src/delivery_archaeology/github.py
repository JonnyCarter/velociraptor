from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from delivery_archaeology.config import RAW_GITHUB_DIR


PR_FIELDS = [
    "number",
    "url",
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

PR_LIST_FIELDS = [
    "number",
    "url",
    "title",
    "author",
    "createdAt",
    "updatedAt",
    "closedAt",
    "mergedAt",
    "state",
    "reviewDecision",
    "headRefName",
    "baseRefName",
    "labels",
]

PR_DETAIL_FIELDS = [
    "number",
    "url",
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

PR_FALLBACK_FIELDS = [
    field for field in PR_DETAIL_FIELDS if field not in {"reviews", "commits"}
]


class GhCliError(RuntimeError):
    def __init__(self, args: list[str], stderr: str):
        self.args_used = args
        self.stderr = stderr
        super().__init__(f"gh {' '.join(args)} failed: {stderr}")


def run_gh(args: list[str]) -> Any:
    result = subprocess.run(
        ["gh", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GhCliError(args, result.stderr.strip() or result.stdout.strip())
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


def filter_and_sort_repos(
    repos: list[dict[str, Any]],
    *,
    contains: str | None = None,
    include_archived: bool = False,
    sort: str = "updated",
) -> list[dict[str, Any]]:
    filtered = repos
    if not include_archived:
        filtered = [repo for repo in filtered if not repo.get("isArchived")]
    if contains:
        needle = contains.casefold()
        filtered = [repo for repo in filtered if needle in repo.get("name", "").casefold()]
    if sort == "name":
        return sorted(filtered, key=lambda repo: repo.get("name", "").casefold())
    return sorted(filtered, key=lambda repo: (_updated_sort_key(repo.get("updatedAt")), repo.get("name", "").casefold()))


def cache_path_for_repo(repo: str, days: int) -> Path:
    owner, name = repo.split("/", 1)
    return RAW_GITHUB_DIR / owner / f"{name}_{days}d_prs.json"


def covering_cache_path_for_repo(repo: str, days: int) -> Path | None:
    owner, name = repo.split("/", 1)
    repo_dir = RAW_GITHUB_DIR / owner
    candidates: list[tuple[int, Path]] = []
    for path in repo_dir.glob(f"{name}_*d_prs.json"):
        match = re.fullmatch(rf"{re.escape(name)}_(\d+)d_prs\.json", path.name)
        if match and int(match.group(1)) >= days:
            candidates.append((int(match.group(1)), path))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[0][1]


def load_or_fetch_prs(repo: str, days: int, *, refresh: bool) -> list[dict[str, Any]]:
    path = cache_path_for_repo(repo, days)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    covering_path = covering_cache_path_for_repo(repo, days)
    if covering_path and not refresh:
        return json.loads(covering_path.read_text())
    since = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
    listed_prs = run_gh(pr_list_args(repo, since)) or []
    prs = []
    for listed_pr in listed_prs:
        number = int(listed_pr["number"])
        pr = fetch_pr_detail(repo, number)
        pr["repository"] = repo
        prs.append(pr)
    path.write_text(json.dumps(prs, indent=2, sort_keys=True))
    return prs


def pr_list_args(repo: str, since: str) -> list[str]:
    return [
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
        ",".join(PR_LIST_FIELDS),
    ]


def pr_view_args(repo: str, number: int, fields: list[str]) -> list[str]:
    return [
        "pr",
        "view",
        str(number),
        "--repo",
        repo,
        "--json",
        ",".join(fields),
    ]


def fetch_pr_detail(repo: str, number: int) -> dict[str, Any]:
    try:
        return run_gh(pr_view_args(repo, number, PR_DETAIL_FIELDS)) or {}
    except GhCliError as exc:
        fallback = run_gh(pr_view_args(repo, number, PR_FALLBACK_FIELDS)) or {}
        fallback["reviews"] = []
        fallback["commits"] = []
        fallback["_detail_warning"] = exc.stderr
        return fallback


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


def _updated_sort_key(value: str | None) -> float:
    if not value:
        return float("inf")
    try:
        updated = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    return -updated.timestamp()
