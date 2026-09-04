from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

from delivery_archaeology.config import StatusMapping
from delivery_archaeology.normalize import JiraIssue


class StateSegment(NamedTuple):
    issue_key: str
    state: str
    status: str
    start: datetime
    end: datetime | None

    @property
    def days(self) -> float:
        if not self.end:
            return 0.0
        return max((self.end - self.start).total_seconds() / 86400, 0.0)


def reconstruct_issue(issue: JiraIssue, mapping: StatusMapping) -> list[StateSegment]:
    if not issue.created:
        return []
    status_changes = [c for c in issue.changelog if c.field.casefold() == "status"]
    status_changes.sort(key=lambda c: c.timestamp)
    initial_status = status_changes[0].from_value if status_changes and status_changes[0].from_value else issue.status
    if not initial_status:
        return []
    segments: list[StateSegment] = []
    current_status = initial_status
    current_start = issue.created
    for change in status_changes:
        state = mapping.classify(current_status)
        if state is not None:
            segments.append(StateSegment(issue.key, state, current_status, current_start, change.timestamp))
        current_status = change.to_value or current_status
        current_start = change.timestamp
    end = issue.resolved or issue.updated
    state = mapping.classify(current_status)
    if state is not None and end and end >= current_start:
        segments.append(StateSegment(issue.key, state, current_status, current_start, end))
    return segments


def cycle_time_days(segments: list[StateSegment]) -> float | None:
    active = [segment for segment in segments if segment.state != "backlog"]
    if not active:
        return None
    return sum(segment.days for segment in active if segment.state != "done")


def rework_loops(segments: list[StateSegment]) -> int:
    states = [segment.state for segment in segments if segment.state not in {"backlog", "done"}]
    seen: set[str] = set()
    loops = 0
    previous = None
    for state in states:
        if state != previous and state in seen:
            loops += 1
        seen.add(state)
        previous = state
    return loops


def handoffs(segments: list[StateSegment]) -> int:
    states = [segment.state for segment in segments]
    return sum(1 for previous, current in zip(states, states[1:]) if previous != current)


def blocked_days(segments: list[StateSegment]) -> float:
    return sum(segment.days for segment in segments if segment.state == "blocked")


def waiting_days(segments: list[StateSegment]) -> float:
    return sum(segment.days for segment in segments if segment.state in {"ready", "review", "qa", "release", "blocked"})
