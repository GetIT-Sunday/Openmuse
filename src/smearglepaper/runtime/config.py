from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

tomllib: Any
try:
    tomllib = importlib.import_module("tomllib")
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    tomllib = None


DEFAULT_WORKSPACE = Path.home() / ".local" / "share" / "smearglepaper"
DEFAULT_CONFIG = Path.home() / ".config" / "smearglepaper" / "config.toml"


def load_runtime_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or DEFAULT_CONFIG
    if not config_path.exists() or tomllib is None:
        return {}
    with config_path.open("rb") as handle:
        value = tomllib.load(handle)
    return dict(value)


def resolve_workspace(explicit: Path | str | None = None, config: dict[str, Any] | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_workspace = os.getenv("SMEARGLEPAPER_HOME", "").strip()
    if env_workspace:
        return Path(env_workspace).expanduser().resolve()
    settings = config if config is not None else load_runtime_config()
    configured = settings.get("workspace")
    if isinstance(configured, str) and configured.strip():
        return Path(configured).expanduser().resolve()
    home = os.getenv("OPENMUSE_HOME", "").strip()
    if home:
        return Path(home).expanduser().resolve() / "runtime"
    return DEFAULT_WORKSPACE
