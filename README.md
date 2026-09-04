# Delivery Archaeology

Small read-only command-line tool for analysing software delivery using Jira Server/Data Center and GitHub evidence.

It answers one practical question:

> Where did delivery time go, and what evidence points to the highest-leverage constraints in this team's delivery system?

No dashboards, notebooks, developer rankings, productivity scores, commit leaderboards, or LOC productivity analysis are included.

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

## What It Collects

Jira issue fields include issue identity, project, issue type, summary, status, created/updated/resolved dates, priority, parent, epic-like fields where discoverable, assignee, labels, components, fix versions, and common custom numeric/text fields such as story points, sprint, and team/workstream where visible.

Jira changelog entries preserve issue key, timestamp, field, from value, and to value for status, assignee, sprint, story points, priority, and fix version changes.

GitHub PR collection uses `gh pr list` and collects PR metadata, review data, commits, size, branches, labels, and merge/close timestamps.

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
- cycle-time median, P75, and P95;
- throughput per week;
- time by Jira workflow state;
- blocked time and blocked-work impact;
- waiting time, handoffs, and rework loops;
- PR count, PR lifetime, time to first review, approval-to-merge time, PR size, and review count;
- Jira/PR linking coverage;
- deterministic evidence-backed findings with observation, evidence, sample size, and interpretation.

The analysis focuses on work, queues, flow, dependencies, rework, quality, variability, and delivery-system behavior.
