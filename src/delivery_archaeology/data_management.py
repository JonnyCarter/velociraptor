from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from delivery_archaeology.config import PROCESSED_DIR, RAW_GITHUB_DIR, RAW_JIRA_DIR, ROOT


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    size_bytes: int
    modified_at: datetime


def cleanup_candidates(*, older_than_days: int | None = None, include_raw: bool = True, include_processed: bool = True) -> list[CleanupCandidate]:
    cutoff = None
    if older_than_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    candidates: list[CleanupCandidate] = []
    roots: list[Path] = []
    if include_raw:
        roots.extend([RAW_JIRA_DIR, RAW_GITHUB_DIR])
    if include_processed:
        roots.append(PROCESSED_DIR)
    for root in roots:
        candidates.extend(_candidates_under(root, cutoff))
    return sorted(candidates, key=lambda candidate: str(candidate.path))


def delete_candidates(candidates: list[CleanupCandidate]) -> None:
    for candidate in candidates:
        candidate.path.unlink(missing_ok=True)


def cleanup_summary(candidates: list[CleanupCandidate]) -> dict[str, int]:
    return {
        "files": len(candidates),
        "bytes": sum(candidate.size_bytes for candidate in candidates),
    }


def relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _candidates_under(root: Path, cutoff: datetime | None) -> list[CleanupCandidate]:
    if not root.exists():
        return []
    candidates: list[CleanupCandidate] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name == ".gitkeep":
            continue
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if cutoff and modified_at > cutoff:
            continue
        candidates.append(CleanupCandidate(path=path, size_bytes=path.stat().st_size, modified_at=modified_at))
    return candidates
