from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable

import pandas as pd

from delivery_archaeology.flow import StateSegment, blocked_days, cycle_time_days, handoffs, rework_loops, waiting_days
from delivery_archaeology.normalize import JiraIssue, PullRequest


def percentile(values: Iterable[float], q: float) -> float | None:
    clean = [v for v in values if pd.notna(v)]
    if not clean:
        return None
    return float(pd.Series(clean).quantile(q / 100))


@dataclass(frozen=True)
class IssueFlowRecord:
    key: str
    completed: bool
    cycle_days: float | None
    blocked_days: float
    waiting_days: float
    rework_loops: int
    handoffs: int


@dataclass(frozen=True)
class ReviewCandidate:
    kind: str
    identifier: str
    title: str
    url: str
    reason: str
    evidence: str
    score: float


def issue_flow_records(issues: list[JiraIssue], timelines: dict[str, list[StateSegment]]) -> list[IssueFlowRecord]:
    records: list[IssueFlowRecord] = []
    for issue in issues:
        segments = timelines.get(issue.key, [])
        completed = issue.resolved is not None or (issue.status or "").casefold() in {"done", "closed", "released"}
        records.append(IssueFlowRecord(
            key=issue.key,
            completed=completed,
            cycle_days=cycle_time_days(segments),
            blocked_days=blocked_days(segments),
            waiting_days=waiting_days(segments),
            rework_loops=rework_loops(segments),
            handoffs=handoffs(segments),
        ))
    return records


def delivery_metrics(records: list[IssueFlowRecord], timelines: dict[str, list[StateSegment]], days: int) -> dict[str, object]:
    completed = [r for r in records if r.completed and r.cycle_days is not None]
    cycle = [r.cycle_days for r in completed if r.cycle_days is not None]
    by_state: dict[str, list[float]] = defaultdict(list)
    for segments in timelines.values():
        for segment in segments:
            if segment.state != "done":
                by_state[segment.state].append(segment.days)
    blocked_records = [r for r in completed if r.blocked_days > 0]
    unblocked_records = [r for r in completed if r.blocked_days == 0]
    return {
        "issue_count": len(records),
        "completed_count": len(completed),
        "throughput_per_week": len(completed) / max(days / 7, 1),
        "cycle_median": percentile(cycle, 50),
        "cycle_p75": percentile(cycle, 75),
        "cycle_p95": percentile(cycle, 95),
        "age_unresolved": age_unresolved(records),
        "state_medians": {state: percentile(values, 50) for state, values in sorted(by_state.items())},
        "state_p95": {state: percentile(values, 95) for state, values in sorted(by_state.items())},
        "blocked_percent": len(blocked_records) / len(completed) * 100 if completed else 0.0,
        "blocked_cycle_median": percentile([r.cycle_days for r in blocked_records if r.cycle_days is not None], 50),
        "unblocked_cycle_median": percentile([r.cycle_days for r in unblocked_records if r.cycle_days is not None], 50),
        "median_rework_loops": percentile([r.rework_loops for r in completed], 50),
        "issues_with_rework": sum(1 for r in completed if r.rework_loops > 0),
        "median_handoffs": percentile([r.handoffs for r in completed], 50),
        "flow_efficiency": flow_efficiency(completed),
        "sample_size": len(completed),
    }


def issue_mix_metrics(issues: list[JiraIssue], completed_keys: set[str]) -> dict[str, object]:
    touched_by_type = _count_issue_types(issues)
    completed_issues = [issue for issue in issues if issue.key in completed_keys]
    completed_by_type = _count_issue_types(completed_issues)
    touched_by_priority = _count_priorities(issues)
    completed_by_priority = _count_priorities(completed_issues)
    bugs_touched = sum(count for issue_type, count in touched_by_type.items() if is_bug_issue_type(issue_type))
    bugs_completed = sum(count for issue_type, count in completed_by_type.items() if is_bug_issue_type(issue_type))
    fix_version_counts: dict[str, int] = defaultdict(int)
    for issue in completed_issues:
        for version in issue.fix_versions:
            fix_version_counts[version] += 1
    return {
        "sample_size": len(issues),
        "completed_sample_size": len(completed_issues),
        "touched_by_type": dict(sorted(touched_by_type.items(), key=lambda item: (-item[1], item[0].casefold()))),
        "completed_by_type": dict(sorted(completed_by_type.items(), key=lambda item: (-item[1], item[0].casefold()))),
        "touched_by_priority": dict(sorted(touched_by_priority.items(), key=_priority_sort_key)),
        "completed_by_priority": dict(sorted(completed_by_priority.items(), key=_priority_sort_key)),
        "bugs_touched": bugs_touched,
        "bugs_completed": bugs_completed,
        "bug_percent_touched": bugs_touched / len(issues) * 100 if issues else 0.0,
        "bug_percent_completed": bugs_completed / len(completed_issues) * 100 if completed_issues else 0.0,
        "fix_versions_on_completed_work": [
            {"name": name, "completed_issues": count}
            for name, count in sorted(fix_version_counts.items(), key=lambda item: (-item[1], item[0].casefold()))
        ],
    }


