from __future__ import annotations

import re
from collections import defaultdict

from delivery_archaeology.normalize import PullRequest


ISSUE_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


def keys_in_pr(pr: PullRequest) -> set[str]:
    parts = [
        pr.title,
        pr.head_ref_name or "",
        pr.body or "",
        " ".join(_commit_messages(pr)),
    ]
    return set(ISSUE_KEY_RE.findall("\n".join(parts)))


def link_prs_to_issues(prs: list[PullRequest]) -> dict[str, list[PullRequest]]:
    linked: dict[str, list[PullRequest]] = defaultdict(list)
    for pr in prs:
        for key in keys_in_pr(pr):
            linked[key].append(pr)
    return dict(linked)


def _commit_messages(pr: PullRequest) -> list[str]:
    messages: list[str] = []
    for commit in pr.commits:
        if not isinstance(commit, dict):
            continue
        message = commit.get("message") or commit.get("messageHeadline")
        if message:
            messages.append(str(message))
    return messages
