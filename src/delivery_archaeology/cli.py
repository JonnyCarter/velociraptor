from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

import typer
from pydantic import ValidationError

from delivery_archaeology.analysis import analyse_window, comparison_rows, weekly_breakdown
from delivery_archaeology.art import VELOCIRAPTOR
from delivery_archaeology.config import JiraSettings, StatusMapping
from delivery_archaeology.data_management import cleanup_candidates, cleanup_summary, delete_candidates, format_bytes, relative_path
from delivery_archaeology.flow import blocked_days, cycle_time_days, reconstruct_issue, rework_loops
from delivery_archaeology.github import GhCliError, filter_and_sort_repos, load_or_fetch_all_prs, relative_updated, repos_for_org
from delivery_archaeology.github import infer_repos_from_search_results, search_prs_for_issue_keys
from delivery_archaeology.inference import analyse_command_for_repos, infer_repos_from_jira_development_links, issue_keys_for_repo_inference
from delivery_archaeology.jira import JiraApiError, JiraClient, inspect_project, load_or_fetch_development_links, load_or_fetch_issues
from delivery_archaeology.normalize import normalize_jira_issues, normalize_prs
from delivery_archaeology.processed import current_command, payload_with_run_metadata, text_with_run_metadata, write_processed_report
from delivery_archaeology.reporting import render_analysis_report, render_compare_report, render_issue, render_repo_inference_report
from delivery_archaeology.serialization import analysis_payload, compare_payload, repo_inference_payload, to_pretty_json


app = typer.Typer(help="Analyse software delivery flow from Jira and GitHub evidence.")
jira_app = typer.Typer(help="Jira discovery and extraction helpers.")
github_app = typer.Typer(help="GitHub discovery and PR-flow helpers.")
data_app = typer.Typer(help="Inspect and clean local generated data.")
app.add_typer(jira_app, name="jira")
app.add_typer(github_app, name="github")
app.add_typer(data_app, name="data")


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


def gh_error_or_exit(exc: GhCliError) -> None:
    typer.echo("GitHub CLI error:", err=True)
    typer.echo(f"  gh {' '.join(exc.args_used)}", err=True)
    typer.echo(f"  {exc.stderr}", err=True)
    raise typer.Exit(1) from exc


def validate_output_format(output_format: str) -> str:
    normalized = output_format.casefold()
    if normalized not in {"text", "json"}:
        typer.echo("Format must be 'text' or 'json'.", err=True)
        raise typer.Exit(2)
    return normalized


def progress(message: str) -> None:
    typer.echo(f"[delivery] {message}", err=True)


@data_app.command("cleanup")
def data_cleanup(
    older_than_days: Annotated[int | None, typer.Option("--older-than-days", help="Only include generated files older than this many days.")] = None,
    include_raw: Annotated[bool, typer.Option("--raw/--no-raw", help="Include raw Jira/GitHub API caches.")] = True,
    include_processed: Annotated[bool, typer.Option("--processed/--no-processed", help="Include processed report files.")] = True,
    yes: Annotated[bool, typer.Option("--yes", help="Delete the listed files. Without this flag the command is a dry run.")] = False,
) -> None:
    candidates = cleanup_candidates(
        older_than_days=older_than_days,
        include_raw=include_raw,
        include_processed=include_processed,
    )
    summary = cleanup_summary(candidates)
    mode = "DELETE" if yes else "DRY RUN"
    typer.echo("LOCAL DATA CLEANUP")
    typer.echo("==================")
    typer.echo("")
    typer.echo(f"Mode:             {mode}")
    typer.echo(f"Files matched:    {summary['files']}")
    typer.echo(f"Total size:       {format_bytes(summary['bytes'])}")
    if older_than_days is not None:
        typer.echo(f"Older than days:  {older_than_days}")
    typer.echo("")
    if not candidates:
        typer.echo("No generated data files matched.")
        return
    typer.echo(f"{'SIZE':>10}  MODIFIED UTC          PATH")
    typer.echo("-" * 72)
    for candidate in candidates:
        typer.echo(
            f"{format_bytes(candidate.size_bytes):>10}  "
            f"{candidate.modified_at:%Y-%m-%d %H:%M}  "
            f"{relative_path(candidate.path)}"
        )
    if not yes:
        typer.echo("")
        typer.echo("No files deleted. Re-run with --yes to delete the listed files.")
        return
    delete_candidates(candidates)
    typer.echo("")
    typer.echo(f"Deleted {summary['files']} generated data files.")


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
def github_repos(
    org: str,
    contains: Annotated[str | None, typer.Option(help="Case-insensitive repository name filter.")] = None,
    include_archived: Annotated[bool, typer.Option(help="Include archived repositories.")] = False,
    sort: Annotated[str, typer.Option(help="Sort by 'updated' or 'name'.")] = "updated",
) -> None:
    if sort not in {"updated", "name"}:
        typer.echo("Sort must be 'updated' or 'name'.", err=True)
        raise typer.Exit(2)
    try:
        repos = repos_for_org(org)
    except GhCliError as exc:
        gh_error_or_exit(exc)
    repos = filter_and_sort_repos(
        repos,
        contains=contains,
        include_archived=include_archived,
        sort=sort,
    )
    typer.echo(f"{'REPOSITORY':<28} UPDATED")
    for repo in repos:
        updated = "archived" if repo.get("isArchived") else relative_updated(repo.get("updatedAt"))
        typer.echo(f"{repo.get('name', ''):<28} {updated}")


