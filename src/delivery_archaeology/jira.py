from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import httpx

from delivery_archaeology.config import JiraSettings, RAW_JIRA_DIR
from delivery_archaeology.storage import safe_cache_slug


ISSUE_FIELDS = ["*all"]
Progress = Callable[[str], None] | None


class JiraApiError(RuntimeError):
    def __init__(self, method: str, path: str, status_code: int, detail: str, *, jql: str | None = None):
        self.method = method
        self.path = path
        self.status_code = status_code
        self.detail = detail
        self.jql = jql
        message = f"Jira {method} {path} returned HTTP {status_code}: {detail}"
        if jql:
            message = f"{message}\nJQL: {jql}"
        super().__init__(message)


class JiraClient:
    def __init__(self, settings: JiraSettings):
        self.settings = settings
        headers = {"Accept": "application/json"}
        auth = None
        if settings.token:
            headers["Authorization"] = f"Bearer {settings.token.get_secret_value()}"
        elif settings.username and settings.password:
            auth = (settings.username, settings.password.get_secret_value())
        self.client = httpx.Client(
            base_url=settings.url,
            headers=headers,
            auth=auth,
            verify=settings.verify_ssl,
            timeout=60,
        )

    def close(self) -> None:
        self.client.close()

    def myself(self) -> dict[str, Any]:
        return self._get("/rest/api/2/myself")

    def projects(self) -> list[dict[str, Any]]:
        return self._get("/rest/api/2/project")

    def project(self, key: str) -> dict[str, Any]:
        return self._get(f"/rest/api/2/project/{quote(key, safe='')}")

    def project_versions(self, key: str) -> list[dict[str, Any]]:
        return self._get(f"/rest/api/2/project/{quote(key, safe='')}/versions")

    def statuses_for_project(self, key: str) -> list[str]:
        data = self._get(f"/rest/api/2/project/{quote(key, safe='')}/statuses")
        return sorted({
            status["name"]
            for issue_type in data
            for status in issue_type.get("statuses", [])
            if status.get("name")
        })

    def fields(self) -> list[dict[str, Any]]:
        return self._get("/rest/api/2/field")

    def remote_links(self, issue_key: str) -> list[dict[str, Any]]:
        try:
            return self._get(f"/rest/api/2/issue/{quote(issue_key, safe='')}/remotelink")
        except JiraApiError as exc:
            if exc.status_code == 404:
                return []
            raise

    def dev_status_pull_requests(self, issue_id: str) -> dict[str, Any]:
        try:
            return self._get(
                "/rest/dev-status/latest/issue/detail",
                params={
                    "issueId": issue_id,
                    "applicationType": "github",
                    "dataType": "pullrequest",
                },
            )
        except JiraApiError as exc:
            if exc.status_code == 404:
                return {}
            raise

    def search(
        self,
        jql: str,
        *,
        fields: list[str] | None = None,
        expand: str | list[str] | None = None,
        start_at: int = 0,
        max_results: int = 100,
    ) -> dict[str, Any]:
        payload = search_payload(
            jql,
            fields=fields,
            expand=expand,
            start_at=start_at,
            max_results=max_results,
        )
        return self._post("/rest/api/2/search", payload)

    def search_all(
        self,
        jql: str,
        *,
        fields: list[str] | None = None,
        expand: str | list[str] | None = None,
        progress: Progress = None,
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        start = 0
        while True:
            page = self.search(jql, fields=fields, expand=expand, start_at=start)
            issues.extend(page.get("issues", []))
            total = int(page.get("total", len(issues)))
            if progress:
                progress(f"Jira issues fetched: {len(issues)}/{total}")
            if len(issues) >= total or not page.get("issues"):
                return issues
            start += len(page["issues"])

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self.client.get(path, params=params)
        self._raise_for_status(response, "GET", path)
        return response.json()

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        response = self.client.post(path, json=payload)
        self._raise_for_status(response, "POST", path, jql=payload.get("jql"))
        return response.json()

    def _raise_for_status(self, response: httpx.Response, method: str, path: str, *, jql: str | None = None) -> None:
        if response.is_success:
            return
        raise JiraApiError(method, path, response.status_code, _error_detail(response), jql=jql)


def _error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:1000] or response.reason_phrase
    parts: list[str] = []
    for message in data.get("errorMessages", []) or []:
        parts.append(str(message))
    errors = data.get("errors", {}) or {}
    for field, message in errors.items():
        parts.append(f"{field}: {message}")
    return "; ".join(parts) or str(data)


