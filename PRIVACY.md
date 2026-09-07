# Privacy

Delivery Archaeology is intended to be a local, read-only analysis tool for Jira and GitHub delivery-system evidence.

The source code does not contain organisation-specific delivery logic. It should be usable across organisations, provided each organisation is comfortable with the local data it extracts and stores.

## Data Sources

The tool reads from:

- Jira REST APIs, using `JIRA_URL` and either a token or username/password from the local environment.
- GitHub via the authenticated `gh` CLI.

It does not write to Jira or GitHub.

## Local Data Stored

Generated data can contain confidential operational information.

Raw Jira responses are cached under `data/raw/jira/` and may include:

- issue keys, summaries, statuses, priorities, labels, components, fix versions, sprint/team fields, and assignees;
- changelog history for status, assignee, sprint, story points, priority, and fix version changes;
- project versions and release dates;
- custom fields returned by the Jira API.

Raw GitHub responses are cached under `data/raw/github/` and may include:

- PR titles, bodies, branch names, labels, authors, timestamps, and review metadata;
- commit metadata/messages returned by `gh`.

Processed reports are written under `data/processed/` and may include:

- issue keys and PR URLs worth reviewing;
- issue summaries and repository names;
- delivery-flow metrics, findings, and data-quality observations;
- the command used to generate the report and the UTC run time.

## Credentials

The CLI must never print Jira passwords, Jira tokens, or GitHub credentials.

`.env` is ignored by git. Do not commit `.env` files, local shell history containing credentials, raw API caches, or processed reports.

## Sharing Guidance

Treat `data/raw/`, `data/processed/`, screenshots, pasted report output, and exported JSON as internal operational data.

Before sharing outside the relevant organisation or team, review for:

- confidential project names or roadmap details;
- sensitive issue summaries or PR titles;
- links to private Jira issues or GitHub pull requests;
- names or usernames in assignee, author, or review metadata;
- security, incident, vulnerability, or customer references.

## Analysis Boundaries

The tool is designed to analyse work and delivery-system behaviour. It must not be used to produce:

- developer rankings;
- individual productivity scores;
- commit leaderboards;
- LOC productivity;
- story-point comparisons between teams.

## Local Cleanup

Use the dry-run cleanup command to inspect generated data:

```bash
uv run delivery data cleanup
```

Delete the listed generated data files only when intended:

```bash
uv run delivery data cleanup --yes
```

Limit cleanup to older files when needed:

```bash
uv run delivery data cleanup --older-than-days 30 --yes
```