@app.command("analyse")
def analyse(
    jira_project: Annotated[list[str], typer.Option("--jira-project", help="Jira project key. Can be repeated.")],
    repo: Annotated[list[str], typer.Option("--repo", help="GitHub repo as owner/name. Can be repeated.")],
    days: Annotated[int, typer.Option(help="Lookback period in days.")] = 180,
    refresh: Annotated[bool, typer.Option(help="Fetch fresh raw Jira and GitHub data.")] = False,
    output_format: Annotated[str, typer.Option("--format", help="Output format: text or json.")] = "text",
) -> None:
    output_format = validate_output_format(output_format)
    run_at = datetime.now(UTC)
    run_command = current_command()
    mapping = StatusMapping.load()
    settings = jira_settings_or_exit()
    client = JiraClient(settings)
    try:
        raw_issues = load_or_fetch_issues(client, jira_project, days, refresh=refresh, progress=progress)
    except JiraApiError as exc:
        jira_api_error_or_exit(exc)
    finally:
        client.close()
    try:
        raw_prs = load_or_fetch_all_prs(repo, days, refresh=refresh, progress=progress)
    except GhCliError as exc:
        gh_error_or_exit(exc)
    issues = normalize_jira_issues(raw_issues)
    prs = normalize_prs(raw_prs)
    progress(f"Normalised {len(issues)} Jira issues and {len(prs)} GitHub PRs")
    end = run_at
    start = end - timedelta(days=days)
    progress("Reconstructing delivery timelines and calculating metrics")
    result = analyse_window(
        issues=issues,
        prs=prs,
        mapping=mapping,
        jira_url=settings.url,
        start=start,
        end=end,
    )
    progress("Building weekly breakdown")
    weekly_rows = weekly_breakdown(issues=issues, prs=prs, mapping=mapping, start=start, end=end)
    if output_format == "json":
        payload = payload_with_run_metadata(
            analysis_payload(
                result=result,
                jira_projects=jira_project,
                repos=repo,
                weekly_rows=weekly_rows,
            ),
            command=run_command,
            run_at=run_at,
        )
        report = to_pretty_json(payload)
        write_processed_report(
            report_name="analyse",
            content=report,
            output_format=output_format,
            command=run_command,
            run_at=run_at,
            progress=progress,
        )
        typer.echo(report)
        return
    report = render_analysis_report(
        start=start,
        end=end,
        jira_projects=jira_project,
        repos=repo,
        delivery=result.delivery,
        github=result.github,
        jira_issue_count=result.jira_issue_count,
        completed_count=result.completed_count,
        linked_completed=result.linked_completed,
        link_coverage=result.link_coverage,
        unknown_statuses=result.unknown_statuses,
        missing_resolution_dates=result.missing_resolution_dates,
        pr_without_links=result.pr_without_links,
        findings=result.findings,
        issue_candidates=result.issue_candidates,
        pr_candidates=result.pr_candidates,
        weekly_rows=weekly_rows,
    )
    write_processed_report(
        report_name="analyse",
        content=text_with_run_metadata(report, command=run_command, run_at=run_at),
        output_format=output_format,
        command=run_command,
        run_at=run_at,
        progress=progress,
    )
    typer.echo(report)


