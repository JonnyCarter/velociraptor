---
name: delivery-archaeology
description: Use the local delivery CLI to analyse Jira and GitHub delivery flow, generate JSON evidence for agents, and avoid individual productivity analysis.
---

# Delivery Archaeology

Use this skill when a task asks an agent to analyse software delivery flow with the `delivery` CLI in this repository.

## Core Workflow

Prefer machine-readable output for agent workflows:

```bash
uv run delivery analyse \
  --jira-project PAY \
  --repo my-org/payments-api \
  --days 180 \
  --format json
```

Use repository inference before analysis when repos are unknown:

```bash
uv run delivery infer-repos \
  --jira-project PAY \
  --days 180 \
  --format json
```

If Jira has no linked PR evidence, add `--org my-org` to allow the bounded GitHub search fallback.

Use comparison mode to inspect recent movement:

```bash
uv run delivery compare \
  --jira-project PAY \
  --repo my-org/payments-api \
  --days 7 \
  --compare 7 \
  --format json
```

## Interpretation Rules

Treat output as delivery-system evidence, not a performance ranking. Focus on:

- work queues;
- blocked time;
- workflow loops and rework;
- cycle-time variability;
- bug counts, issue type mix, and release evidence;
- PR review flow;
- linking and Jira data quality.

Do not produce developer rankings, individual productivity scores, commit leaderboards, LOC productivity claims, or story-point comparisons between teams.

Call out weak data quality explicitly, especially unknown Jira statuses, missing resolution dates, low Jira/GitHub link coverage, and missing PR links.

## Operating Notes

The tool is read-only. It uses Jira REST APIs and GitHub CLI authentication through `gh`.

Raw data is cached under `data/raw/`. Use `--refresh` only when fresh source data is needed.

Progress messages are written to stderr. JSON reports are written to stdout and should remain parseable when stdout is captured separately.

Report-producing commands write a processed copy under `data/processed/`. Text copies include the command and UTC run time in a metadata header. JSON copies include the same values in the top-level `run` object.

Generated data can contain confidential Jira and GitHub content. Review `PRIVACY.md` before sharing output, and use `uv run delivery data cleanup` to inspect local generated files before deleting them with `--yes`.

For management summaries, distinguish evidence from interpretation. Prefer phrasing like "blocked issues had 3.1x higher median cycle time" over claims about team efficiency.
