"""Configuration models for the monitoring system.

Everything that can vary between environments lives here. The models are
pydantic so that a malformed config file fails fast at startup with a
precise error, instead of surfacing as a runtime TypeError three hours
into operation.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProbeType(StrEnum):
    ICMP = "icmp"
    TCP = "tcp"
    HTTP = "http"


class TargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    host: str = Field(min_length=1, max_length=253)
    checks: list[ProbeType] = Field(min_length=1)
    port: int = Field(default=8080, ge=1, le=65535)
    http_path: str = "/health"
    http_expected_status: int = Field(default=200, ge=100, le=599)
    http_expected_body: str | None = None
    interval_seconds: float = Field(default=10.0, gt=0)
    timeout_seconds: float = Field(default=3.0, gt=0)
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("http_path")
    @classmethod
    def path_must_start_with_slash(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("http_path must start with '/'")
        return v


class MonitoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interval_seconds: float = Field(default=10.0, gt=0)
    failure_threshold: int = Field(default=3, ge=1)
    degraded_threshold: int = Field(default=1, ge=1)
    recovery_threshold: int = Field(default=2, ge=1)
    packet_loss_window: int = Field(default=20, ge=1)
    max_concurrent_probes: int = Field(default=64, ge=1)

    @field_validator("degraded_threshold")
    @classmethod
    def degraded_lt_failure(cls, v: int, info: object) -> int:
        failure = getattr(info, "data", {}).get("failure_threshold")
        if failure is not None and v >= failure:
            raise ValueError("degraded_threshold must be < failure_threshold")
        return v


class RemediationPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    max_attempts: int = Field(default=3, ge=1)
    cooldown_seconds: float = Field(default=60.0, ge=0)
    attempts_window_seconds: float = Field(default=900.0, gt=0)
    verify_interval_seconds: float = Field(default=5.0, gt=0)
    verify_timeout_seconds: float = Field(default=120.0, gt=0)


class RemediationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    policies: dict[str, RemediationPolicyConfig] = Field(default_factory=dict)


class AppConfig(BaseModel):
    """Root configuration object."""

    model_config = ConfigDict(extra="forbid")

    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    targets: list[TargetConfig] = Field(min_length=1)
    remediation: RemediationConfig = Field(default_factory=RemediationConfig)


def load_config(path: str) -> AppConfig:
    """Load and validate a YAML config file. Raises ConfigError with a
    human-readable message on any validation failure."""
    import yaml

    from netmon.config.errors import ConfigError

    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {path}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be a mapping, got {type(raw).__name__}")

    try:
        return AppConfig.model_validate(raw)
    except Exception as e:  # pydantic.ValidationError re-wrapped for callers
        raise ConfigError(f"config validation failed for {path}:\n{e}") from e
