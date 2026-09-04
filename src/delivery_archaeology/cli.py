from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import typer
from pydantic import ValidationError

from delivery_archaeology.config import JiraSettings, StatusMapping
from delivery_archaeology.findings import build_findings
from delivery_archaeology.flow import blocked_days, cycle_time_days, reconstruct_issue, rework_loops
from delivery_archaeology.github import load_or_fetch_all_prs, relative_updated, repos_for_org
from delivery_archaeology.jira import JiraApiError, JiraClient, inspect_project, load_or_fetch_issues, period_start
from delivery_archaeology.linking import keys_in_pr, link_prs_to_issues
from delivery_archaeology.metrics import delivery_metrics, issue_flow_records, pr_metrics
from delivery_archaeology.normalize import normalize_jira_issues, normalize_prs
from delivery_archaeology.reporting import render_analysis_report, render_issue


app = typer.Typer(help="Analyse software delivery flow from Jira and GitHub evidence.")
jira_app = typer.Typer(help="Jira discovery and extraction helpers.")
github_app = typer.Typer(help="GitHub discovery and PR-flow helpers.")
app.add_typer(jira_app, name="jira")
app.add_typer(github_app, name="github")


def jira_settings_or_exit() -> JiraSettings:
    try:
        return JiraSettings.from_env()
    except ValidationError as exc:
        messages = [str(error["msg"]).removeprefix("Value error, ") for error in exc.errors()]
        typer.echo("Jira configuration error:", err=True)
        for message in messages:
            typer.echo(f"  {message}", err=True)
        raise typer.Exit(2) from exc


def jira_api_error_or_exit(exc: JiraApiError) -> None:
    typer.echo("Jira API error:", err=True)
    typer.echo(f"  HTTP {exc.status_code} from {exc.method} {exc.path}", err=True)
    typer.echo(f"  {exc.detail}", err=True)
    if exc.jql:
        typer.echo(f"  JQL: {exc.jql}", err=True)
    raise typer.Exit(1) from exc


@jira_app.command("test")
def jira_test() -> None:
    settings = jira_settings_or_exit()
    client = JiraClient(settings)
    try:
        user = client.myself()
    except JiraApiError as exc:
        jira_api_error_or_exit(exc)
    finally:
        client.close()
    typer.echo(f"Connected to: {settings.url}")
    typer.echo(f"User: {user.get('name') or user.get('displayName') or user.get('emailAddress') or 'unknown'}")
    typer.echo("Status: OK")


@jira_app.command("projects")
def jira_projects(contains: Annotated[str | None, typer.Option(help="Case-insensitive project name/key filter.")] = None) -> None:
    client = JiraClient(jira_settings_or_exit())
    try:
        projects = client.projects()
    except JiraApiError as exc:
        jira_api_error_or_exit(exc)
    finally:
        client.close()
    if contains:
        needle = contains.casefold()
        projects = [p for p in projects if needle in p.get("key", "").casefold() or needle in p.get("name", "").casefold()]
    typer.echo(f"{'KEY':<10} NAME")
    for project in sorted(projects, key=lambda p: p.get("key", "")):
        typer.echo(f"{project.get('key', ''):<10} {project.get('name', '')}")


@jira_app.command("inspect")
def jira_inspect(project_key: str, days: Annotated[int, typer.Option()] = 180) -> None:
    client = JiraClient(jira_settings_or_exit())
    try:
        info = inspect_project(client, project_key, days)
    except JiraApiError as exc:
        jira_api_error_or_exit(exc)
    finally:
        client.close()
    project = info["project"]
    typer.echo(f"Project: {project.get('key')} - {project.get('name')}")
    typer.echo("")
    typer.echo(f"Issues updated in last {days} days: {info['issue_count']}")
    typer.echo("")
    typer.echo("Issue Types")
    typer.echo("-----------")
    for name, count in sorted(info["issue_types"].items(), key=lambda item: item[1], reverse=True):
        typer.echo(f"{name:<16}{count}")
    typer.echo("")
    typer.echo("Statuses")
    typer.echo("--------")
    for status in info["statuses"]:
        typer.echo(status)
    typer.echo("")
    typer.echo("Likely useful fields")
    typer.echo("--------------------")
    for field in info["useful_fields"]:
        typer.echo(f"{field['name']:<24}{field['id']}")


@jira_app.command("statuses")
def jira_statuses(project_key: str) -> None:
    client = JiraClient(jira_settings_or_exit())
    try:
        statuses = client.statuses_for_project(project_key)
    except JiraApiError as exc:
        jira_api_error_or_exit(exc)
    finally:
        client.close()
    for status in statuses:
        typer.echo(status)


