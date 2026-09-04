from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from delivery_archaeology.config import JiraSettings, RAW_JIRA_DIR


ISSUE_FIELDS = ["*all"]


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
        return self._get(f"/rest/api/2/project/{key}")

    def statuses_for_project(self, key: str) -> list[str]:
        data = self._get(f"/rest/api/2/project/{key}/statuses")
        return sorted({
            status["name"]
            for issue_type in data
            for status in issue_type.get("statuses", [])
            if status.get("name")
        })

    def fields(self) -> list[dict[str, Any]]:
        return self._get("/rest/api/2/field")

    def search(
        self,
        jql: str,
        *,
        fields: list[str] | None = None,
        expand: str | None = None,
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
            payload["expand"] = expand
        return self._post("/rest/api/2/search", payload)

    def search_all(
        self,
        jql: str,
        *,
        fields: list[str] | None = None,
        expand: str | None = None,
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        start = 0
        while True:
            page = self.search(jql, fields=fields, expand=expand, start_at=start)
            issues.extend(page.get("issues", []))
            total = int(page.get("total", len(issues)))
            if len(issues) >= total or not page.get("issues"):
                return issues
            start += len(page["issues"])

    def _get(self, path: str) -> Any:
        response = self.client.get(path)
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
    slug = "_".join(sorted(projects))
    return RAW_JIRA_DIR / f"issues_{slug}_{days}d.json"


def load_or_fetch_issues(
    client: JiraClient,
    projects: list[str],
    days: int,
    *,
    refresh: bool,
) -> list[dict[str, Any]]:
    RAW_JIRA_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path_for_projects(projects, days)
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    issues = client.search_all(updated_since_jql(projects, days), expand="changelog")
    path.write_text(json.dumps(issues, indent=2, sort_keys=True))
    return issues


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
