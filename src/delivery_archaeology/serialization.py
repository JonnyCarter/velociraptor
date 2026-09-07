from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from delivery_archaeology.analysis import AnalysisResult
from delivery_archaeology.metrics import ReviewCandidate


def analysis_payload(
    *,
    result: AnalysisResult,
    jira_projects: list[str],
    repos: list[str],
    weekly_rows: list[dict[str, object]],
) -> dict[str, Any]:
    return {
        "report_type": "delivery_analysis",
        "period": _period_payload(result.start, result.end, result.days),
        "scope": {
            "jira_projects": jira_projects,
            "repositories": repos,
        },
        "data_quality": {
            "jira_issues_analysed": result.jira_issue_count,
            "completed_issues": result.completed_count,
            "github_prs": result.github.get("pr_count", 0),
            "issues_linked_to_prs": result.linked_completed,
            "link_coverage_percent": result.link_coverage,
            "unknown_jira_statuses": sorted(result.unknown_statuses),
            "missing_resolution_dates": result.missing_resolution_dates,
            "prs_without_jira_links": result.pr_without_links,
        },
        "work_mix": _jsonable(result.issue_mix),
        "releases": _jsonable(result.releases),
        "delivery_flow": result.delivery,
        "github_flow": result.github,
        "review_candidates": {
            "jira_issues": [_candidate_payload(candidate) for candidate in result.issue_candidates],
            "pull_requests": [_candidate_payload(candidate) for candidate in result.pr_candidates],
        },
        "weekly_breakdown": [_jsonable(row) for row in weekly_rows],
        "findings": [asdict(finding) for finding in result.findings],
    }


def compare_payload(
    *,
    previous: AnalysisResult,
    current: AnalysisResult,
    jira_projects: list[str],
    repos: list[str],
    rows: list[dict[str, object]],
) -> dict[str, Any]:
    return {
        "report_type": "delivery_comparison",
        "scope": {
            "jira_projects": jira_projects,
            "repositories": repos,
        },
        "periods": {
            "previous": _period_payload(previous.start, previous.end, previous.days),
            "current": _period_payload(current.start, current.end, current.days),
        },
        "metrics": [_jsonable(row) for row in rows],
        "work_mix": {
            "previous": _jsonable(previous.issue_mix),
            "current": _jsonable(current.issue_mix),
        },
        "releases": {
            "previous": _jsonable(previous.releases),
            "current": _jsonable(current.releases),
        },
        "data_quality": {
            "previous": {
                "unknown_jira_statuses": sorted(previous.unknown_statuses),
                "link_coverage_percent": previous.link_coverage,
                "completed_issues": previous.completed_count,
            },
            "current": {
                "unknown_jira_statuses": sorted(current.unknown_statuses),
                "link_coverage_percent": current.link_coverage,
                "completed_issues": current.completed_count,
            },
        },
        "current_review_candidates": {
            "jira_issues": [_candidate_payload(candidate) for candidate in current.issue_candidates[:5]],
            "pull_requests": [_candidate_payload(candidate) for candidate in current.pr_candidates[:5]],
        },
    }


def repo_inference_payload(
    *,
    jira_projects: list[str],
    org: str | None,
    days: int,
    issue_key_count: int,
    pr_match_count: int,
    candidates: list[dict[str, object]],
    command: str,
    min_prs: int,
    source: str,
) -> dict[str, Any]:
    return {
        "report_type": "repository_inference",
        "scope": {
            "jira_projects": jira_projects,
            "github_org": org,
            "days": days,
        },
        "evidence": {
            "source": source,
            "jira_issue_keys_searched": issue_key_count,
            "pr_links_or_matches_found": pr_match_count,
            "minimum_prs_per_repo": min_prs,
        },
        "candidate_repositories": [_jsonable(candidate) for candidate in candidates],
        "suggested_command": command,
    }


def to_pretty_json(payload: dict[str, Any]) -> str:
    return json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n"


def _period_payload(start: datetime, end: datetime, days: int) -> dict[str, Any]:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days,
    }


def _candidate_payload(candidate: ReviewCandidate) -> dict[str, Any]:
    return {
        "kind": candidate.kind,
        "identifier": candidate.identifier,
        "title": candidate.title,
        "url": candidate.url,
        "reason": candidate.reason,
        "evidence": candidate.evidence,
        "score": candidate.score,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_jsonable(nested) for nested in value]
    return value
