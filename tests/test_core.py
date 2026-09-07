from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from delivery_archaeology.analysis import analyse_window, comparison_rows, weekly_breakdown
from delivery_archaeology.art import VELOCIRAPTOR
from delivery_archaeology.config import JiraSettings, StatusMapping, load_env_file
from delivery_archaeology.flow import reconstruct_issue, rework_loops
from delivery_archaeology.github import GITHUB_SEARCH_ISSUE_KEY_CHUNK_SIZE, GhCliError, PR_LIST_FIELDS, filter_and_sort_repos, pr_list_args, pr_view_args
from delivery_archaeology.github import infer_repos_from_search_results, search_prs_for_issue_keys
from delivery_archaeology.inference import analyse_command_for_repos, extract_pr_urls, infer_repos_from_jira_development_links, issue_keys_for_repo_inference, repo_from_pr_url
from delivery_archaeology.jira import covering_cache_path_for_projects, load_or_fetch_development_links, search_payload, updated_since_jql
from delivery_archaeology.linking import keys_in_pr
from delivery_archaeology.metrics import delivery_metrics, issue_flow_records, issue_review_candidates, pr_metrics, pr_review_candidates
from delivery_archaeology.normalize import JiraChange, JiraIssue, PullRequest
from delivery_archaeology.serialization import analysis_payload, compare_payload, repo_inference_payload, to_pretty_json


def test_status_mapping_reports_unknowns() -> None:
    mapping = StatusMapping(states={"done": ["Done"], "review": ["Code Review"]})
    assert mapping.classify("code review") == "review"
    assert mapping.unknown_statuses({"Done", "Mystery"}) == {"Mystery"}


def test_velociraptor_art_is_available() -> None:
    assert "velociraptor" in VELOCIRAPTOR


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


def test_jira_covering_cache_uses_smallest_matching_superset(tmp_path, monkeypatch) -> None:
    import delivery_archaeology.jira as jira

    monkeypatch.setattr(jira, "RAW_JIRA_DIR", tmp_path)
    (tmp_path / "issues_PAY_180d.json").write_text("[]")
    (tmp_path / "issues_PAY_30d.json").write_text("[]")
    assert covering_cache_path_for_projects(["PAY"], 14) == tmp_path / "issues_PAY_30d.json"


def test_development_link_progress_counts_uncached_issues(tmp_path, monkeypatch) -> None:
    import delivery_archaeology.jira as jira

    class FakeClient:
        def remote_links(self, issue_key: str) -> list[dict[str, object]]:
            return [{"object": {"url": f"https://github.example/org/repo/pull/{issue_key[-1]}"}}]

        def dev_status_pull_requests(self, issue_id: str) -> dict[str, object]:
            return {"issueId": issue_id}

    monkeypatch.setattr(jira, "RAW_JIRA_DIR", tmp_path)
    cache = tmp_path / "development_links_PAY_180d.json"
    cache.write_text(json.dumps({"PAY-1": {"id": "1", "remote_links": [], "dev_status": {}, "errors": []}}))
    messages: list[str] = []
    issues = [
        JiraIssue(id="1", key="PAY-1", project="PAY", updated=datetime(2026, 1, 1, tzinfo=UTC)),
        JiraIssue(id="2", key="PAY-2", project="PAY", updated=datetime(2026, 1, 2, tzinfo=UTC)),
    ]

    result = load_or_fetch_development_links(
        FakeClient(),
        issues,
        ["PAY"],
        180,
        refresh=False,
        progress=messages.append,
    )

    assert "Fetching Jira development links for 1 issues" in messages
    assert "Jira development links fetched: 1/1" in messages
    assert set(result) == {"PAY-1", "PAY-2"}


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


def test_filter_and_sort_repos_skips_archived_and_sorts_by_updated() -> None:
    repos = [
        {"name": "payments-web", "updatedAt": "2026-01-02T00:00:00Z", "isArchived": False},
        {"name": "old-payments", "updatedAt": "2026-01-03T00:00:00Z", "isArchived": True},
        {"name": "payments-api", "updatedAt": "2026-01-04T00:00:00Z", "isArchived": False},
    ]
    result = filter_and_sort_repos(repos, contains="payments")
    assert [repo["name"] for repo in result] == ["payments-api", "payments-web"]


