from __future__ import annotations

from datetime import datetime

from delivery_archaeology.findings import Finding
from delivery_archaeology.flow import StateSegment
from delivery_archaeology.metrics import IssueFlowRecord
from delivery_archaeology.normalize import JiraIssue, PullRequest


def render_analysis_report(
    *,
    start: datetime,
    end: datetime,
    jira_projects: list[str],
    repos: list[str],
    delivery: dict[str, object],
    github: dict[str, object],
    jira_issue_count: int,
    completed_count: int,
    linked_completed: int,
    link_coverage: float,
    unknown_statuses: set[str],
    missing_resolution_dates: int,
    pr_without_links: int,
    findings: list[Finding],
) -> str:
    lines = [
        "DELIVERY ANALYSIS",
        "=================",
        "",
        "Period",
        f"{start:%-d %B %Y} - {end:%-d %B %Y}",
        "",
        "Scope",
        f"Jira projects: {', '.join(jira_projects)}",
        "Repositories:",
        *[f"  {repo}" for repo in repos],
        "",
        "Data quality",
        "------------",
        f"Jira issues analysed:        {jira_issue_count}",
        f"Completed issues:            {completed_count}",
        f"GitHub PRs:                  {github.get('pr_count', 0)}",
        f"Issues linked to PRs:        {linked_completed} ({link_coverage:.1f}%)",
        f"Unknown Jira statuses:       {', '.join(sorted(unknown_statuses)) if unknown_statuses else 'none'}",
        f"Missing resolution dates:    {missing_resolution_dates}",
        f"PRs without Jira links:      {pr_without_links}",
        "",
        "DELIVERY FLOW",
        "-------------",
        "",
        f"Completed:                   {completed_count}",
        f"Throughput per week:         {_fmt(delivery.get('throughput_per_week'))}",
        f"Median cycle time:           {_fmt_days(delivery.get('cycle_median'))}",
        f"P75:                         {_fmt_days(delivery.get('cycle_p75'))}",
        f"P95:                         {_fmt_days(delivery.get('cycle_p95'))}",
        f"Median flow efficiency:      {_fmt_pct(delivery.get('flow_efficiency'))}",
        f"Median rework loops:         {_fmt(delivery.get('median_rework_loops'))}",
        f"Median handoffs:             {_fmt(delivery.get('median_handoffs'))}",
        "",
        "Median time by state",
        "",
    ]
    state_medians = delivery.get("state_medians") or {}
    if isinstance(state_medians, dict):
        for state, value in state_medians.items():
            lines.append(f"{state.title():<28}{_fmt_days(value)}")
    lines.extend([
        "",
        "Issues experiencing blocked state:",
        f"{delivery.get('blocked_percent', 0):>31.0f}%",
        "",
        "Median cycle time when blocked:",
        f"{_fmt_days(delivery.get('blocked_cycle_median')):>32}",
        "",
        "Median cycle time otherwise:",
        f"{_fmt_days(delivery.get('unblocked_cycle_median')):>32}",
        "",
        "GITHUB FLOW",
        "-----------",
        "",
        f"PRs analysed:                {github.get('pr_count', 0)}",
        f"Median PR lifetime:          {_fmt_days(github.get('lifetime_median'))}",
        f"P75 PR lifetime:             {_fmt_days(github.get('lifetime_p75'))}",
        f"P95 PR lifetime:             {_fmt_days(github.get('lifetime_p95'))}",
        f"Median first review:         {_fmt_review(github.get('first_review_median'))}",
        f"P95 first review:            {_fmt_review(github.get('first_review_p95'))}",
        f"Median approval to merge:    {_fmt_review(github.get('approval_to_merge_median'))}",
        f"Median additions:            {_fmt(github.get('additions_median'))}",
        f"Median changed files:        {_fmt(github.get('changed_files_median'))}",
        f"Median review count:         {_fmt(github.get('review_count_median'))}",
        "",
        "FINDINGS",
        "========",
        "",
    ])
    for index, finding in enumerate(findings, start=1):
        lines.extend([
            f"{index}. {finding.title}",
            "",
            f"Observation: {finding.observation}",
            "Evidence:",
            *[f"- {item}" for item in finding.evidence],
            f"Sample: {finding.sample_size}",
            f"Interpretation: {finding.interpretation}",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def render_issue(issue: JiraIssue, timeline: list[StateSegment], prs: list[PullRequest], cycle_days: float | None, blocked_days: float, loops: int) -> str:
    lines = [f"{issue.key} - {issue.summary or ''}".rstrip(), ""]
    if issue.created:
        lines.append(f"Created             {issue.created:%-d %b}")
        lines.append("")
    for segment in timeline:
        lines.extend([
            segment.state.title(),
            f"{segment.start:%-d %b} -> {segment.end:%-d %b}      {segment.days:.1f}d" if segment.end else f"{segment.start:%-d %b} -> now",
            "",
        ])
    for pr in prs:
        lines.append(f"PR #{pr.number} opened       {pr.created_at:%-d %b}" if pr.created_at else f"PR #{pr.number} opened")
        if pr.merged_at:
            lines.append(f"Merged               {pr.merged_at:%-d %b}")
    lines.extend(["", f"Cycle time           {_fmt_days(cycle_days)}", f"Blocked              {blocked_days:.1f}d", f"Rework loops         {loops}"])
    return "\n".join(lines).rstrip() + "\n"


def _fmt(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:.1f}"


def _fmt_days(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:.1f} days"


def _fmt_review(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    if value < 1:
        return f"{value * 24:.1f}h"
    return f"{value:.1f}d"


def _fmt_pct(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:.1f}%"
