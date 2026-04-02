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
        helpers_module.__path__ = []
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

    class _DummyAreaRegistry:
        def __init__(self):
            self.areas = {}

        def async_list_areas(self):
            return list(self.areas.values())

        def async_create(self, name):
            area = types.SimpleNamespace(id=name.lower().replace(" ", "_"), name=name)
            self.areas[area.id] = area
            return area

    class _DummyDeviceRegistry:
        def __init__(self):
            self.devices = {}

        def async_update_device(self, *_args, **_kwargs):
            return None

        def async_remove_device(self, *_args, **_kwargs):
            return None

    class _DummyEntityRegistry:
        def __init__(self):
            self.entities = {}

        def async_remove(self, *_args, **_kwargs):
            return None

    class _DummyLabelRegistry:
        def __init__(self):
            self.labels = {}

    area_module = sys.modules.get("homeassistant.helpers.area_registry")
    if area_module is None:
        area_module = types.ModuleType("homeassistant.helpers.area_registry")
        area_module.async_get = lambda _hass=None: _DummyAreaRegistry()
        sys.modules["homeassistant.helpers.area_registry"] = area_module

    device_module = sys.modules.get("homeassistant.helpers.device_registry")
    if device_module is None:
        device_module = types.ModuleType("homeassistant.helpers.device_registry")
        device_module.async_get = lambda _hass=None: _DummyDeviceRegistry()
        device_module.async_entries_for_config_entry = lambda *_args, **_kwargs: []
        sys.modules["homeassistant.helpers.device_registry"] = device_module

    entity_module = sys.modules.get("homeassistant.helpers.entity_registry")
    if entity_module is None:
        entity_module = types.ModuleType("homeassistant.helpers.entity_registry")
        entity_module.async_get = lambda _hass=None: _DummyEntityRegistry()
        entity_module.async_entries_for_device = lambda *_args, **_kwargs: []
        sys.modules["homeassistant.helpers.entity_registry"] = entity_module

    label_module = sys.modules.get("homeassistant.helpers.label_registry")
    if label_module is None:
        label_module = types.ModuleType("homeassistant.helpers.label_registry")
        label_module.async_get = lambda _hass=None: _DummyLabelRegistry()
        sys.modules["homeassistant.helpers.label_registry"] = label_module


def _bootstrap_pymodbus_stubs():
    """Provide minimal pymodbus client stubs for logic tests."""
    pymodbus_module = sys.modules.get("pymodbus")
    if pymodbus_module is None:
        pymodbus_module = types.ModuleType("pymodbus")
        sys.modules["pymodbus"] = pymodbus_module

    client_module = sys.modules.get("pymodbus.client")
    if client_module is None:
        client_module = types.ModuleType("pymodbus.client")

        class _DummyResult:
            def __init__(self):
                self.registers = [0]
                self.bits = [False]

            def isError(self):
                return False

        class _BaseClient:
            def __init__(self, *args, **kwargs):
                self._open = False

            def connect(self):
                self._open = True
                return True

            def is_socket_open(self):
                return self._open

            def read_holding_registers(self, *args, **kwargs):
                return _DummyResult()

            def read_input_registers(self, *args, **kwargs):
                return _DummyResult()

            def read_coils(self, *args, **kwargs):
                return _DummyResult()

            def write_register(self, *args, **kwargs):
                return _DummyResult()

            def write_coil(self, *args, **kwargs):
                return _DummyResult()

        class ModbusSerialClient(_BaseClient):
            pass

        class ModbusTcpClient(_BaseClient):
            pass

        client_module.ModbusSerialClient = ModbusSerialClient
        client_module.ModbusTcpClient = ModbusTcpClient
        sys.modules["pymodbus.client"] = client_module


_bootstrap_opengrowbox_namespace()
_bootstrap_homeassistant_stubs()
_bootstrap_pymodbus_stubs()