def release_metrics(versions: list[dict[str, Any]], *, start: datetime, end: datetime) -> dict[str, object]:
    errors = [
        {"project": version.get("project"), "error": version.get("_error")}
        for version in versions
        if version.get("_error")
    ]
    releases = []
    for version in versions:
        release_date = _parse_release_date(version.get("releaseDate"))
        if release_date is None or not (start <= release_date < end):
            continue
        releases.append({
            "project": version.get("project"),
            "name": version.get("name"),
            "release_date": release_date.date().isoformat(),
            "released": bool(version.get("released")),
            "archived": bool(version.get("archived")),
        })
    releases = sorted(
        releases,
        key=lambda item: (str(item["release_date"]), str(item.get("project") or ""), str(item.get("name") or "")),
    )
    return {
        "release_count": len(releases),
        "released_count": sum(1 for release in releases if release["released"]),
        "unreleased_count": sum(1 for release in releases if not release["released"]),
        "sample_size": len(versions),
        "error_count": len(errors),
        "errors": errors,
        "releases": releases,
    }


def is_bug_issue_type(issue_type: str | None) -> bool:
    normalized = (issue_type or "").casefold()
    return "bug" in normalized or "defect" in normalized


def age_unresolved(records: list[IssueFlowRecord]) -> dict[str, float | None]:
    unresolved = [r.cycle_days for r in records if not r.completed and r.cycle_days is not None]
    return {
        "median": percentile(unresolved, 50),
        "p95": percentile(unresolved, 95),
        "sample": len(unresolved),
    }


def flow_efficiency(records: list[IssueFlowRecord]) -> float | None:
    ratios = []
    for record in records:
        if record.cycle_days and record.cycle_days > 0:
            active = max(record.cycle_days - record.waiting_days, 0)
            ratios.append(active / record.cycle_days * 100)
    return percentile(ratios, 50)


def pr_metrics(prs: list[PullRequest]) -> dict[str, object]:
    lifetimes = []
    first_reviews = []
    approval_to_merge = []
    additions = []
    changed_files = []
    review_counts = []
    for pr in prs:
        end = pr.merged_at or pr.closed_at
        if pr.created_at and end:
            lifetimes.append((end - pr.created_at).total_seconds() / 86400)
        review_times = sorted([
            parsed for review in pr.reviews
            if (parsed := _review_submitted_at(review)) is not None
        ])
        if pr.created_at and review_times:
            first_reviews.append((review_times[0] - pr.created_at).total_seconds() / 86400)
        approvals = [
            parsed for review in pr.reviews
            if str(review.get("state", "")).casefold() == "approved"
            and (parsed := _review_submitted_at(review)) is not None
        ]
        if pr.merged_at and approvals:
            approval_to_merge.append((pr.merged_at - min(approvals)).total_seconds() / 86400)
        if pr.additions is not None:
            additions.append(pr.additions)
        if pr.changed_files is not None:
            changed_files.append(pr.changed_files)
        review_counts.append(len(pr.reviews))
    return {
        "pr_count": len(prs),
        "lifetime_median": percentile(lifetimes, 50),
        "lifetime_p75": percentile(lifetimes, 75),
        "lifetime_p95": percentile(lifetimes, 95),
        "first_review_median": percentile(first_reviews, 50),
        "first_review_p95": percentile(first_reviews, 95),
        "approval_to_merge_median": percentile(approval_to_merge, 50),
        "additions_median": percentile(additions, 50),
        "changed_files_median": percentile(changed_files, 50),
        "review_count_median": percentile(review_counts, 50),
        "sample_size": len(prs),
    }


def issue_review_candidates(
    issues: list[JiraIssue],
    records: list[IssueFlowRecord],
    *,
    jira_url: str,
    limit: int = 8,
) -> list[ReviewCandidate]:
    issue_by_key = {issue.key: issue for issue in issues}
    candidates: list[ReviewCandidate] = []
    completed = [record for record in records if record.completed]
    cycle_p95 = percentile([record.cycle_days for record in completed if record.cycle_days is not None], 95)
    for record in completed:
        issue = issue_by_key.get(record.key)
        if not issue:
            continue
        reasons: list[tuple[int, str, str]] = []
        if record.cycle_days is not None and cycle_p95 is not None and record.cycle_days >= cycle_p95:
            reasons.append((40, "Cycle-time outlier", f"Cycle time {record.cycle_days:.1f}d, at or above P95 {cycle_p95:.1f}d."))
        if record.blocked_days > 0:
            reasons.append((30 + int(record.blocked_days), "Blocked-time candidate", f"Blocked for {record.blocked_days:.1f}d."))
        if record.rework_loops > 0:
            reasons.append((25 + record.rework_loops, "Workflow-loop candidate", f"Re-entered a previous workflow state {record.rework_loops} time(s)."))
        if not reasons:
            continue
        priority, reason, _evidence = max(reasons, key=lambda item: item[0])
        evidence = " ".join(item[2] for item in sorted(reasons, key=lambda item: item[0], reverse=True))
        candidates.append(ReviewCandidate(
            kind="issue",
            identifier=record.key,
            title=issue.summary or "",
            url=f"{jira_url.rstrip('/')}/browse/{record.key}",
            reason=reason,
            evidence=evidence,
            score=float(priority),
        ))
    return sorted(candidates, key=_candidate_sort_key)[:limit]


