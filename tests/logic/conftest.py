from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from pathlib import Path


def _bootstrap_opengrowbox_namespace():
    """Allow importing OGB submodules without executing integration __init__.py."""
    repo_root = Path(__file__).resolve().parents[2]
    custom_components_dir = repo_root / "custom_components"
    ogb_dir = custom_components_dir / "opengrowbox"

    cc_module = sys.modules.get("custom_components")
    if cc_module is None:
        cc_module = types.ModuleType("custom_components")
        cc_module.__path__ = [str(custom_components_dir)]
        sys.modules["custom_components"] = cc_module

    ogb_module = sys.modules.get("custom_components.opengrowbox")
    if ogb_module is None:
        ogb_module = types.ModuleType("custom_components.opengrowbox")
        ogb_module.__path__ = [str(ogb_dir)]
        sys.modules["custom_components.opengrowbox"] = ogb_module


def _bootstrap_homeassistant_stubs():
    """Provide minimal HA modules for pure logic tests in CI."""
    ha_module = sys.modules.get("homeassistant")
    if ha_module is None:
        ha_module = types.ModuleType("homeassistant")
        sys.modules["homeassistant"] = ha_module

    util_module = sys.modules.get("homeassistant.util")
    if util_module is None:
        util_module = types.ModuleType("homeassistant.util")
        sys.modules["homeassistant.util"] = util_module

    dt_module = sys.modules.get("homeassistant.util.dt")
    if dt_module is None:
        dt_module = types.ModuleType("homeassistant.util.dt")

        def _now():
            return datetime.now(timezone.utc)

        def _as_local(value):
            return value

        dt_module.now = _now
        dt_module.utcnow = _now
        dt_module.as_local = _as_local
        sys.modules["homeassistant.util.dt"] = dt_module

    helpers_module = sys.modules.get("homeassistant.helpers")
    if helpers_module is None:
        helpers_module = types.ModuleType("homeassistant.helpers")
        sys.modules["homeassistant.helpers"] = helpers_module

    event_module = sys.modules.get("homeassistant.helpers.event")
    if event_module is None:
        event_module = types.ModuleType("homeassistant.helpers.event")

        def _track_point_in_time(*_args, **_kwargs):
            return lambda: None

        def _track_time_interval(*_args, **_kwargs):
            return lambda: None

        event_module.async_track_point_in_time = _track_point_in_time
        event_module.async_track_time_interval = _track_time_interval
        sys.modules["homeassistant.helpers.event"] = event_module


_bootstrap_opengrowbox_namespace()
_bootstrap_homeassistant_stubs()
