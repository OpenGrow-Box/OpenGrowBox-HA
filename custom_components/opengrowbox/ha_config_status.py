"""Helpers for checking required Home Assistant logger configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import yaml as yaml_lib

try:
    from yaml.constructor import ConstructorError as YAMLConstructorError
except ImportError:
    YAMLConstructorError = Exception


REQUIRED_LOGGER_DEFAULT = "info"
REQUIRED_LOGGER_LEVEL = "debug"
REQUIRED_LOGGER_OVERRIDES = {
    "homeassistant.config_entries": REQUIRED_LOGGER_LEVEL,
    "homeassistant.setup": REQUIRED_LOGGER_LEVEL,
    "homeassistant.loader": REQUIRED_LOGGER_LEVEL,
    "custom_components.opengrowbox": REQUIRED_LOGGER_LEVEL,
    "custom_components.ogb-dev-env": REQUIRED_LOGGER_LEVEL,
}


class _HomeAssistantYamlLoader(yaml_lib.SafeLoader):
    """YAML loader that accepts Home Assistant-specific tags."""


def _construct_ha_tag(loader, _tag_suffix, node):
    """Construct unknown HA tags as their underlying YAML value."""
    if isinstance(node, yaml_lib.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml_lib.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml_lib.MappingNode):
        return loader.construct_mapping(node)
    return None


_HomeAssistantYamlLoader.add_multi_constructor("!", _construct_ha_tag)


@dataclass(frozen=True)
class HAConfigStatus:
    """Result of inspecting Home Assistant YAML configuration."""

    path: str
    missing: tuple[str, ...] = ()
    error: str | None = None

    @property
    def is_complete(self) -> bool:
        """Return true when all required settings are present."""
        return self.error is None and not self.missing


def load_configuration_yaml(path: str) -> dict[str, Any]:
    """Load Home Assistant configuration.yaml as dict."""
    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as file:
        content = file.read()

    return parse_configuration_yaml(content)


def parse_configuration_yaml(content: str) -> dict[str, Any]:
    """Parse Home Assistant YAML, tolerating common HA-specific include tags."""
    try:
        loaded = yaml_lib.load(content, Loader=_HomeAssistantYamlLoader)
    except YAMLConstructorError:
        return _parse_yaml_with_ha_includes(content)

    if isinstance(loaded, dict):
        return loaded
    return {}


def get_ha_config_status(path: str) -> HAConfigStatus:
    """Return missing required OpenGrowBox YAML settings."""
    if not os.path.exists(path):
        return HAConfigStatus(
            path=path,
            missing=_required_logger_settings(),
            error="configuration.yaml was not found",
        )

    try:
        with open(path, "r", encoding="utf-8") as file:
            content = file.read()
        config = parse_configuration_yaml(content)
    except Exception as err:
        return HAConfigStatus(path=path, error=str(err))

    missing = []

    logger_config = config.get("logger")
    logger_line = _find_top_level_logger_line(content)
    logger_line_is_inline = bool(logger_line and _line_has_inline_value(logger_line))

    if "logger" not in config:
        missing.extend(_required_logger_settings())
    elif not isinstance(logger_config, dict):
        if logger_line_is_inline:
            return HAConfigStatus(
                path=path,
                error=(
                    "logger is defined with an inline value or include and "
                    "cannot be verified or updated automatically: "
                    f"{logger_line.strip()}"
                ),
            )
        missing.append(f"logger.default: {REQUIRED_LOGGER_DEFAULT}")
        missing.extend(
            f"logger.logs.{name}: {level}"
            for name, level in REQUIRED_LOGGER_OVERRIDES.items()
        )
    else:
        logger_default = logger_config.get("default")
        if _as_level(logger_default) != REQUIRED_LOGGER_DEFAULT:
            missing.append(
                _format_expected_value(
                    "logger.default",
                    REQUIRED_LOGGER_DEFAULT,
                    logger_default,
                )
            )

        logs_block = logger_config.get("logs")
        if not isinstance(logs_block, dict):
            logs_line = _find_logger_child_line(content, "logs")
            if logs_line and _line_has_inline_value(logs_line):
                return HAConfigStatus(
                    path=path,
                    missing=tuple(missing),
                    error=(
                        "logger.logs is defined with an inline value or include "
                        "and cannot be verified or updated automatically: "
                        f"{logs_line.strip()}"
                    ),
                )
            missing.extend(
                f"logger.logs.{name}: {level}"
                for name, level in REQUIRED_LOGGER_OVERRIDES.items()
            )
        else:
            for name, level in REQUIRED_LOGGER_OVERRIDES.items():
                current = logs_block.get(name)
                if _as_level(current) != level:
                    missing.append(
                        _format_expected_value(
                            f"logger.logs.{name}",
                            level,
                            current,
                        )
                    )

    if missing and logger_line_is_inline:
        return HAConfigStatus(
            path=path,
            missing=tuple(missing),
            error=(
                "logger is defined with an inline mapping and cannot be "
                f"updated automatically: {logger_line.strip()}"
            ),
        )

    return HAConfigStatus(path=path, missing=tuple(missing))


def _required_logger_settings() -> tuple[str, ...]:
    """Return every required logger setting in user-facing form."""
    return (
        "logger:",
        f"logger.default: {REQUIRED_LOGGER_DEFAULT}",
        *(
            f"logger.logs.{name}: {level}"
            for name, level in REQUIRED_LOGGER_OVERRIDES.items()
        ),
    )


def format_ha_config_status_message(status: HAConfigStatus) -> str:
    """Return a short user-facing status message for config-flow forms."""
    if status.is_complete:
        return (
            "configuration.yaml already contains the required OpenGrowBox "
            "logger diagnostics."
        )

    if status.error:
        if not status.missing:
            return (
                f"Warning: OpenGrowBox could not verify {status.path}: "
                f"{status.error}. Update the logger configuration manually, "
                "or use a plain logger block in configuration.yaml before "
                "enabling automatic updates."
            )
        return (
            f"Warning: OpenGrowBox could not verify {status.path}: {status.error}. "
            "Required logger settings: "
            f"{', '.join(status.missing)}. Add them manually, or enable automatic "
            "updates below."
        )

    return (
        "Warning: configuration.yaml is missing: "
        f"{', '.join(status.missing)}. Add them manually, or enable automatic "
        "updates below so OpenGrowBox can create a backup and add the logger "
        "settings."
    )


def _parse_yaml_with_ha_includes(content: str) -> dict[str, Any]:
    """Extract relevant sections from YAML that contains HA-specific tags."""
    result = {}
    found_logger = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("logger:") and not found_logger:
            result["logger"] = _extract_logger_block(content)
            found_logger = True
    return result


def _extract_logger_block(content: str) -> dict[str, Any] | str:
    """Extract logger section from YAML content."""
    logger_block = {}
    in_logs = False
    in_logger = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("logger:"):
            inline = line.split(":", 1)[1].strip()
            if inline:
                return inline
            in_logger = True
            continue
        if in_logger and line and not line[0].isspace():
            break
        if in_logger and "logs:" in line and "logs" not in logger_block:
            in_logs = True
            continue
        if in_logger and in_logs and line and not line[0].isspace():
            in_logs = False
        if in_logger and in_logs and ":" in line:
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[0].strip():
                logger_block["logs"] = logger_block.get("logs", {})
                logger_block["logs"][parts[0].strip()] = parts[1].strip()
        elif in_logger and "default:" in line:
            parts = line.split(":", 1)
            if len(parts) == 2:
                logger_block["default"] = parts[1].strip()
    return logger_block


def _as_level(value: Any) -> str:
    """Normalize logger level value to lowercase string."""
    if value is None:
        return ""
    return str(value).strip().lower()


def _format_expected_value(key: str, expected: str, current: Any) -> str:
    """Format an exact missing or mismatched logger value."""
    if current is None:
        return f"{key}: {expected}"
    return f"{key}: {expected} (currently {current})"


def _find_top_level_logger_line(content: str) -> str | None:
    """Return the top-level logger declaration line if present."""
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line == line.lstrip() and stripped.startswith("logger:"):
            return line
    return None


def _find_logger_child_line(content: str, child_key: str) -> str | None:
    """Return a direct child line from the top-level logger block."""
    in_logger = False
    child_prefix = f"{child_key}:"
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line == line.lstrip():
            if stripped.startswith("logger:"):
                in_logger = True
                continue
            if in_logger:
                break
        if in_logger and stripped.startswith(child_prefix):
            return line
    return None


def _line_has_inline_value(line: str) -> bool:
    """Return true when a YAML key line has a value after the colon."""
    return bool(line.split(":", 1)[1].strip())
