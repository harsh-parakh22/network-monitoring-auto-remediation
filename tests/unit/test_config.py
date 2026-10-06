"""Unit tests for configuration loading and validation."""

from __future__ import annotations

import pytest

from netmon.config.errors import ConfigError
from netmon.config.models import AppConfig, MonitoringConfig, load_config

VALID_YAML = """
monitoring:
  interval_seconds: 5
  failure_threshold: 3
  recovery_threshold: 2

targets:
  - name: app-01
    host: app-01
    port: 8080
    checks: [icmp, tcp, http]
    http_path: /health

  - name: db-01
    host: db-01
    port: 5432
    checks: [icmp, tcp]
"""


def test_valid_config_loads(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(VALID_YAML, encoding="utf-8")
    cfg = load_config(str(p))
    assert len(cfg.targets) == 2
    assert cfg.targets[0].name == "app-01"
    assert cfg.targets[1].port == 5432
    assert cfg.monitoring.failure_threshold == 3


def test_missing_file_raises_config_error():
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nonexistent/config.yaml")


def test_invalid_yaml_raises_config_error(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("targets: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(str(p))


def test_non_mapping_root_raises(tmp_path):
    p = tmp_path / "list.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(str(p))


def test_empty_targets_rejected(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text("targets: []\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="validation failed"):
        load_config(str(p))


def test_unknown_fields_rejected(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text("targets:\n  - name: x\n    host: x\n    checks: [tcp]\n    bogus: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="validation failed"):
        load_config(str(p))


@pytest.mark.parametrize(
    "bad_name",
    ["", "has space", "-leading-dash", "x" * 64],
)
def test_target_name_pattern_enforced(bad_name):
    from netmon.config.models import ProbeType, TargetConfig

    with pytest.raises(Exception):
        TargetConfig(name=bad_name, host="h", checks=[ProbeType.TCP])


def test_degraded_threshold_must_be_below_failure_threshold():
    with pytest.raises(Exception):
        MonitoringConfig(degraded_threshold=3, failure_threshold=3)


def test_http_path_must_start_with_slash():
    from netmon.config.models import ProbeType, TargetConfig

    with pytest.raises(Exception):
        TargetConfig(name="a", host="a", checks=[ProbeType.HTTP], http_path="health")


def test_defaults_are_sensible():
    cfg = AppConfig(targets=[{"name": "a", "host": "a", "checks": ["tcp"]}])
    m = cfg.monitoring
    assert m.failure_threshold == 3
    assert m.recovery_threshold == 2
    t = cfg.targets[0]
    assert t.interval_seconds == 10.0
    assert t.timeout_seconds == 3.0
    assert t.http_path == "/health"
