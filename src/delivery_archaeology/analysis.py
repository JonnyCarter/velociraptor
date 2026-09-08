from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from delivery_archaeology.config import StatusMapping
from delivery_archaeology.findings import Finding, build_findings
from delivery_archaeology.flow import StateSegment, reconstruct_issue
from delivery_archaeology.linking import is_dependabot_pr, keys_in_pr, link_prs_to_issues
from delivery_archaeology.metrics import (
    IssueFlowRecord,
    ReviewCandidate,
    delivery_metrics,
    issue_flow_records,
    issue_review_candidates,
    issue_mix_metrics,
    pr_metrics,
    pr_review_candidates,
    release_metrics,
)
from delivery_archaeology.normalize import JiraIssue, PullRequest


@dataclass(frozen=True)
class AnalysisResult:
    start: datetime
    end: datetime
    days: int
    issues: list[JiraIssue]
    prs: list[PullRequest]
    timelines: dict[str, list[StateSegment]]
    records: list[IssueFlowRecord]
    delivery: dict[str, object]
    github: dict[str, object]
    issue_mix: dict[str, object]
    releases: dict[str, object]
    jira_issue_count: int
    completed_count: int
    linked_completed: int
    link_coverage: float
    unknown_statuses: set[str]
    missing_resolution_dates: int
    pr_without_links: int
    findings: list[Finding]
    issue_candidates: list[ReviewCandidate]
    pr_candidates: list[ReviewCandidate]


def analyse_window(
    *,
    issues: list[JiraIssue],
    prs: list[PullRequest],
    mapping: StatusMapping,
    jira_url: str,
    start: datetime,
    end: datetime,
    versions: list[dict[str, object]] | None = None,
) -> AnalysisResult:
    days = max((end - start).days, 1)
    all_timelines = {issue.key: reconstruct_issue(issue, mapping) for issue in issues}
    all_records = issue_flow_records(issues, all_timelines)
    issue_by_key = {issue.key: issue for issue in issues}
    window_issue_keys = {
        issue.key for issue in issues
        if _issue_touches_window(issue, start, end)
    }
    completed_records = [
        record for record in all_records
        if record.completed and _issue_completed_in_window(issue_by_key[record.key], start, end)
    ]
    completed_keys = {record.key for record in completed_records}
    window_timelines = {key: all_timelines[key] for key in completed_keys}
    window_prs = [pr for pr in prs if _pr_touches_window(pr, start, end)]
    linked = link_prs_to_issues(window_prs)
    linked_completed = len([key for key in completed_keys if linked.get(key)])
    link_coverage = linked_completed / len(completed_keys) * 100 if completed_keys else 0.0
    pr_without_links = sum(1 for pr in window_prs if not is_dependabot_pr(pr) and not keys_in_pr(pr))
    window_issues = [issue for issue in issues if issue.key in window_issue_keys]
    statuses = {issue.status for issue in window_issues if issue.status}
    statuses.update(
        change.to_value
        for issue in window_issues
        for change in issue.changelog
        if change.field.casefold() == "status" and change.to_value
    )
    unknown_statuses = mapping.unknown_statuses(statuses)
    missing_resolution_dates = sum(
        1
        for issue in window_issues
        if (issue.status or "").casefold() in {"done", "closed", "released"} and issue.resolved is None
    )
    delivery = delivery_metrics(completed_records, window_timelines, days)
    github = pr_metrics(window_prs)
    issue_mix = issue_mix_metrics(window_issues, completed_keys)
    releases = release_metrics(versions or [], start=start, end=end)
    findings = build_findings(
        delivery,
        github,
        link_coverage=link_coverage,
        unknown_statuses=unknown_statuses,
        missing_resolution_dates=missing_resolution_dates,
        pr_without_links=pr_without_links,
    )
    return AnalysisResult(
        start=start,
        end=end,
        days=days,
        issues=window_issues,
        prs=window_prs,
        timelines=window_timelines,
        records=completed_records,
        delivery=delivery,
        github=github,
        issue_mix=issue_mix,
        releases=releases,
        jira_issue_count=len(window_issue_keys),
        completed_count=len(completed_keys),
        linked_completed=linked_completed,
        link_coverage=link_coverage,
        unknown_statuses=unknown_statuses,
        missing_resolution_dates=missing_resolution_dates,
        pr_without_links=pr_without_links,
        findings=findings,
        issue_candidates=issue_review_candidates(window_issues, completed_records, jira_url=jira_url),
        pr_candidates=pr_review_candidates(window_prs),
    )