@github_app.command("repos")
def github_repos(org: str) -> None:
    repos = repos_for_org(org)
    typer.echo(f"{'REPOSITORY':<28} UPDATED")
    for repo in sorted(repos, key=lambda r: r.get("name", "")):
        updated = "archived" if repo.get("isArchived") else relative_updated(repo.get("updatedAt"))
        typer.echo(f"{repo.get('name', ''):<28} {updated}")


@app.command("analyse")
def analyse(
    jira_project: Annotated[list[str], typer.Option("--jira-project", help="Jira project key. Can be repeated.")],
    repo: Annotated[list[str], typer.Option("--repo", help="GitHub repo as owner/name. Can be repeated.")],
    days: Annotated[int, typer.Option(help="Lookback period in days.")] = 180,
    refresh: Annotated[bool, typer.Option(help="Fetch fresh raw Jira and GitHub data.")] = False,
) -> None:
    mapping = StatusMapping.load()
    client = JiraClient(jira_settings_or_exit())
    try:
        raw_issues = load_or_fetch_issues(client, jira_project, days, refresh=refresh)
    except JiraApiError as exc:
        jira_api_error_or_exit(exc)
    finally:
        client.close()
    raw_prs = load_or_fetch_all_prs(repo, days, refresh=refresh)
    issues = normalize_jira_issues(raw_issues)
    prs = normalize_prs(raw_prs)
    timelines = {issue.key: reconstruct_issue(issue, mapping) for issue in issues}
    records = issue_flow_records(issues, timelines)
    linked = link_prs_to_issues(prs)
    completed_keys = {r.key for r in records if r.completed}
    linked_completed = len([key for key in completed_keys if linked.get(key)])
    link_coverage = linked_completed / len(completed_keys) * 100 if completed_keys else 0.0
    pr_without_links = sum(1 for pr in prs if not keys_in_pr(pr))
    statuses = {issue.status for issue in issues if issue.status}
    statuses.update(change.to_value for issue in issues for change in issue.changelog if change.field.casefold() == "status" and change.to_value)
    unknown_statuses = mapping.unknown_statuses(statuses)
    missing_resolution_dates = sum(1 for issue in issues if (issue.status or "").casefold() in {"done", "closed", "released"} and issue.resolved is None)
    delivery = delivery_metrics(records, timelines, days)
    github = pr_metrics(prs)
    findings = build_findings(
        delivery,
        github,
        link_coverage=link_coverage,
        unknown_statuses=unknown_statuses,
        missing_resolution_dates=missing_resolution_dates,
        pr_without_links=pr_without_links,
    )
    typer.echo(render_analysis_report(
        start=period_start(days),
        end=datetime.now(UTC),
        jira_projects=jira_project,
        repos=repo,
        delivery=delivery,
        github=github,
        jira_issue_count=len(issues),
        completed_count=len(completed_keys),
        linked_completed=linked_completed,
        link_coverage=link_coverage,
        unknown_statuses=unknown_statuses,
        missing_resolution_dates=missing_resolution_dates,
        pr_without_links=pr_without_links,
        findings=findings,
    ))


@app.command("flow")
def flow(
    jira_project: Annotated[list[str], typer.Option("--jira-project")],
    days: Annotated[int, typer.Option()] = 180,
    refresh: Annotated[bool, typer.Option()] = False,
) -> None:
    analyse(jira_project=jira_project, repo=[], days=days, refresh=refresh)


@app.command("issue")
def issue(issue_key: str, days: Annotated[int, typer.Option()] = 365, refresh: Annotated[bool, typer.Option()] = False) -> None:
    project = issue_key.split("-", 1)[0]
    mapping = StatusMapping.load()
    client = JiraClient(jira_settings_or_exit())
    try:
        raw = load_or_fetch_issues(client, [project], days, refresh=refresh)
    except JiraApiError as exc:
        jira_api_error_or_exit(exc)
    finally:
        client.close()
    issues = normalize_jira_issues(raw)
    found = next((candidate for candidate in issues if candidate.key == issue_key), None)
    if not found:
        typer.echo(f"Issue {issue_key} not found in cached/fetched project data.", err=True)
        raise typer.Exit(1)
    timeline = reconstruct_issue(found, mapping)
    typer.echo(render_issue(found, timeline, [], cycle_time_days(timeline), blocked_days(timeline), rework_loops(timeline)))


@app.command("epic")
def epic(epic_key: str) -> None:
    typer.echo("Epic analysis is reserved for the next iteration. Use `delivery analyse` for the initial workflow.")


if __name__ == "__main__":
    app()
