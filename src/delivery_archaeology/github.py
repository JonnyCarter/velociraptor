from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from delivery_archaeology.config import RAW_GITHUB_DIR
from delivery_archaeology.linking import ISSUE_KEY_RE


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


def search_prs_for_issue_keys(org: str, issue_keys: list[str], *, days: int, chunk_size: int = 8) -> list[dict[str, Any]]:
    since = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
    items: list[dict[str, Any]] = []
    for chunk in _chunks(issue_keys, chunk_size):
        key_query = " OR ".join(_quote_search_term(key) for key in chunk)
        query = f"org:{org} is:pr updated:>={since} ({key_query})"
        result = run_gh([
            "api",
            "-X",
            "GET",
            "search/issues",
            "-f",
            f"q={query}",
            "-f",
            "per_page=100",
        ]) or {}
        items.extend(result.get("items", []))
    return _deduplicate_search_items(items)


def infer_repos_from_search_results(items: list[dict[str, Any]], issue_keys: set[str]) -> list[dict[str, Any]]:
    by_repo: dict[str, dict[str, Any]] = {}
    for item in items:
        repo = _repo_from_search_item(item)
        if not repo:
            continue
        entry = by_repo.setdefault(repo, {
            "repository": repo,
            "pr_count": 0,
            "issue_keys": set(),
            "examples": [],
        })
        entry["pr_count"] += 1
        text = "\n".join([item.get("title") or "", item.get("body") or ""])
        keys = ISSUE_KEY_RE.findall(text)
        entry["issue_keys"].update(key for key in keys if key in issue_keys)
        if len(entry["examples"]) < 3:
            entry["examples"].append({
                "title": item.get("title") or "",
                "url": item.get("html_url") or "",
            })
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


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def _quote_search_term(value: str) -> str:
    return f'"{value}"'


def _deduplicate_search_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        key = item.get("node_id") or item.get("html_url") or str(item.get("id"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _repo_from_search_item(item: dict[str, Any]) -> str | None:
    repository_url = item.get("repository_url")
    if repository_url:
        return repository_url.rstrip("/").rsplit("/repos/", 1)[-1]
    html_url = item.get("html_url")
    if html_url:
        parts = html_url.split("/")
        if len(parts) >= 5:
            return f"{parts[3]}/{parts[4]}"
    return None