@app.command("compare")
def compare(
    jira_project: Annotated[list[str], typer.Option("--jira-project", "--project", help="Jira project key. Can be repeated.")],
    repo: Annotated[list[str], typer.Option("--repo", help="GitHub repo as owner/name. Can be repeated.")],
    days: Annotated[int, typer.Option(help="Current lookback window in days.")] = 7,
    compare_days: Annotated[int, typer.Option("--compare", help="Previous comparison window in days.")] = 7,
    refresh: Annotated[bool, typer.Option(help="Fetch fresh raw Jira and GitHub data.")] = False,
    output_format: Annotated[str, typer.Option("--format", help="Output format: text or json.")] = "text",
) -> None:
    output_format = validate_output_format(output_format)
    run_at = datetime.now(UTC)
    run_command = current_command()
    mapping = StatusMapping.load()
    settings = jira_settings_or_exit()
    total_days = days + compare_days
    client = JiraClient(settings)
    try:
        raw_issues = load_or_fetch_issues(client, jira_project, total_days, refresh=refresh, progress=progress)
    except JiraApiError as exc:
        jira_api_error_or_exit(exc)
    finally:
        client.close()
    try:
        raw_prs = load_or_fetch_all_prs(repo, total_days, refresh=refresh, progress=progress)
    except GhCliError as exc:
        gh_error_or_exit(exc)
    issues = normalize_jira_issues(raw_issues)
    prs = normalize_prs(raw_prs)
    progress(f"Normalised {len(issues)} Jira issues and {len(prs)} GitHub PRs")
    end = run_at
    current_start = end - timedelta(days=days)
    previous_start = current_start - timedelta(days=compare_days)
    progress("Analysing previous comparison window")
    previous = analyse_window(
        issues=issues,
        prs=prs,
        mapping=mapping,
        jira_url=settings.url,
        start=previous_start,
        end=current_start,
    )
    progress("Analysing current comparison window")
    current = analyse_window(
        issues=issues,
        prs=prs,
        mapping=mapping,
        jira_url=settings.url,
        start=current_start,
        end=end,
    )
    rows = comparison_rows(previous, current)
    if output_format == "json":
        payload = payload_with_run_metadata(
            compare_payload(
                previous=previous,
                current=current,
                jira_projects=jira_project,
                repos=repo,
                rows=rows,
            ),
            command=run_command,
            run_at=run_at,
        )
        report = to_pretty_json(payload)
        write_processed_report(
            report_name="compare",
            content=report,
            output_format=output_format,
            command=run_command,
            run_at=run_at,
            progress=progress,
        )
        typer.echo(report)
        return
    report = render_compare_report(
        jira_projects=jira_project,
        repos=repo,
        previous=previous,
        current=current,
        rows=rows,
    )
    write_processed_report(
        report_name="compare",
        content=text_with_run_metadata(report, command=run_command, run_at=run_at),
        output_format=output_format,
        command=run_command,
        run_at=run_at,
        progress=progress,
    )
    typer.echo(report)


