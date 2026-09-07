from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator


ROOT = Path.cwd()
DEFAULT_STATUS_MAPPING = ROOT / "config" / "status_mapping.yaml"
RAW_JIRA_DIR = ROOT / "data" / "raw" / "jira"
RAW_GITHUB_DIR = ROOT / "data" / "raw" / "github"
PROCESSED_DIR = ROOT / "data" / "processed"


class JiraSettings(BaseModel):
    url: str = Field(alias="JIRA_URL")
    username: str | None = Field(default=None, alias="JIRA_USERNAME")
    password: SecretStr | None = Field(default=None, alias="JIRA_PASSWORD")
    token: SecretStr | None = Field(default=None, alias="JIRA_TOKEN")
    verify_ssl: bool = Field(default=True, alias="JIRA_VERIFY_SSL")

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError("JIRA_URL is required and must include http:// or https://")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("JIRA_URL must include http:// or https://")
        return normalized

    @classmethod
    def from_env(cls) -> "JiraSettings":
        env_file_values = load_env_file()
        values: dict[str, Any] = {
            "JIRA_URL": _env_value("JIRA_URL", env_file_values, ""),
            "JIRA_USERNAME": _env_value("JIRA_USERNAME", env_file_values) or None,
            "JIRA_PASSWORD": _env_value("JIRA_PASSWORD", env_file_values) or None,
            "JIRA_TOKEN": _env_value("JIRA_TOKEN", env_file_values) or None,
            "JIRA_VERIFY_SSL": _parse_bool(_env_value("JIRA_VERIFY_SSL", env_file_values, "true")),
        }
        return cls.model_validate(values)


def load_env_file(path: Path | None = None) -> dict[str, str]:
    env_path = path or Path.cwd() / ".env"
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = _strip_env_value(value.strip())
    return values


def _env_value(key: str, env_file_values: dict[str, str], default: str = "") -> str:
    return os.getenv(key) or env_file_values.get(key, default)


def _strip_env_value(value: str) -> str:
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if " #" in value:
        return value.split(" #", 1)[0].rstrip()
    return value


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