def test_filter_and_sort_repos_can_sort_by_name_and_include_archived() -> None:
    repos = [
        {"name": "zeta", "updatedAt": "2026-01-04T00:00:00Z", "isArchived": False},
        {"name": "alpha", "updatedAt": "2026-01-01T00:00:00Z", "isArchived": True},
    ]
    result = filter_and_sort_repos(repos, include_archived=True, sort="name")
    assert [repo["name"] for repo in result] == ["alpha", "zeta"]


def test_issue_keys_for_repo_inference_uses_recent_non_epic_work() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    issues = [
        JiraIssue(id="1", key="PAY-1", project="PAY", issue_type="Epic", updated=base + timedelta(days=3)),
        JiraIssue(id="2", key="PAY-2", project="PAY", issue_type="Story", updated=base + timedelta(days=2)),
        JiraIssue(id="3", key="PAY-3", project="PAY", issue_type="Bug", updated=base + timedelta(days=4)),
    ]
    assert issue_keys_for_repo_inference(issues) == ["PAY-3", "PAY-2"]


def test_infer_repos_from_search_results_counts_pr_and_issue_evidence() -> None:
    items = [
        {
            "repository_url": "https://api.github.com/repos/my-org/payments-api",
            "title": "PAY-1 add validation",
            "body": "",
            "html_url": "https://github.com/my-org/payments-api/pull/1",
        },
        {
            "repository_url": "https://api.github.com/repos/my-org/payments-api",
            "title": "PAY-2 fix capture",
            "body": "",
            "html_url": "https://github.com/my-org/payments-api/pull/2",
        },
        {
            "repository_url": "https://api.github.com/repos/my-org/payments-web",
            "title": "PAY-1 UI",
            "body": "",
            "html_url": "https://github.com/my-org/payments-web/pull/3",
        },
    ]
    repos = infer_repos_from_search_results(items, {"PAY-1", "PAY-2"})
    assert repos[0]["repository"] == "my-org/payments-api"
    assert repos[0]["pr_count"] == 2
    assert repos[0]["issue_count"] == 2


def test_github_issue_search_uses_small_chunks_to_avoid_operator_limit(monkeypatch) -> None:
    import delivery_archaeology.github as github

    calls: list[list[str]] = []

    def fake_run_gh(args: list[str]) -> dict[str, object]:
        calls.append(args)
        return {"items": []}

    monkeypatch.setattr(github, "run_gh", fake_run_gh)

    search_prs_for_issue_keys("my-org", [f"PAY-{index}" for index in range(1, 8)], days=180)

    assert GITHUB_SEARCH_ISSUE_KEY_CHUNK_SIZE == 3
    assert len(calls) == 3
    queries = [next(arg.removeprefix("q=") for arg in call if arg.startswith("q=")) for call in calls]
    assert [query.count(" OR ") for query in queries] == [2, 2, 0]


def test_github_issue_search_retries_operator_limit_chunks_individually(monkeypatch) -> None:
    import delivery_archaeology.github as github

    calls: list[str] = []

    def fake_run_gh(args: list[str]) -> dict[str, object]:
        query = next(arg.removeprefix("q=") for arg in args if arg.startswith("q="))
        calls.append(query)
        if " OR " in query:
            raise GhCliError(args, "HTTP 422: more than 5 AND / OR operators")
        return {"items": [{"id": len(calls), "html_url": f"https://github.com/my-org/repo/pull/{len(calls)}"}]}

    monkeypatch.setattr(github, "run_gh", fake_run_gh)

    results = search_prs_for_issue_keys("my-org", ["PAY-1", "PAY-2", "PAY-3"], days=180)

    assert len(calls) == 4
    assert len(results) == 3
    assert all(" OR " not in query for query in calls[1:])


def test_analyse_command_for_repos_prints_ready_to_run_command() -> None:
    command = analyse_command_for_repos(["PAY"], ["my-org/payments-api", "my-org/payments-web"], 180)
    assert "--jira-project PAY" in command
    assert "--repo my-org/payments-api" in command
    assert command.endswith("--days 180")