def pr_review_candidates(prs: list[PullRequest], *, limit: int = 8) -> list[ReviewCandidate]:
    candidates: list[ReviewCandidate] = []
    lifetimes = [_pr_lifetime_days(pr) for pr in prs]
    first_reviews = [_pr_first_review_days(pr) for pr in prs]
    review_counts = [len(pr.reviews) for pr in prs]
    lifetime_p95 = percentile([value for value in lifetimes if value is not None], 95)
    first_review_p95 = percentile([value for value in first_reviews if value is not None], 95)
    review_count_p95 = percentile(review_counts, 95)
    for pr in prs:
        reasons: list[tuple[float, str, str]] = []
        lifetime = _pr_lifetime_days(pr)
        first_review = _pr_first_review_days(pr)
        review_count = len(pr.reviews)
        if lifetime is not None and lifetime_p95 is not None and lifetime >= lifetime_p95:
            reasons.append((40 + lifetime, "PR lifetime outlier", f"Open-to-close lifetime {lifetime:.1f}d, at or above P95 {lifetime_p95:.1f}d."))
        if first_review is not None and first_review_p95 is not None and first_review >= first_review_p95:
            reasons.append((35 + first_review, "Slow first review", f"First review after {_days_or_hours(first_review)}, at or above P95 {_days_or_hours(first_review_p95)}."))
        if review_count_p95 is not None and review_count > 0 and review_count >= review_count_p95 and review_count >= 5:
            reasons.append((30 + review_count, "High review back-and-forth", f"{review_count} review events, at or above P95 {review_count_p95:.0f}."))
        if not reasons:
            continue
        priority, reason, _evidence = max(reasons, key=lambda item: item[0])
        evidence = " ".join(item[2] for item in sorted(reasons, key=lambda item: item[0], reverse=True))
        candidates.append(ReviewCandidate(
            kind="pr",
            identifier=f"{pr.repository}#{pr.number}",
            title=pr.title,
            url=pr.url or f"https://github.com/{pr.repository}/pull/{pr.number}",
            reason=reason,
            evidence=evidence,
            score=float(priority),
        ))
    return sorted(candidates, key=_candidate_sort_key)[:limit]


def _candidate_sort_key(candidate: ReviewCandidate) -> tuple[int, float, str]:
    reason_rank = {
        "Cycle-time outlier": 0,
        "PR lifetime outlier": 1,
        "Blocked-time candidate": 2,
        "Slow first review": 3,
        "High review back-and-forth": 4,
        "Workflow-loop candidate": 5,
    }
    return (reason_rank.get(candidate.reason, 99), -candidate.score, candidate.identifier)


def _pr_lifetime_days(pr: PullRequest) -> float | None:
    end = pr.merged_at or pr.closed_at
    if not pr.created_at or not end:
        return None
    return (end - pr.created_at).total_seconds() / 86400


def _pr_first_review_days(pr: PullRequest) -> float | None:
    review_times = sorted([
        parsed for review in pr.reviews
        if (parsed := _review_submitted_at(review)) is not None
    ])
    if not pr.created_at or not review_times:
        return None
    return (review_times[0] - pr.created_at).total_seconds() / 86400


def _days_or_hours(days: float) -> str:
    if days < 1:
        return f"{days * 24:.1f}h"
    return f"{days:.1f}d"


def _review_submitted_at(review: dict) -> datetime | None:
    from delivery_archaeology.normalize import parse_dt

    return parse_dt(review.get("submittedAt") or review.get("createdAt"))


def _count_issue_types(issues: list[JiraIssue]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for issue in issues:
        counts[issue.issue_type or "Unknown"] += 1
    return counts


def _count_priorities(issues: list[JiraIssue]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for issue in issues:
        counts[issue.priority or "Unknown"] += 1
    return counts


def _priority_sort_key(item: tuple[str, int]) -> tuple[int, int, str]:
    name, count = item
    normalized = name.casefold().replace(" ", "")
    rank = {
        "p0": 0,
        "blocker": 0,
        "highest": 0,
        "critical": 0,
        "p1": 1,
        "high": 1,
        "p2": 2,
        "medium": 2,
        "p3": 3,
        "low": 3,
        "p4": 4,
        "lowest": 4,
        "unknown": 99,
    }.get(normalized, 50)
    return (rank, -count, name.casefold())


def _parse_release_date(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=UTC)
    except ValueError:
        return None
