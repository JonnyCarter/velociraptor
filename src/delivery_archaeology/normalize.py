from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


JIRA_DATE_FORMATS = ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z")
DEFAULT_EPIC_FIELDS = ("customfield_10008",)
DEFAULT_STORY_POINT_FIELDS = ("customfield_10014",)
DEFAULT_SPRINT_FIELDS = ("customfield_10020",)
DEFAULT_TEAM_FIELDS = ("customfield_10231",)


class JiraIssue(BaseModel):
    id: str
    key: str
    project: str
    issue_type: str | None = None
    summary: str | None = None
    status: str | None = None
    created: datetime | None = None
    updated: datetime | None = None
    resolved: datetime | None = None
    priority: str | None = None
    parent: str | None = None
    epic: str | None = None
    assignee: str | None = None
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    fix_versions: list[str] = Field(default_factory=list)
    story_points: float | None = None
    sprint: str | None = None
    team: str | None = None
    changelog: list["JiraChange"] = Field(default_factory=list)


class JiraChange(BaseModel):
    issue_key: str
    timestamp: datetime
    field: str
    from_value: str | None = None
    to_value: str | None = None


class PullRequest(BaseModel):
    repository: str
    number: int
    url: str | None = None
    title: str
    body: str | None = None
    author: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None
    merged_at: datetime | None = None
    state: str | None = None
    review_decision: str | None = None
    reviews: list[dict[str, Any]] = Field(default_factory=list)
    commits: list[dict[str, Any]] = Field(default_factory=list)
    additions: int | None = None
    deletions: int | None = None
    changed_files: int | None = None
    head_ref_name: str | None = None
    base_ref_name: str | None = None
    labels: list[str] = Field(default_factory=list)


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for fmt in JIRA_DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
    return None


def normalize_jira_issue(raw: dict[str, Any]) -> JiraIssue:
    fields = raw.get("fields", {})
    changelog: list[JiraChange] = []
    for history in raw.get("changelog", {}).get("histories", []):
        timestamp = parse_dt(history.get("created"))
        if not timestamp:
            continue
        for item in history.get("items", []):
            field = item.get("field") or ""
            if field.casefold() in {
                "status",
                "assignee",
                "sprint",
                "story points",
                "priority",
                "fix version",
                "fixversions",
            }:
                changelog.append(JiraChange(
                    issue_key=raw["key"],
                    timestamp=timestamp,
                    field=field,
                    from_value=item.get("fromString"),
                    to_value=item.get("toString"),
                ))
    return JiraIssue(
        id=raw["id"],
        key=raw["key"],
        project=fields.get("project", {}).get("key", raw["key"].split("-", 1)[0]),
        issue_type=_name(fields.get("issuetype")),
        summary=fields.get("summary"),
        status=_name(fields.get("status")),
        created=parse_dt(fields.get("created")),
        updated=parse_dt(fields.get("updated")),
        resolved=parse_dt(fields.get("resolutiondate")),
        priority=_name(fields.get("priority")),
        parent=fields.get("parent", {}).get("key") if fields.get("parent") else None,
        epic=_stringify(_first_present(fields, DEFAULT_EPIC_FIELDS)),
        assignee=fields.get("assignee", {}).get("displayName") if fields.get("assignee") else None,
        labels=fields.get("labels") or [],
        components=[c["name"] for c in fields.get("components", []) if c.get("name")],
        fix_versions=[v["name"] for v in fields.get("fixVersions", []) if v.get("name")],
        story_points=_first_number(fields, DEFAULT_STORY_POINT_FIELDS),
        sprint=_stringify(_first_present(fields, DEFAULT_SPRINT_FIELDS)),
        team=_stringify(_first_present(fields, DEFAULT_TEAM_FIELDS)),
        changelog=sorted(changelog, key=lambda c: c.timestamp),
    )


def normalize_pr(raw: dict[str, Any]) -> PullRequest:
    author = raw.get("author")
    labels = raw.get("labels") or []
    return PullRequest(
        repository=raw.get("repository", ""),
        number=int(raw["number"]),
        url=raw.get("url"),
        title=raw.get("title") or "",
        body=raw.get("body"),
        author=author.get("login") if isinstance(author, dict) else author,
        created_at=parse_dt(raw.get("createdAt")),
        updated_at=parse_dt(raw.get("updatedAt")),
        closed_at=parse_dt(raw.get("closedAt")),
        merged_at=parse_dt(raw.get("mergedAt")),
        state=raw.get("state"),
        review_decision=raw.get("reviewDecision"),
        reviews=raw.get("reviews") or [],
        commits=raw.get("commits") or [],
        additions=raw.get("additions"),
        deletions=raw.get("deletions"),
        changed_files=raw.get("changedFiles"),
        head_ref_name=raw.get("headRefName"),
        base_ref_name=raw.get("baseRefName"),
        labels=[label.get("name", str(label)) if isinstance(label, dict) else str(label) for label in labels],
    )


def normalize_jira_issues(raw: list[dict[str, Any]]) -> list[JiraIssue]:
    return [normalize_jira_issue(issue) for issue in raw]


def normalize_prs(raw: list[dict[str, Any]]) -> list[PullRequest]:
    return [normalize_pr(pr) for pr in raw]


def _name(value: Any) -> str | None:
    return value.get("name") if isinstance(value, dict) else None


def _first_number(fields: dict[str, Any], preferred_keys: tuple[str, ...] = ()) -> float | None:
    keys = [*preferred_keys, *[key for key in fields if key.startswith("customfield_") and key not in preferred_keys]]
    for key in keys:
        value = fields.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _first_present(fields: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = fields.get(key)
        if value not in (None, "", []):
            return value
    return None


def _stringify(value: Any) -> str | None:
    if value in (None, "", []):
        return None
    if isinstance(value, dict):
        return value.get("name") or value.get("value") or str(value)
    if isinstance(value, list):
        names = [_stringify(item) for item in value]
        return ", ".join(name for name in names if name)
    return str(value)