def test_extract_pr_urls_from_jira_development_link_payloads() -> None:
    payload = {
        "remote_links": [{"object": {"url": "https://github.com/my-org/payments-api/pull/42"}}],
        "dev_status": {"detail": [{"pullRequests": [{"url": "https://github.internal/my-org/payments-web/pull/9"}]}]},
    }
    assert extract_pr_urls(payload) == [
        "https://github.com/my-org/payments-api/pull/42",
        "https://github.internal/my-org/payments-web/pull/9",
    ]
    assert repo_from_pr_url("https://github.com/my-org/payments-api/pull/42") == "my-org/payments-api"


def test_infer_repos_from_jira_development_links_counts_pr_links() -> None:
    links = {
        "PAY-1": {"remote_links": [{"object": {"url": "https://github.com/my-org/payments-api/pull/1"}}]},
        "PAY-2": {"dev_status": {"detail": [{"pullRequests": [{"url": "https://github.com/my-org/payments-api/pull/2"}]}]}},
        "PAY-3": {"remote_links": [{"object": {"url": "https://github.com/my-org/payments-web/pull/3"}}]},
    }
    repos = infer_repos_from_jira_development_links(links)
    assert repos[0]["repository"] == "my-org/payments-api"
    assert repos[0]["pr_count"] == 2
    assert repos[0]["issue_count"] == 2


def test_analysis_window_and_comparison_rows_split_same_raw_data() -> None:
    mapping = StatusMapping(states={"ready": ["Ready"], "development": ["In Progress"], "done": ["Done"]})
    base = datetime(2026, 1, 1, tzinfo=UTC)
    issues = [
        JiraIssue(id="1", key="PAY-1", project="PAY", summary="Previous", status="Done", created=base, updated=base + timedelta(days=4), resolved=base + timedelta(days=4), changelog=[
            JiraChange(issue_key="PAY-1", timestamp=base + timedelta(days=1), field="status", from_value="Ready", to_value="In Progress"),
            JiraChange(issue_key="PAY-1", timestamp=base + timedelta(days=4), field="status", from_value="In Progress", to_value="Done"),
        ]),
        JiraIssue(id="2", key="PAY-2", project="PAY", summary="Current", status="Done", created=base + timedelta(days=7), updated=base + timedelta(days=10), resolved=base + timedelta(days=10), changelog=[
            JiraChange(issue_key="PAY-2", timestamp=base + timedelta(days=8), field="status", from_value="Ready", to_value="In Progress"),
            JiraChange(issue_key="PAY-2", timestamp=base + timedelta(days=10), field="status", from_value="In Progress", to_value="Done"),
        ]),
    ]
    previous = analyse_window(issues=issues, prs=[], mapping=mapping, jira_url="https://jira.example", start=base, end=base + timedelta(days=7))
    current = analyse_window(issues=issues, prs=[], mapping=mapping, jira_url="https://jira.example", start=base + timedelta(days=7), end=base + timedelta(days=14))
    rows = comparison_rows(previous, current)
    assert previous.completed_count == 1
    assert current.completed_count == 1
    assert rows[0]["metric"] == "Completed issues"


def test_analysis_payload_is_json_serializable() -> None:
    mapping = StatusMapping(states={"ready": ["Ready"], "development": ["In Progress"], "done": ["Done"]})
    base = datetime(2026, 1, 1, tzinfo=UTC)
    issue = JiraIssue(id="1", key="PAY-1", project="PAY", summary="Done", status="Done", created=base, updated=base + timedelta(days=2), resolved=base + timedelta(days=2), changelog=[
        JiraChange(issue_key="PAY-1", timestamp=base + timedelta(days=1), field="status", from_value="Ready", to_value="In Progress"),
        JiraChange(issue_key="PAY-1", timestamp=base + timedelta(days=2), field="status", from_value="In Progress", to_value="Done"),
    ])
    result = analyse_window(issues=[issue], prs=[], mapping=mapping, jira_url="https://jira.example", start=base, end=base + timedelta(days=7))
    payload = analysis_payload(result=result, jira_projects=["PAY"], repos=[], weekly_rows=[])
    rendered = to_pretty_json(payload)
    assert '"report_type": "delivery_analysis"' in rendered
    assert '"jira_projects": [' in rendered


