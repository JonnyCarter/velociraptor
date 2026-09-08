from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from delivery_archaeology.findings import Finding
from delivery_archaeology.flow import StateSegment
from delivery_archaeology.metrics import ReviewCandidate
from delivery_archaeology.normalize import JiraIssue, PullRequest

if TYPE_CHECKING:
    from delivery_archaeology.analysis import AnalysisResult


def render_analysis_report(
    *,
    start: datetime,
    end: datetime,
    jira_projects: list[str],
    repos: list[str],
    delivery: dict[str, object],
    github: dict[str, object],
    issue_mix: dict[str, object],
    releases: dict[str, object],
    jira_issue_count: int,
    completed_count: int,
    linked_completed: int,
    link_coverage: float,
    unknown_statuses: set[str],
    missing_resolution_dates: int,
    pr_without_links: int,
    findings: list[Finding],
    issue_candidates: list[ReviewCandidate],
    pr_candidates: list[ReviewCandidate],
    weekly_rows: list[dict[str, object]],
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
        f"Jira issues touched:         {jira_issue_count}",
        f"Completed issues:            {completed_count}",
        f"GitHub PRs:                  {github.get('pr_count', 0)}",
        f"Issues linked to PRs:        {linked_completed} ({link_coverage:.1f}%)",
        f"Unknown Jira statuses:       {', '.join(sorted(unknown_statuses)) if unknown_statuses else 'none'}",
        f"Missing resolution dates:    {missing_resolution_dates}",
        f"PRs without Jira links:      {pr_without_links}",
        "",
        "WORK MIX AND RELEASES",
        "---------------------",
        "",
        f"Touched Jira issues:         {jira_issue_count}",
        f"Bugs touched:                {issue_mix.get('bugs_touched', 0)} ({_fmt_pct(issue_mix.get('bug_percent_touched'))})",
        f"Bugs completed:              {issue_mix.get('bugs_completed', 0)} ({_fmt_pct(issue_mix.get('bug_percent_completed'))})",
        f"Releases in period:          {releases.get('release_count', 0)}",
        f"Release data errors:         {releases.get('error_count', 0)}",
        "",
        "Issue types touched",
        "",
        *_render_count_mapping(issue_mix.get("touched_by_type")),
        "",
        "Issue types completed",
        "",
        *_render_count_mapping(issue_mix.get("completed_by_type")),
        "",
        "Priority/severity touched",
        "",
        *_render_count_mapping(issue_mix.get("touched_by_priority")),
        "",
        "Priority/severity completed",
        "",
        *_render_count_mapping(issue_mix.get("completed_by_priority")),
        "",
        "Releases",
        "",
        *_render_releases(releases.get("releases")),
        "",
        "Fix versions on completed work",
        "",
        *_render_fix_versions(issue_mix.get("fix_versions_on_completed_work")),
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
        "REVIEW CANDIDATES",
        "=================",
        "",
        "Jira issues worth examining",
        "---------------------------",
    ])
    lines.extend(_render_candidates(issue_candidates))
    lines.extend([
        "",
        "PRs worth examining",
        "-------------------",
    ])
    lines.extend(_render_candidates(pr_candidates))
    lines.extend([
        "",
        "WEEKLY BREAKDOWN",
        "================",
        "",
    ])
    lines.extend(_render_weekly_rows(weekly_rows))
    lines.extend([
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


def render_compare_report(
    *,
    jira_projects: list[str],
    repos: list[str],
    previous: AnalysisResult,
    current: AnalysisResult,
    rows: list[dict[str, object]],
) -> str:
    lines = [
        "DELIVERY COMPARISON",
        "===================",
        "",
        "Current period:",
        f"{current.start:%-d %B %Y} - {current.end:%-d %B %Y}",
        "",
        "Previous period:",
        f"{previous.start:%-d %B %Y} - {previous.end:%-d %B %Y}",
        "",
        "Scope",
        f"Jira projects: {', '.join(jira_projects)}",
        "Repositories:",
        *[f"  {repo}" for repo in repos],
        "",
        f"{'Metric':<28} {'Previous':>12} {'Current':>12} {'Change':>12}",
        "-" * 67,
    ]
    for row in rows:
        unit = str(row["unit"])
        previous_value = row["previous"]
        current_value = row["current"]
        lines.append(
            f"{str(row['metric']):<28} "
            f"{_fmt_unit(previous_value, unit):>12} "
            f"{_fmt_unit(current_value, unit):>12} "
            f"{_fmt_change(previous_value, current_value, unit):>12}"
        )
    lines.extend([
        "",
        "Data quality",
        "------------",
        f"Previous unknown statuses:   {', '.join(sorted(previous.unknown_statuses)) if previous.unknown_statuses else 'none'}",
        f"Current unknown statuses:    {', '.join(sorted(current.unknown_statuses)) if current.unknown_statuses else 'none'}",
        f"Previous link coverage:      {previous.link_coverage:.1f}%",
        f"Current link coverage:       {current.link_coverage:.1f}%",
        "",
    ])
    sample_warning = _sample_warning(previous.completed_count, current.completed_count)
    if sample_warning:
        lines.extend(["Sample warning", "--------------", sample_warning, ""])
    lines.extend([
        "Comparison notes",
        "----------------",
        *_comparison_notes(previous, current),
        "",
        "Current review candidates",
        "-------------------------",
    ])
    lines.extend(_render_candidates(current.issue_candidates[:5]))
    lines.extend(["", "Current PR candidates", "---------------------"])
    lines.extend(_render_candidates(current.pr_candidates[:5]))
    return "\n".join(lines).rstrip() + "\n"


def render_repo_inference_report(
    *,
    jira_projects: list[str],
    org: str | None,
    days: int,
    issue_key_count: int,
    searched_pr_count: int,
    candidates: list[dict[str, object]],
    command: str,
    min_prs: int,
    source: str,
) -> str:
    lines = [
        "REPOSITORY INFERENCE",
        "====================",
        "",
        "Scope",
        f"Jira projects: {', '.join(jira_projects)}",
        f"GitHub org:    {org or 'not used'}",
        f"Period:        last {days} days",
        "",
        "Evidence",
        f"Source:                    {source}",
        f"Jira issue keys searched:    {issue_key_count}",
        f"PR links/matches found:      {searched_pr_count}",
        f"Minimum PRs per repo:        {min_prs}",
        "",
        f"{'Repository':<36} {'PRs':>6} {'Issues':>8}",
        "-" * 53,
    ]
    if not candidates:
        lines.extend([
            "none",
            "",
            "No repositories met the evidence threshold. If Jira has no development-link evidence, pass --org to allow the GitHub issue-search fallback.",
        ])
        return "\n".join(lines).rstrip() + "\n"
    for candidate in candidates:
        lines.append(
            f"{str(candidate['repository']):<36} "
            f"{int(candidate['pr_count']):>6} "
            f"{int(candidate['issue_count']):>8}"
        )
    lines.extend(["", "Examples", "--------"])
    for candidate in candidates[:5]:
        examples = candidate.get("examples") or []
        if not isinstance(examples, list) or not examples:
            continue
        lines.append(str(candidate["repository"]))
        for example in examples[:2]:
            if not isinstance(example, dict):
                continue
            title = example.get("title") or ""
            url = example.get("url") or ""
            lines.append(f"  - {title}")
            if url:
                lines.append(f"    {url}")
    lines.extend(["", "Suggested command", "-----------------", command])
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


def _fmt_unit(value: object, unit: str) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    if unit == "days":
        return f"{value:.1f}d"
    if unit == "review_time":
        return _fmt_review(value)
    if unit == "percent":
        return f"{value:.1f}%"
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def _fmt_change(previous: object, current: object, unit: str) -> str:
    if not isinstance(previous, (int, float)) or not isinstance(current, (int, float)):
        return "n/a"
    delta = current - previous
    if unit == "percent":
        return f"{delta:+.1f}pp"
    if previous == 0:
        return "n/a"
    return f"{(delta / previous) * 100:+.0f}%"


def _render_candidates(candidates: list[ReviewCandidate]) -> list[str]:
    if not candidates:
        return ["none"]
    lines: list[str] = []
    for candidate in candidates:
        title = f" - {candidate.title}" if candidate.title else ""
        lines.extend([
            f"{candidate.identifier}{title}",
            f"Reason:   {candidate.reason}",
            f"Evidence: {candidate.evidence}",
            f"Link:     {candidate.url}",
            "",
        ])
    return lines[:-1]


def _render_weekly_rows(rows: list[dict[str, object]]) -> list[str]:
    if not rows:
        return ["none"]
    lines = [f"{'Week':<23} {'Done':>6} {'Bugs':>6} {'Median':>8} {'P95':>8} {'Blocked':>9} {'PRs':>6}"]
    for row in rows:
        start = row["start"]
        end = row["end"]
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            continue
        lines.append(
            f"{start:%-d %b} - {end:%-d %b}".ljust(23)
            + f" {int(row['completed']):>6}"
            + f" {int(row.get('bugs_completed', 0)):>6}"
            + f" {_fmt_unit(row['cycle_median'], 'days'):>8}"
            + f" {_fmt_unit(row['cycle_p95'], 'days'):>8}"
            + f" {_fmt_unit(row['blocked_percent'], 'percent'):>9}"
            + f" {int(row['prs']):>6}"
        )
    return lines


def _render_count_mapping(value: object) -> list[str]:
    if not isinstance(value, dict) or not value:
        return ["none"]
    return [f"{str(name):<28}{int(count)}" for name, count in value.items()]


def _render_releases(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        return ["none"]
    lines = [f"{'DATE':<12} {'PROJECT':<10} {'STATUS':<10} NAME"]
    for release in value[:12]:
        if not isinstance(release, dict):
            continue
        status = "released" if release.get("released") else "planned"
        name = str(release.get("name") or "")
        project = str(release.get("project") or "")
        release_date = str(release.get("release_date") or "")
        lines.append(f"{release_date:<12} {project:<10} {status:<10} {name}")
    if len(value) > 12:
        lines.append(f"... {len(value) - 12} more")
    return lines


def _render_fix_versions(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        return ["none"]
    lines = [f"{'COMPLETED':>9}  VERSION"]
    for item in value[:12]:
        if not isinstance(item, dict):
            continue
        lines.append(f"{int(item.get('completed_issues', 0)):>9}  {item.get('name') or ''}")
    if len(value) > 12:
        lines.append(f"... {len(value) - 12} more")
    return lines


def _sample_warning(previous_count: int, current_count: int) -> str | None:
    if previous_count < 10 or current_count < 10:
        return "One or both periods have fewer than 10 completed issues. Treat percentile movement as directional, not definitive."
    return None


def _comparison_notes(previous: AnalysisResult, current: AnalysisResult) -> list[str]:
    notes: list[str] = []
    _add_directional_note(notes, "Completed work", previous.completed_count, current.completed_count, higher_is_bad=False)
    _add_directional_note(notes, "P95 cycle time", previous.delivery.get("cycle_p95"), current.delivery.get("cycle_p95"), higher_is_bad=True)
    _add_directional_note(notes, "Blocked-work exposure", previous.delivery.get("blocked_percent"), current.delivery.get("blocked_percent"), higher_is_bad=True, suffix="pp")
    _add_directional_note(notes, "Median PR lifetime", previous.github.get("lifetime_median"), current.github.get("lifetime_median"), higher_is_bad=True)
    _add_directional_note(notes, "Link coverage", previous.link_coverage, current.link_coverage, higher_is_bad=False, suffix="pp")
    return notes or ["No material directional movement detected in the core metrics."]


def _add_directional_note(
    notes: list[str],
    label: str,
    previous: object,
    current: object,
    *,
    higher_is_bad: bool,
    suffix: str = "%",
) -> None:
    if not isinstance(previous, (int, float)) or not isinstance(current, (int, float)) or previous == current:
        return
    if suffix == "pp":
        change = current - previous
        if abs(change) < 5:
            return
        direction = "increased" if change > 0 else "decreased"
        interpretation = "worse" if (change > 0) == higher_is_bad else "better"
        notes.append(f"{label} {direction} by {abs(change):.1f} percentage points ({interpretation}).")
        return
    if previous == 0:
        return
    percent = (current - previous) / previous * 100
    if abs(percent) < 20:
        return
    direction = "increased" if percent > 0 else "decreased"
    interpretation = "worse" if (percent > 0) == higher_is_bad else "better"
    notes.append(f"{label} {direction} by {abs(percent):.0f}% ({interpretation}).")
