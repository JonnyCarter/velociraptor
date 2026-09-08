![Delivery Archaeology velociraptor](Velociraptor.png)

# Delivery Archaeology

Small read-only command-line tool for analysing software delivery using Jira Server/Data Center and GitHub evidence.

Current version: `1.0.3`

It answers one practical question:

> Where did delivery time go, and what evidence points to the highest-leverage constraints in this team's delivery system?

This project intentionally measures the delivery system, not individual developer productivity. No dashboards, notebooks, developer rankings, productivity scores, commit leaderboards, or LOC productivity analysis are included.

## Philosophy

Delivery Archaeology is built around a simple idea: software delivery problems usually live in the system of work, not in isolated individuals.

The tool is intended to help engineering managers and teams:

- measure systems, not people;
- prefer evidence over intuition;
- separate observation from interpretation;
- use metrics to improve conversations, not assign blame;
- make every finding explainable and reproducible.

See [CODE_OF_ETHICS.md](CODE_OF_ETHICS.md) for the full project principles.

## Prerequisites

- Python 3.12 or newer.
- `uv` for dependency management. Install it using the official Astral instructions: <https://docs.astral.sh/uv/getting-started/installation/>.
- GitHub CLI, `gh`, for GitHub authentication and PR extraction. Install it using the official GitHub CLI instructions: <https://github.com/cli/cli#installation>.
- Jira Server/Data Center credentials with read access to the projects being analysed.

## Install

```bash
uv sync
uv run delivery --help
```

Copy `.env.example` to `.env` and configure Jira. The CLI reads `.env` from the directory where you run `delivery`; exported shell environment variables take precedence.

```bash
JIRA_URL=https://jira.example.internal
JIRA_USERNAME=alice
JIRA_PASSWORD=
JIRA_TOKEN=
JIRA_VERIFY_SSL=true
```

Use either `JIRA_TOKEN` or `JIRA_USERNAME`/`JIRA_PASSWORD`. Credentials are never printed by the CLI.

GitHub authentication uses the installed GitHub CLI:

```bash
gh auth login
gh auth status
```

## Privacy

Generated raw data and processed reports can contain confidential Jira/GitHub content. Read [PRIVACY.md](PRIVACY.md) before using this with real organisation data.

## License

This project is licensed under the [Apache License 2.0](LICENSE).

## Version

This repository is at `1.0.3`, with clearer touched-work reporting and Dependabot exclusion from missing Jira-link counts. See [CHANGELOG.md](CHANGELOG.md) for release history.

## Discovery Workflow

Test Jira access:

```bash
uv run delivery jira test
```

List Jira projects:

```bash
uv run delivery jira projects
uv run delivery jira projects --contains platform
```

Inspect an unfamiliar Jira project:

```bash
uv run delivery jira inspect PAY
uv run delivery jira statuses PAY
```

List repositories in a GitHub organization:

```bash
uv run delivery github repos MY-ORG
uv run delivery github repos MY-ORG --contains payments
uv run delivery github repos MY-ORG --sort name
uv run delivery github repos MY-ORG --include-archived
```

Infer likely repositories from Jira issue keys:

```bash
uv run delivery infer-repos \
  --jira-project PAY \
  --days 180 \
  --format json
```

This starts from recent Jira issues, reads Jira remote links and development-panel PR links, and prints candidate repositories plus a ready-to-run `delivery analyse` command. It does not infer ownership from developers or timestamps.

If Jira has no linked PR evidence, pass `--org my-org` to allow a bounded GitHub issue-search fallback using the sampled Jira keys.

Optional mascot:

```bash
uv run delivery raptor
```

Inspect local generated data before cleanup:

```bash
uv run delivery data cleanup
```

Delete the listed generated data files:

```bash
uv run delivery data cleanup --yes
```

## Analyse

Single Jira project:

```bash
uv run delivery analyse \
  --jira-project PAY \
  --repo my-org/payments-api \
  --repo my-org/payments-web \
  --days 180
```

Multiple Jira projects:

```bash
uv run delivery analyse \
  --jira-project PAY \
  --jira-project LEDGER \
  --repo my-org/payments-api \
  --repo my-org/ledger \
  --days 180
```

Use `--refresh` to fetch fresh raw data instead of using local cache.

Raw Jira responses are cached under `data/raw/jira/`. Raw GitHub PR JSON is cached under `data/raw/github/`.

Jira project versions are also cached under `data/raw/jira/` so reports can show releases whose Jira `releaseDate` falls inside the analysis period.

If a larger matching cache already exists, the CLI reuses it for smaller analyses. For example, a prior 180-day pull can support a 14-day comparison without another Jira or GitHub fetch.

Compare the current period with the immediately preceding period:

```bash
uv run delivery compare \
  --jira-project PAY \
  --repo my-org/payments-api \
  --days 7 \
  --compare 7 \
  --format json
```

This fetches or reuses one combined 14-day dataset, then splits it locally into:

- current period: last 7 days;
- previous period: the 7 days before that.

The normal `analyse` report also includes a weekly breakdown across the selected period, so a 180-day analysis can show week-by-week movement without running explicit comparisons.

