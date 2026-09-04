from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    title: str
    observation: str
    evidence: list[str]
    sample_size: int
    interpretation: str


def build_findings(
    delivery: dict[str, object],
    github: dict[str, object],
    *,
    link_coverage: float,
    unknown_statuses: set[str],
    missing_resolution_dates: int,
    pr_without_links: int,
) -> list[Finding]:
    findings: list[Finding] = []
    sample = int(delivery.get("sample_size") or 0)
    blocked = _num(delivery.get("blocked_cycle_median"))
    unblocked = _num(delivery.get("unblocked_cycle_median"))
    blocked_percent = _num(delivery.get("blocked_percent")) or 0
    if blocked and unblocked and blocked >= unblocked * 1.5:
        findings.append(Finding(
            title="BLOCKED WORK HAS HIGH DELIVERY IMPACT",
            observation=f"Issues experiencing a blocked state had a median cycle time {blocked / unblocked:.1f}x higher than non-blocked issues.",
            evidence=[
                f"{blocked_percent:.0f}% of completed issues entered a blocked state.",
                f"Median cycle time when blocked: {blocked:.1f} days.",
                f"Median cycle time otherwise: {unblocked:.1f} days.",
            ],
            sample_size=sample,
            interpretation="Blocked states are a likely high-leverage constraint to investigate before optimizing normal development activity.",
        ))
    cycle_median = _num(delivery.get("cycle_median"))
    cycle_p95 = _num(delivery.get("cycle_p95"))
    if cycle_median and cycle_p95 and cycle_p95 >= cycle_median * 3:
        findings.append(Finding(
            title="DELIVERY VARIABILITY IS HIGH",
            observation="The upper tail of cycle time is much longer than the typical completed issue.",
            evidence=[
                f"Median cycle time: {cycle_median:.1f} days.",
                f"P95 cycle time: {cycle_p95:.1f} days.",
            ],
            sample_size=sample,
            interpretation="Predictability may be a larger constraint than median delivery speed.",
        ))
    state_medians = delivery.get("state_medians") or {}
    if isinstance(state_medians, dict):
        ready = _num(state_medians.get("ready"))
        review = _num(state_medians.get("review"))
        qa = _num(state_medians.get("qa"))
        if ready and cycle_median and ready / cycle_median >= 0.2:
            findings.append(Finding(
                title="INVESTIGATE READY AND WAITING TIME",
                observation="A material share of elapsed time appears before development starts.",
                evidence=[f"Median ready time: {ready:.1f} days.", f"Median cycle time: {cycle_median:.1f} days."],
                sample_size=sample,
                interpretation="Queue time before work starts may be a meaningful source of elapsed delivery time.",
            ))
        if review is not None and cycle_median and review / cycle_median <= 0.15:
            findings.append(Finding(
                title="CODE REVIEW DOES NOT APPEAR TO BE THE PRIMARY BOTTLENECK",
                observation="Median review time is a small share of observed cycle time.",
                evidence=[f"Median review time: {review:.1f} days.", f"Median cycle time: {cycle_median:.1f} days."],
                sample_size=sample,
                interpretation="The evidence does not currently support review speed as the largest source of delivery delay.",
            ))
        if qa and cycle_median and qa / cycle_median >= 0.2:
            findings.append(Finding(
                title="QA TIME IS A SIGNIFICANT DELIVERY COMPONENT",
                observation="QA accounts for a material share of typical cycle time.",
                evidence=[f"Median QA time: {qa:.1f} days.", f"Median cycle time: {cycle_median:.1f} days."],
                sample_size=sample,
                interpretation="Testing, validation, or release-readiness queues may deserve focused investigation.",
            ))
    first_review_p95 = _num(github.get("first_review_p95"))
    first_review_median = _num(github.get("first_review_median"))
    if first_review_p95 and first_review_median and first_review_p95 >= max(first_review_median * 4, 2):
        findings.append(Finding(
            title="FIRST REVIEW TIME HAS A LONG TAIL",
            observation="Some PRs wait much longer than typical before receiving review.",
            evidence=[
                f"Median time to first review: {_days_or_hours(first_review_median)}.",
                f"P95 time to first review: {_days_or_hours(first_review_p95)}.",
            ],
            sample_size=int(github.get("sample_size") or 0),
            interpretation="Review availability or batching may affect a minority of PRs enough to create delivery variability.",
        ))
    if link_coverage < 70:
        findings.append(Finding(
            title="JIRA TO GITHUB LINKING COVERAGE IS LOW",
            observation="A substantial share of completed Jira issues could not be linked to PR evidence.",
            evidence=[f"Link coverage: {link_coverage:.1f}%.", f"PRs without Jira links: {pr_without_links}."],
            sample_size=sample,
            interpretation="Improve issue-key usage in PR titles, branches, bodies, or commits before relying heavily on Jira/GitHub combined analysis.",
        ))
    if unknown_statuses:
        findings.append(Finding(
            title="UNKNOWN JIRA STATUSES NEED MAPPING",
            observation="Some Jira statuses were not classified into common workflow states.",
            evidence=[", ".join(sorted(unknown_statuses))],
            sample_size=sample,
            interpretation="Update config/status_mapping.yaml; unmapped statuses are excluded from state-duration analysis.",
        ))
    if missing_resolution_dates:
        findings.append(Finding(
            title="COMPLETION DATA QUALITY NEEDS ATTENTION",
            observation="Some completed-looking issues are missing resolution dates.",
            evidence=[f"Missing resolution dates: {missing_resolution_dates}."],
            sample_size=sample,
            interpretation="Cycle-time analysis is more reliable when Jira resolution dates are consistently populated.",
        ))
    return findings[:10]


def _num(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _days_or_hours(days: float) -> str:
    if days < 1:
        return f"{days * 24:.1f} hours"
    return f"{days:.1f} days"
