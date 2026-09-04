from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from delivery_archaeology.config import JiraSettings, StatusMapping, load_env_file
from delivery_archaeology.flow import reconstruct_issue, rework_loops
from delivery_archaeology.github import PR_LIST_FIELDS, pr_list_args, pr_view_args
from delivery_archaeology.jira import search_payload, updated_since_jql
from delivery_archaeology.linking import keys_in_pr
from delivery_archaeology.metrics import delivery_metrics, issue_flow_records, pr_metrics
from delivery_archaeology.normalize import JiraChange, JiraIssue, PullRequest


def test_status_mapping_reports_unknowns() -> None:
    mapping = StatusMapping(states={"done": ["Done"], "review": ["Code Review"]})
    assert mapping.classify("code review") == "review"
    assert mapping.unknown_statuses({"Done", "Mystery"}) == {"Mystery"}


def test_jira_url_requires_protocol() -> None:
    with pytest.raises(ValidationError, match="JIRA_URL must include http:// or https://"):
        JiraSettings.model_validate({"JIRA_URL": "jira.example.internal"})


def test_jira_url_is_trimmed() -> None:
    settings = JiraSettings.model_validate({"JIRA_URL": " https://jira.example.internal/ "})
    assert settings.url == "https://jira.example.internal"


def test_load_env_file_reads_jira_url(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("""
# local config
export JIRA_URL="https://jira.example.internal/"
JIRA_USERNAME=alice
JIRA_VERIFY_SSL=false # local self-signed cert
""")
    values = load_env_file(env_file)
    assert values["JIRA_URL"] == "https://jira.example.internal/"
    assert values["JIRA_USERNAME"] == "alice"
    assert values["JIRA_VERIFY_SSL"] == "false"


def test_from_env_reads_dotenv_when_process_env_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_USERNAME", raising=False)
    (tmp_path / ".env").write_text("JIRA_URL=https://jira.example.internal\nJIRA_USERNAME=alice\n")
    settings = JiraSettings.from_env()
    assert settings.url == "https://jira.example.internal"
    assert settings.username == "alice"


def test_updated_since_jql_quotes_projects_and_uses_start_of_day() -> None:
    assert updated_since_jql(["PAY", "LEDGER"], 180) == (
        'project in ("PAY", "LEDGER") AND updated >= startOfDay("-180d") ORDER BY updated ASC'
    )


def test_jira_search_payload_uses_array_expand_for_server_compatibility() -> None:
    payload = search_payload("project = PAY", expand="changelog")
    assert payload["expand"] == ["changelog"]
    assert payload["fields"] == ["*all"]


def test_github_list_query_avoids_nested_node_heavy_fields() -> None:
    args = pr_list_args("org/repo", "2026-01-01")
    fields = args[args.index("--json") + 1].split(",")
    assert "reviews" not in fields
    assert "commits" not in fields
    assert "body" not in fields
    assert fields == PR_LIST_FIELDS


def test_github_view_query_fetches_pr_detail_for_one_pr() -> None:
    args = pr_view_args("org/repo", 123, ["number", "reviews", "commits"])
    assert args[:5] == ["pr", "view", "123", "--repo", "org/repo"]
    assert args[-1] == "number,reviews,commits"


def test_reconstruct_preserves_repeated_states_as_rework() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    issue = JiraIssue(
        id="1",
        key="PAY-123",
        project="PAY",
        status="Done",
        created=start,
        updated=start + timedelta(days=9),
        resolved=start + timedelta(days=9),
        changelog=[
            JiraChange(issue_key="PAY-123", timestamp=start + timedelta(days=1), field="status", from_value="Ready", to_value="In Progress"),
            JiraChange(issue_key="PAY-123", timestamp=start + timedelta(days=4), field="status", from_value="In Progress", to_value="Code Review"),
            JiraChange(issue_key="PAY-123", timestamp=start + timedelta(days=5), field="status", from_value="Code Review", to_value="In Progress"),
            JiraChange(issue_key="PAY-123", timestamp=start + timedelta(days=6), field="status", from_value="In Progress", to_value="Done"),
        ],
    )
    mapping = StatusMapping(states={"ready": ["Ready"], "development": ["In Progress"], "review": ["Code Review"], "done": ["Done"]})
    timeline = reconstruct_issue(issue, mapping)
    assert [segment.state for segment in timeline] == ["ready", "development", "review", "development", "done"]
    assert rework_loops(timeline) == 1


def test_links_keys_from_title_branch_body_and_commits() -> None:
    pr = PullRequest(
        repository="org/repo",
        number=7,
        title="PAY-123 add capture",
        body="Related to LEDGER-9",
        head_ref_name="feature/IDENT-22-login",
        commits=[{"message": "PLAT-44 wire dependency"}],
    )
    assert keys_in_pr(pr) == {"PAY-123", "LEDGER-9", "IDENT-22", "PLAT-44"}


def test_delivery_metrics_include_blocked_impact() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    mapping = StatusMapping(states={"ready": ["Ready"], "development": ["In Progress"], "blocked": ["Blocked"], "done": ["Done"]})
    issues = [
        JiraIssue(id="1", key="PAY-1", project="PAY", status="Done", created=start, updated=start + timedelta(days=4), resolved=start + timedelta(days=4), changelog=[
            JiraChange(issue_key="PAY-1", timestamp=start + timedelta(days=1), field="status", from_value="Ready", to_value="In Progress"),
            JiraChange(issue_key="PAY-1", timestamp=start + timedelta(days=4), field="status", from_value="In Progress", to_value="Done"),
        ]),
        JiraIssue(id="2", key="PAY-2", project="PAY", status="Done", created=start, updated=start + timedelta(days=10), resolved=start + timedelta(days=10), changelog=[
            JiraChange(issue_key="PAY-2", timestamp=start + timedelta(days=1), field="status", from_value="Ready", to_value="In Progress"),
            JiraChange(issue_key="PAY-2", timestamp=start + timedelta(days=3), field="status", from_value="In Progress", to_value="Blocked"),
            JiraChange(issue_key="PAY-2", timestamp=start + timedelta(days=8), field="status", from_value="Blocked", to_value="In Progress"),
            JiraChange(issue_key="PAY-2", timestamp=start + timedelta(days=10), field="status", from_value="In Progress", to_value="Done"),
        ]),
    ]
    timelines = {issue.key: reconstruct_issue(issue, mapping) for issue in issues}
    records = issue_flow_records(issues, timelines)
    metrics = delivery_metrics(records, timelines, 14)
    assert metrics["completed_count"] == 2
    assert metrics["blocked_percent"] == 50
    assert metrics["blocked_cycle_median"] == 10


def test_pr_metrics() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    pr = PullRequest(
        repository="org/repo",
        number=1,
        title="PAY-1",
        created_at=start,
        merged_at=start + timedelta(days=2),
        additions=10,
        changed_files=3,
        reviews=[{"state": "APPROVED", "submittedAt": (start + timedelta(hours=6)).isoformat()}],
    )
    metrics = pr_metrics([pr])
    assert metrics["pr_count"] == 1
    assert metrics["lifetime_median"] == 2
    assert metrics["first_review_median"] == 0.25