Use `--format json` on `analyse`, `compare`, and `infer-repos` when another tool or AI agent will consume the output. Progress messages are written to stderr, so stdout remains parseable JSON.

Report-producing commands also write a processed copy under `data/processed/`. Text reports include a metadata header with the command used and UTC run time. JSON reports include the same information under the top-level `run` key.

Long-running commands print progress to stderr, for example:

```text
[delivery] Fetching Jira issues for PAY over 180 days
[delivery] Jira issues fetched: 100/842
[delivery] Processing GitHub repo 1/2: my-org/payments-api
[delivery] GitHub PR details fetched for my-org/payments-api: 25/514
[delivery] Reconstructing delivery timelines and calculating metrics
[delivery] Wrote processed report: data/processed/20260907T143000Z_analyse-pay-my-org-payments-api-180.txt
```

Agent instructions are available in `skills/delivery-archaeology/SKILL.md`.

## What It Collects

Jira issue fields include issue identity, project, issue type, summary, status, created/updated/resolved dates, priority, parent, epic-like fields where discoverable, assignee, labels, components, fix versions, and common custom numeric/text fields such as story points, sprint, and team/workstream where visible.

Jira changelog entries preserve issue key, timestamp, field, from value, and to value for status, assignee, sprint, story points, priority, and fix version changes.

Jira project versions are collected to identify releases in the selected timeframe.

GitHub PR collection uses `gh pr list` and collects PR metadata, review data, commits, size, branches, labels, and merge/close timestamps.

## How Reports Are Calculated

Reports are calculated from read-only Jira and GitHub evidence inside the selected period. The analysis window is inclusive of the start date and exclusive of the end date.

Jira issues are counted as touched when their Jira `updated` date or `resolved` date falls inside the selected period. Completed issues are the subset that resolved inside the period, plus issues with no resolution date that are currently in `Done`, `Closed`, or `Released` and were updated inside the period.

Workflow timing is reconstructed from Jira status changelog entries. Each Jira status is mapped to a common state using `config/status_mapping.yaml`. Unknown statuses are reported and are not included in state-duration calculations until they are mapped.

Cycle time is the elapsed time spent outside backlog and done states. Median, P75, and P95 values are percentiles across completed issues with enough status history to calculate a cycle time. Throughput per week is completed issues divided by the number of weeks in the selected period.

Time by state is calculated from the reconstructed status timeline. Blocked time is time spent in statuses mapped to `blocked`. Waiting time is time spent in `ready`, `review`, `qa`, `release`, or `blocked`. Flow efficiency is the median share of cycle time not spent in those waiting states.

Rework loops count when an issue returns to a workflow state it has already visited, excluding backlog and done. Handoffs count transitions between different mapped workflow states.

Work mix is based on touched Jira issues. Bug counts include Jira issue types containing `bug` or `defect`, case-insensitively. Fix-version evidence is counted from completed issues only.

Releases are Jira project versions whose `releaseDate` falls inside the selected period. The report separates versions already marked as released from planned or unreleased versions.

GitHub PR metrics are calculated from PRs touched in the selected period. A PR is included when it was merged or closed in the period, or when it is still open and was updated in the period. PR lifetime is creation to merge or close. First review time is creation to the first review event. Approval-to-merge time is first approval to merge.

Jira/GitHub linking is based on Jira issue keys found in PR titles, branch names, PR bodies, or commit messages. Link coverage is the share of completed Jira issues that have at least one linked PR in the period. `PRs without Jira links` counts PRs in the period with no detectable Jira key, excluding Dependabot PRs.

Review candidates are deterministic examples worth inspecting. Jira candidates are selected from completed issues with long cycle time, blocked time, or workflow loops. PR candidates are selected from long-lived PRs, slow first reviews, or high review back-and-forth.

Comparison reports fetch or reuse one combined dataset, then split it into previous and current periods locally. Weekly breakdowns use the same calculation rules per week.

These metrics are evidence summaries, not performance scores. They depend on Jira resolution dates, status history, workflow mapping, issue-key usage in PRs, and the repositories included in the command.

## Status Mapping

Teams use different Jira workflows. Configure `config/status_mapping.yaml` to map local statuses into common workflow states:

```yaml
development:
  - In Progress
  - Development
review:
  - Code Review
  - Peer Review
```

Unknown Jira statuses are reported and are not silently classified.

## Evidence Produced

The initial `analyse` command reports:

- issue counts and completed work;
- issue type mix, bug counts, and releases in the selected timeframe;
- cycle-time median, P75, and P95;
- throughput per week;
- time by Jira workflow state;
- blocked time and blocked-work impact;
- waiting time, handoffs, and rework loops;
- PR count, PR lifetime, time to first review, approval-to-merge time, PR size, and review count;
- Jira/PR linking coverage;
- linked review candidates for long-running issues, blocked work, workflow loops, long-lived PRs, slow first review, and high review back-and-forth;
- deterministic evidence-backed findings with observation, evidence, sample size, and interpretation.

The analysis focuses on work, queues, flow, dependencies, rework, quality, variability, and delivery-system behavior.