@app.command("infer-repos")
def infer_repos(
    jira_project: Annotated[list[str], typer.Option("--jira-project", "--project", help="Jira project key. Can be repeated.")],
    org: Annotated[str | None, typer.Option("--org", help="GitHub organization to search if Jira has no linked PR evidence.")] = None,
    days: Annotated[int, typer.Option(help="Lookback period in days.")] = 180,
    max_issues: Annotated[int, typer.Option(help="Maximum recent Jira issue keys to search for.")] = 100,
    min_prs: Annotated[int, typer.Option(help="Minimum matching PRs for a repo to be suggested.")] = 1,
    max_repos: Annotated[int, typer.Option(help="Maximum repos to include in the suggested command.")] = 12,
    issue_type: Annotated[list[str] | None, typer.Option("--issue-type", help="Optional Jira issue type filter. Can be repeated.")] = None,
    refresh: Annotated[bool, typer.Option(help="Fetch fresh Jira data instead of using local cache.")] = False,
    output_format: Annotated[str, typer.Option("--format", help="Output format: text or json.")] = "text",
) -> None:
    output_format = validate_output_format(output_format)
    run_at = datetime.now(UTC)
    run_command = current_command()
    settings = jira_settings_or_exit()
    client = JiraClient(settings)
    try:
        raw_issues = load_or_fetch_issues(client, jira_project, days, refresh=refresh, progress=progress)
    except JiraApiError as exc:
        jira_api_error_or_exit(exc)
    finally:
        client.close()
    issues = normalize_jira_issues(raw_issues)
    issue_keys = issue_keys_for_repo_inference(
        issues,
        issue_types=issue_type,
        max_issues=max_issues,
    )
    sampled_issue_keys = set(issue_keys)
    sampled_issues = [issue for issue in issues if issue.key in sampled_issue_keys]
    progress(f"Selected {len(issue_keys)} Jira issue keys for repository inference")
    client = JiraClient(settings)
    try:
        development_links = load_or_fetch_development_links(
            client,
            sampled_issues,
            jira_project,
            days,
            refresh=refresh,
            progress=progress,
        )
    except JiraApiError as exc:
        jira_api_error_or_exit(exc)
    finally:
        client.close()
    source = "Jira development links"
    search_results = []
    progress("Inferring repositories from Jira development links")
    inferred = infer_repos_from_jira_development_links(development_links)
    if not inferred and org:
        source = "GitHub issue search fallback"
        progress("No Jira PR links found; using bounded GitHub issue-search fallback")
        try:
            search_results = search_prs_for_issue_keys(org, issue_keys, days=days, progress=progress)
        except GhCliError as exc:
            gh_error_or_exit(exc)
        inferred = infer_repos_from_search_results(search_results, sampled_issue_keys)
    candidates = [
        candidate
        for candidate in inferred
        if int(candidate["pr_count"]) >= min_prs
    ][:max_repos]
    repos = [str(candidate["repository"]) for candidate in candidates]
    pr_match_count = sum(int(candidate["pr_count"]) for candidate in inferred) if source == "Jira development links" else len(search_results)
    suggested_command = analyse_command_for_repos(jira_project, repos, days)
    if output_format == "json":
        payload = payload_with_run_metadata(
            repo_inference_payload(
                jira_projects=jira_project,
                org=org,
                days=days,
                issue_key_count=len(issue_keys),
                pr_match_count=pr_match_count,
                candidates=candidates,
                command=suggested_command,
                min_prs=min_prs,
                source=source,
            ),
            command=run_command,
            run_at=run_at,
        )
        report = to_pretty_json(payload)
        write_processed_report(
            report_name="infer-repos",
            content=report,
            output_format=output_format,
            command=run_command,
            run_at=run_at,
            progress=progress,
        )
        typer.echo(report)
        return
    report = render_repo_inference_report(
        jira_projects=jira_project,
        org=org,
        days=days,
        issue_key_count=len(issue_keys),
        searched_pr_count=pr_match_count,
        candidates=candidates,
        command=suggested_command,
        min_prs=min_prs,
        source=source,
    )
    write_processed_report(
        report_name="infer-repos",
        content=text_with_run_metadata(report, command=run_command, run_at=run_at),
        output_format=output_format,
        command=run_command,
        run_at=run_at,
        progress=progress,
    )
    typer.echo(report)


@app.command("flow")
def flow(
    jira_project: Annotated[list[str], typer.Option("--jira-project")],
    days: Annotated[int, typer.Option()] = 180,
    refresh: Annotated[bool, typer.Option()] = False,
) -> None:
    analyse(jira_project=jira_project, repo=[], days=days, refresh=refresh)


@app.command("issue")
def issue(issue_key: str, days: Annotated[int, typer.Option()] = 365, refresh: Annotated[bool, typer.Option()] = False) -> None:
    run_at = datetime.now(UTC)
    command = current_command()
    project = issue_key.split("-", 1)[0]
    mapping = StatusMapping.load()
    client = JiraClient(jira_settings_or_exit())
    try:
        raw = load_or_fetch_issues(client, [project], days, refresh=refresh, progress=progress)
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
    report = render_issue(found, timeline, [], cycle_time_days(timeline), blocked_days(timeline), rework_loops(timeline))
    write_processed_report(
        report_name="issue",
        content=text_with_run_metadata(report, command=command, run_at=run_at),
        output_format="text",
        command=command,
        run_at=run_at,
        progress=progress,
    )
    typer.echo(report)


@app.command("epic")
def epic(epic_key: str) -> None:
    typer.echo("Epic analysis is reserved for the next iteration. Use `delivery analyse` for the initial workflow.")


@app.command("raptor")
def raptor() -> None:
    typer.echo(VELOCIRAPTOR)


if __name__ == "__main__":
    app()