def test_compare_payload_is_json_serializable() -> None:
    mapping = StatusMapping(states={"ready": ["Ready"], "development": ["In Progress"], "done": ["Done"]})
    base = datetime(2026, 1, 1, tzinfo=UTC)
    previous = analyse_window(issues=[], prs=[], mapping=mapping, jira_url="https://jira.example", start=base, end=base + timedelta(days=7))
    current = analyse_window(issues=[], prs=[], mapping=mapping, jira_url="https://jira.example", start=base + timedelta(days=7), end=base + timedelta(days=14))
    payload = compare_payload(previous=previous, current=current, jira_projects=["PAY"], repos=[], rows=comparison_rows(previous, current))
    assert '"report_type": "delivery_comparison"' in to_pretty_json(payload)


def test_repo_inference_payload_is_json_serializable() -> None:
    payload = repo_inference_payload(
        jira_projects=["PAY"],
        org=None,
        days=180,
        issue_key_count=10,
        pr_match_count=4,
        candidates=[{"repository": "my-org/payments-api", "pr_count": 4, "issue_count": 3, "examples": []}],
        command="uv run delivery analyse --jira-project PAY --repo my-org/payments-api --days 180",
        min_prs=1,
        source="Jira development links",
    )
    assert '"report_type": "repository_inference"' in to_pretty_json(payload)


def test_weekly_breakdown_uses_completed_issues_per_bucket() -> None:
    mapping = StatusMapping(states={"ready": ["Ready"], "development": ["In Progress"], "done": ["Done"]})
    base = datetime(2026, 1, 1, tzinfo=UTC)
    issue = JiraIssue(id="1", key="PAY-1", project="PAY", summary="Done", status="Done", created=base, updated=base + timedelta(days=8), resolved=base + timedelta(days=8), changelog=[
        JiraChange(issue_key="PAY-1", timestamp=base + timedelta(days=1), field="status", from_value="Ready", to_value="In Progress"),
        JiraChange(issue_key="PAY-1", timestamp=base + timedelta(days=8), field="status", from_value="In Progress", to_value="Done"),
    ])
    rows = weekly_breakdown(issues=[issue], prs=[], mapping=mapping, start=base, end=base + timedelta(days=14))
    assert [row["completed"] for row in rows] == [0, 1]


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


def test_issue_review_candidates_include_jira_links_and_reasons() -> None:
    records = [
        type("Record", (), {"key": "PAY-1", "completed": True, "cycle_days": 4.0, "blocked_days": 0.0, "rework_loops": 0})(),
        type("Record", (), {"key": "PAY-2", "completed": True, "cycle_days": 20.0, "blocked_days": 6.0, "rework_loops": 1})(),
    ]
    issues = [
        JiraIssue(id="1", key="PAY-1", project="PAY", summary="Small change"),
        JiraIssue(id="2", key="PAY-2", project="PAY", summary="Slow change"),
    ]
    candidates = issue_review_candidates(issues, records, jira_url="https://jira.example.internal")
    assert candidates[0].identifier == "PAY-2"
    assert candidates[0].url == "https://jira.example.internal/browse/PAY-2"
    assert candidates[0].reason == "Cycle-time outlier"


def test_pr_review_candidates_include_pr_links_and_back_and_forth() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    reviews = [{"state": "COMMENTED", "submittedAt": (start + timedelta(hours=index)).isoformat()} for index in range(6)]
    prs = [
        PullRequest(repository="org/repo", number=1, title="PAY-1", created_at=start, merged_at=start + timedelta(days=1), reviews=[]),
        PullRequest(repository="org/repo", number=2, title="PAY-2", url="https://github.example/org/repo/pull/2", created_at=start, merged_at=start + timedelta(days=5), reviews=reviews),
    ]
    candidates = pr_review_candidates(prs)
    assert candidates[0].identifier == "org/repo#2"
    assert candidates[0].url == "https://github.example/org/repo/pull/2"
    assert candidates[0].reason in {"PR lifetime outlier", "High review back-and-forth"}


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
