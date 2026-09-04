from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable

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


def _review_submitted_at(review: dict) -> datetime | None:
    from delivery_archaeology.normalize import parse_dt

    return parse_dt(review.get("submittedAt") or review.get("createdAt"))
