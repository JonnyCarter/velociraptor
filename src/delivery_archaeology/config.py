from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr


ROOT = Path.cwd()
DEFAULT_STATUS_MAPPING = ROOT / "config" / "status_mapping.yaml"
RAW_JIRA_DIR = ROOT / "data" / "raw" / "jira"
RAW_GITHUB_DIR = ROOT / "data" / "raw" / "github"


class JiraSettings(BaseModel):
    url: str = Field(alias="JIRA_URL")
    username: str | None = Field(default=None, alias="JIRA_USERNAME")
    password: SecretStr | None = Field(default=None, alias="JIRA_PASSWORD")
    token: SecretStr | None = Field(default=None, alias="JIRA_TOKEN")
    verify_ssl: bool = Field(default=True, alias="JIRA_VERIFY_SSL")

    @classmethod
    def from_env(cls) -> "JiraSettings":
        values: dict[str, Any] = {
            "JIRA_URL": os.getenv("JIRA_URL", "").rstrip("/"),
            "JIRA_USERNAME": os.getenv("JIRA_USERNAME") or None,
            "JIRA_PASSWORD": os.getenv("JIRA_PASSWORD") or None,
            "JIRA_TOKEN": os.getenv("JIRA_TOKEN") or None,
            "JIRA_VERIFY_SSL": _parse_bool(os.getenv("JIRA_VERIFY_SSL", "true")),
        }
        return cls.model_validate(values)


def _parse_bool(value: str) -> bool:
    return value.strip().lower() not in {"0", "false", "no", "off"}


class StatusMapping(BaseModel):
    states: dict[str, list[str]]

    @classmethod
    def load(cls, path: Path = DEFAULT_STATUS_MAPPING) -> "StatusMapping":
        data = yaml.safe_load(path.read_text()) or {}
        return cls(states={str(k): [str(v) for v in values] for k, values in data.items()})

    @property
    def reverse(self) -> dict[str, str]:
        return {
            status.casefold(): state
            for state, statuses in self.states.items()
            for status in statuses
        }

    def classify(self, status: str) -> str | None:
        return self.reverse.get(status.casefold())

    def unknown_statuses(self, statuses: set[str]) -> set[str]:
        return {status for status in statuses if self.classify(status) is None}
