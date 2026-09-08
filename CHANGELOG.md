# Changelog

## 1.0.3 - Clearer Work Mix Reporting

- Renamed Jira issue analysis output to report touched Jira issues consistently.
- Renamed the analysis JSON data-quality field from `jira_issues_analysed` to `jira_issues_touched`.
- Excluded Dependabot PRs from the `PRs without Jira links` count.

## 1.0.2 - Licensing and Ethics Documentation

- Added explicit Apache License 2.0 project metadata.
- Added README guidance for the project philosophy and licensing.
- Added a code of ethics for responsible system-level delivery analysis.

## 1.0.1 - Security Hardening

- Sanitised local cache filenames derived from Jira project keys and GitHub repository names.
- URL-encoded Jira path parameters for project, version, status, and issue remote-link requests.
- Added a CLI warning when `JIRA_VERIFY_SSL=false` disables TLS certificate verification.
- Added the Apache License 2.0 and a project code of ethics for responsible system-level use.

## 1.0.0 - First Usable Version

This is the first usable command-line release of Delivery Archaeology.

Included:

- Jira Server/Data Center connection checks, project discovery, project inspection, and status discovery.
- GitHub repository discovery using the authenticated `gh` CLI.
- Full delivery analysis across one or more Jira projects and GitHub repositories.
- Local raw-data caching for Jira issues, Jira changelogs, Jira project versions, Jira development links, and GitHub pull requests.
- Jira/GitHub linking by issue key only.
- Delivery-flow metrics including completed work, throughput, cycle-time distribution, state time, blocked time, waiting time, handoffs, flow efficiency, and rework loops.
- GitHub PR-flow metrics including PR count, PR lifetime, time to first review, approval-to-merge time, PR size, and review count.
- Issue type mix, bug counts, fix-version evidence, and Jira releases in the selected timeframe.
- Deterministic evidence-backed findings.
- Review candidates with links to Jira issues and GitHub PRs worth examining.
- Period comparison via `delivery compare`.
- Repository inference via Jira PR links, with bounded GitHub search fallback.
- Machine-readable JSON output for `analyse`, `compare`, and `infer-repos`.
- Processed report copies under `data/processed/` with command and UTC run metadata.
- Local generated-data cleanup via `delivery data cleanup`.
- Privacy guidance in `PRIVACY.md`.

Not included in v1:

- Web dashboards.
- Notebook workflows.
- Database storage.
- LLM-generated findings.
- Developer rankings, individual productivity scores, commit leaderboards, LOC productivity, or story-point comparisons between teams.