def search_payload(
    jql: str,
    *,
    fields: list[str] | None = None,
    expand: str | list[str] | None = None,
    start_at: int = 0,
    max_results: int = 100,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jql": jql,
        "startAt": start_at,
        "maxResults": max_results,
        "fields": fields or ISSUE_FIELDS,
    }
    if expand:
        payload["expand"] = [expand] if isinstance(expand, str) else expand
    return payload


def updated_since_jql(projects: list[str], days: int) -> str:
    project_clause = ", ".join(_quote_project_key(project) for project in projects)
    return f'project in ({project_clause}) AND updated >= startOfDay("-{days}d") ORDER BY updated ASC'


def completed_since_jql(projects: list[str], days: int) -> str:
    project_clause = ", ".join(_quote_project_key(project) for project in projects)
    return f'project in ({project_clause}) AND statusCategory = Done AND updated >= startOfDay("-{days}d") ORDER BY updated ASC'


def _quote_project_key(project: str) -> str:
    escaped = project.replace('"', '\\"')
    return f'"{escaped}"'


def cache_path_for_projects(projects: list[str], days: int) -> Path:
    slug = _join_safe_projects(projects)
    return RAW_JIRA_DIR / f"issues_{slug}_{days}d.json"


def cache_path_for_development_links(projects: list[str], days: int) -> Path:
    slug = _join_safe_projects(projects)
    return RAW_JIRA_DIR / f"development_links_{slug}_{days}d.json"


def cache_path_for_project_versions(project: str) -> Path:
    return RAW_JIRA_DIR / f"versions_{safe_cache_slug(project)}.json"


def covering_cache_path_for_projects(projects: list[str], days: int) -> Path | None:
    slug = _join_safe_projects(projects)
    candidates: list[tuple[int, Path]] = []
    for path in RAW_JIRA_DIR.glob(f"issues_{slug}_*d.json"):
        match = re.fullmatch(rf"issues_{re.escape(slug)}_(\d+)d\.json", path.name)
        if match and int(match.group(1)) >= days:
            candidates.append((int(match.group(1)), path))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[0][1]


def load_or_fetch_issues(
    client: JiraClient,
    projects: list[str],
    days: int,
    *,
    refresh: bool,
    progress: Progress = None,
) -> list[dict[str, Any]]:
    RAW_JIRA_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path_for_projects(projects, days)
    if path.exists() and not refresh:
        if progress:
            progress(f"Using Jira cache: {path}")
        return json.loads(path.read_text())
    covering_path = covering_cache_path_for_projects(projects, days)
    if covering_path and not refresh:
        if progress:
            progress(f"Using Jira covering cache: {covering_path}")
        return json.loads(covering_path.read_text())
    if progress:
        progress(f"Fetching Jira issues for {', '.join(projects)} over {days} days")
    issues = client.search_all(updated_since_jql(projects, days), expand="changelog", progress=progress)
    path.write_text(json.dumps(issues, indent=2, sort_keys=True))
    if progress:
        progress(f"Wrote Jira cache: {path}")
    return issues


