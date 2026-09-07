from __future__ import annotations

import re
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from delivery_archaeology.config import PROCESSED_DIR


Progress = Callable[[str], None] | None


def current_command(argv: list[str] | None = None) -> str:
    parts = list(argv if argv is not None else sys.argv)
    if parts:
        parts[0] = Path(parts[0]).name
    return " ".join(shlex.quote(part) for part in parts)


def run_metadata(command: str, run_at: datetime) -> dict[str, str]:
    return {
        "command": command,
        "run_at": run_at.astimezone(UTC).isoformat(),
    }


def payload_with_run_metadata(payload: dict[str, Any], *, command: str, run_at: datetime) -> dict[str, Any]:
    return {
        **payload,
        "run": run_metadata(command, run_at),
    }


def text_with_run_metadata(report: str, *, command: str, run_at: datetime) -> str:
    return "\n".join([
        "Processed report",
        "----------------",
        f"Command: {command}",
        f"Run at:  {run_at.astimezone(UTC).isoformat()}",
        "",
        report.rstrip(),
        "",
    ])


def write_processed_report(
    *,
    report_name: str,
    content: str,
    output_format: str,
    command: str,
    run_at: datetime,
    progress: Progress = None,
) -> Path:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = processed_report_path(report_name=report_name, output_format=output_format, command=command, run_at=run_at)
    path.write_text(content)
    if progress:
        progress(f"Wrote processed report: {path}")
    return path


def processed_report_path(*, report_name: str, output_format: str, command: str, run_at: datetime) -> Path:
    timestamp = run_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    scope = _command_scope(command)
    slug_source = scope if scope.startswith(report_name) else f"{report_name} {scope}"
    slug = _slugify(slug_source)
    extension = "json" if output_format == "json" else "txt"
    return PROCESSED_DIR / f"{timestamp}_{slug}.{extension}"


def _command_scope(command: str) -> str:
    parts = shlex.split(command)
    scope_parts: list[str] = []
    for index, part in enumerate(parts):
        if part in {"analyse", "compare", "infer-repos", "issue", "flow"}:
            scope_parts.append(part)
        elif part in {"--jira-project", "--project", "--repo", "--days", "--compare", "--org"} and index + 1 < len(parts):
            scope_parts.append(parts[index + 1])
    return " ".join(scope_parts[:12])


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug[:120] or "delivery-report"