def weekly_breakdown(
    *,
    issues: list[JiraIssue],
    prs: list[PullRequest],
    mapping: StatusMapping,
    start: datetime,
    end: datetime,
) -> list[dict[str, object]]:
    timelines = {issue.key: reconstruct_issue(issue, mapping) for issue in issues}
    records = issue_flow_records(issues, timelines)
    issue_by_key = {issue.key: issue for issue in issues}
    rows: list[dict[str, object]] = []
    bucket_start = start
    while bucket_start < end:
        bucket_end = min(bucket_start + timedelta(days=7), end)
        completed = [
            record for record in records
            if record.completed and _issue_completed_in_window(issue_by_key[record.key], bucket_start, bucket_end)
        ]
        bucket_prs = [pr for pr in prs if _pr_touches_window(pr, bucket_start, bucket_end)]
        cycle_values = [record.cycle_days for record in completed if record.cycle_days is not None]
        blocked = [record for record in completed if record.blocked_days > 0]
        bucket_issues = [
            issue for issue in issues
            if _issue_touches_window(issue, bucket_start, bucket_end)
        ]
        completed_keys = {record.key for record in completed}
        issue_mix = issue_mix_metrics(bucket_issues, completed_keys)
        rows.append({
            "start": bucket_start,
            "end": bucket_end,
            "completed": len(completed),
            "bugs_completed": issue_mix["bugs_completed"],
            "cycle_median": _percentile(cycle_values, 50),
            "cycle_p95": _percentile(cycle_values, 95),
            "blocked_percent": len(blocked) / len(completed) * 100 if completed else 0.0,
            "prs": len(bucket_prs),
        })
        bucket_start = bucket_end
    return rows


def comparison_rows(previous: AnalysisResult, current: AnalysisResult) -> list[dict[str, object]]:
    specs = [
        ("Completed issues", previous.completed_count, current.completed_count, ""),
        ("Bugs completed", previous.issue_mix.get("bugs_completed"), current.issue_mix.get("bugs_completed"), ""),
        ("Jira issues touched", previous.jira_issue_count, current.jira_issue_count, ""),
        ("Releases", previous.releases.get("release_count"), current.releases.get("release_count"), ""),
        ("Median cycle time", previous.delivery.get("cycle_median"), current.delivery.get("cycle_median"), "days"),
        ("P95 cycle time", previous.delivery.get("cycle_p95"), current.delivery.get("cycle_p95"), "days"),
        ("Blocked issues", previous.delivery.get("blocked_percent"), current.delivery.get("blocked_percent"), "percent"),
        ("Median rework loops", previous.delivery.get("median_rework_loops"), current.delivery.get("median_rework_loops"), ""),
        ("GitHub PRs", previous.github.get("pr_count"), current.github.get("pr_count"), ""),
        ("Median PR lifetime", previous.github.get("lifetime_median"), current.github.get("lifetime_median"), "days"),
        ("Median first review", previous.github.get("first_review_median"), current.github.get("first_review_median"), "review_time"),
        ("Link coverage", previous.link_coverage, current.link_coverage, "percent"),
    ]
    return [
        {"metric": metric, "previous": previous_value, "current": current_value, "unit": unit}
        for metric, previous_value, current_value, unit in specs
    ]


def _issue_touches_window(issue: JiraIssue, start: datetime, end: datetime) -> bool:
    return _in_window(issue.updated, start, end) or _in_window(issue.resolved, start, end)


def _issue_completed_in_window(issue: JiraIssue, start: datetime, end: datetime) -> bool:
    if _in_window(issue.resolved, start, end):
        return True
    return issue.resolved is None and (issue.status or "").casefold() in {"done", "closed", "released"} and _in_window(issue.updated, start, end)


def _pr_touches_window(pr: PullRequest, start: datetime, end: datetime) -> bool:
    return (
        _in_window(pr.merged_at, start, end)
        or _in_window(pr.closed_at, start, end)
        or (pr.merged_at is None and pr.closed_at is None and _in_window(pr.updated_at, start, end))
    )


def _in_window(value: datetime | None, start: datetime, end: datetime) -> bool:
    if value is None:
        return False
    comparable = value if value.tzinfo else value.replace(tzinfo=UTC)
    return start <= comparable < end


def _percentile(values: list[float], q: float) -> float | None:
    from delivery_archaeology.metrics import percentile

    return percentile(values, q)