def load_or_fetch_development_links(
    client: JiraClient,
    issues: list[Any],
    projects: list[str],
    days: int,
    *,
    refresh: bool,
    progress: Progress = None,
) -> dict[str, dict[str, Any]]:
    RAW_JIRA_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path_for_development_links(projects, days)
    requested_issue_keys = {issue.key for issue in issues}
    if path.exists() and not refresh:
        cached = json.loads(path.read_text())
        if requested_issue_keys.issubset(cached.keys()):
            if progress:
                progress(f"Using Jira development-link cache: {path}")
            return {key: cached[key] for key in requested_issue_keys}
        data = cached
    else:
        data: dict[str, dict[str, Any]] = {}
    missing_issues = [issue for issue in issues if refresh or issue.key not in data]
    total = len(missing_issues)
    fetched = 0
    if progress and total:
        progress(f"Fetching Jira development links for {total} issues")
    for issue in missing_issues:
        issue_key = issue.key
        entry: dict[str, Any] = {"id": issue.id, "remote_links": [], "dev_status": {}, "errors": []}
        try:
            entry["remote_links"] = client.remote_links(issue_key)
        except JiraApiError as exc:
            entry["errors"].append(f"remote_links HTTP {exc.status_code}: {exc.detail}")
        try:
            entry["dev_status"] = client.dev_status_pull_requests(issue.id)
        except JiraApiError as exc:
            entry["errors"].append(f"dev_status HTTP {exc.status_code}: {exc.detail}")
        data[issue_key] = entry
        fetched += 1
        if progress and (fetched == 1 or fetched % 10 == 0 or fetched == total):
            progress(f"Jira development links fetched: {fetched}/{total}")
    path.write_text(json.dumps(data, indent=2, sort_keys=True))
    if progress:
        progress(f"Wrote Jira development-link cache: {path}")
    return data


def load_or_fetch_project_versions(
    client: JiraClient,
    projects: list[str],
    *,
    refresh: bool,
    progress: Progress = None,
) -> list[dict[str, Any]]:
    RAW_JIRA_DIR.mkdir(parents=True, exist_ok=True)
    versions: list[dict[str, Any]] = []
    for project in projects:
        path = cache_path_for_project_versions(project)
        if path.exists() and not refresh:
            if progress:
                progress(f"Using Jira versions cache: {path}")
            project_versions = json.loads(path.read_text())
        else:
            if progress:
                progress(f"Fetching Jira versions for {project}")
            try:
                project_versions = client.project_versions(project)
            except JiraApiError as exc:
                if progress:
                    progress(f"Could not fetch Jira versions for {project}: HTTP {exc.status_code}")
                project_versions = [{"project": project, "_error": f"HTTP {exc.status_code}: {exc.detail}"}]
            else:
                path.write_text(json.dumps(project_versions, indent=2, sort_keys=True))
                if progress:
                    progress(f"Wrote Jira versions cache: {path}")
        for version in project_versions:
            enriched = dict(version)
            enriched.setdefault("project", project)
            versions.append(enriched)
    return versions


def inspect_project(client: JiraClient, project_key: str, days: int = 180) -> dict[str, Any]:
    project = client.project(project_key)
    jql = updated_since_jql([project_key], days)
    sample = client.search_all(jql, fields=["issuetype", "status"], expand=None)
    fields = client.fields()
    useful_names = {
        "story points",
        "sprint",
        "team",
        "workstream",
        "epic link",
        "epic name",
    }
    useful = [
        {"name": f["name"], "id": f["id"]}
        for f in fields
        if f.get("name", "").casefold() in useful_names
        or any(token in f.get("name", "").casefold() for token in ["story point", "sprint", "team", "workstream", "epic"])
    ]
    issue_types: dict[str, int] = {}
    statuses: set[str] = set()
    for issue in sample:
        fields_data = issue.get("fields", {})
        issue_type = fields_data.get("issuetype", {}).get("name", "Unknown")
        status = fields_data.get("status", {}).get("name")
        issue_types[issue_type] = issue_types.get(issue_type, 0) + 1
        if status:
            statuses.add(status)
    return {
        "project": project,
        "days": days,
        "issue_count": len(sample),
        "issue_types": issue_types,
        "statuses": sorted(statuses),
        "useful_fields": useful,
    }


def period_start(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _join_safe_projects(projects: list[str]) -> str:
    return "_".join(sorted(safe_cache_slug(project) for project in projects))
