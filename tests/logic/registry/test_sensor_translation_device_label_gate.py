"""Tests for the Sensor-device label gate in the RegistryListener filter."""

import sys
import types

import pytest
from unittest.mock import Mock

core_module = sys.modules.get("homeassistant.core")
if core_module is None:
    core_module = types.ModuleType("homeassistant.core")
    core_module.callback = lambda fn: fn
    sys.modules["homeassistant.core"] = core_module

from custom_components.opengrowbox.OGBController.RegistryListener import (
    OGBRegistryEvenListener,
)
import custom_components.opengrowbox.OGBController.utils.sensor_identification as sid


def _label(name):
    entry = Mock()
    entry.name = name
    return entry


def _registry(entries):
    registry = Mock()
    registry.labels = entries
    return registry


def _listener(monkeypatch):
    monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
    return OGBRegistryEvenListener(None, None, None, "dev_room")


def _entity(entity_id, device_id=None, labels=None):
    entity = Mock()
    entity.entity_id = entity_id
    entity.device_id = device_id
    entity.labels = labels or []
    entity.disabled = False
    return entity


def test_sensor_label_on_device_passes_filter(monkeypatch):
    listener = _listener(monkeypatch)
    label_registry = _registry({"sensor": _label("Sensor")})
    device = Mock()
    device.labels = ["sensor"]
    devices_in_room = {"device.devsensor1": device}

    assert listener._device_labels_for_entity(
        _entity("sensor.devsensor1_temperature", "device.devsensor1"),
        label_registry,
        devices_in_room,
    ) == [{"id": "sensor", "name": "Sensor"}]

    assert listener._matches_sensor_translations(
        _entity("sensor.devsensor1_temperature", "device.devsensor1"),
        label_registry,
        [{"id": "sensor", "name": "Sensor"}],
    ) is True


def test_non_sensor_label_fails_filter(monkeypatch):
    listener = _listener(monkeypatch)
    label_registry = _registry({"garage": _label("Garage")})
    device = Mock()
    device.labels = ["garage"]
    devices_in_room = {"device.garage": device}

    device_labels = listener._device_labels_for_entity(
        _entity("sensor.garage_temperature", "device.garage"),
        label_registry,
        devices_in_room,
    )
    assert device_labels == [{"id": "garage", "name": "Garage"}]

    assert listener._matches_sensor_translations(
        _entity("sensor.garage_temperature", "device.garage"),
        label_registry,
        device_labels,
    ) is False


def test_no_device_labels_fails_filter_in_label_only(monkeypatch):
    listener = _listener(monkeypatch)
    label_registry = _registry({})
    devices_in_room = {}

    assert listener._matches_sensor_translations(
        _entity("sensor.devsensor1_temperature", "device.devsensor1"),
        label_registry,
        [],
    ) is False


def test_non_sensor_entities_ignored(monkeypatch):
    listener = _listener(monkeypatch)
    label_registry = _registry({"sensor": _label("Sensor")})
    assert listener._matches_sensor_translations(
        _entity("switch.devsensor1_power", "device.devsensor1"),
        label_registry,
        [{"id": "sensor", "name": "Sensor"}],
    ) is False


def test_warns_once_for_device_without_label_in_label_only(monkeypatch, caplog):
    listener = _listener(monkeypatch)
    label_registry = _registry({})
    device = Mock()
    device.labels = []
    devices_in_room = {"device.devsensor2": device, "device.devsensor3": device}

    import logging as logging_module
    with caplog.at_level(logging_module.WARNING, logger="custom_components.opengrowbox.OGBController.RegistryListener"):
        listener._warn_device_found_without_label(
            _entity("sensor.devsensor2_temperature", "device.devsensor2"),
            [],
            devices_in_room,
        )
        listener._warn_device_found_without_label(
            _entity("sensor.devsensor2_humidity", "device.devsensor2"),
            [],
            devices_in_room,
        )
        listener._warn_device_found_without_label(
            _entity("sensor.devsensor3_temperature", "device.devsensor3"),
            [],
            devices_in_room,
        )

    messages = [rec.message for rec in caplog.records]
    assert sum("DEVICE FOUND WITHOUT LABEL" in msg for msg in messages) == 2
    assert any("sensor.devsensor2_temperature" in msg for msg in messages)
    assert any("sensor.devsensor3_temperature" in msg for msg in messages)


def test_no_warning_when_device_has_labels(monkeypatch, caplog):
    listener = _listener(monkeypatch)
    label_registry = _registry({"sensor": _label("Sensor")})
    device = Mock()
    device.labels = ["sensor"]
    devices_in_room = {"device.devsensor1": device}

    import logging as logging_module
    with caplog.at_level(logging_module.WARNING, logger="custom_components.opengrowbox.OGBController.RegistryListener"):
        listener._warn_device_found_without_label(
            _entity("sensor.devsensor1_temperature", "device.devsensor1"),
            [{"id": "sensor", "name": "Sensor"}],
            devices_in_room,
        )

    messages = [rec.message for rec in caplog.records]
    assert not any("DEVICE FOUND WITHOUT LABEL" in msg for msg in messages)


def test_no_warning_when_not_in_room(monkeypatch, caplog):
    listener = _listener(monkeypatch)
    label_registry = _registry({})

    import logging as logging_module
    with caplog.at_level(logging_module.WARNING, logger="custom_components.opengrowbox.OGBController.RegistryListener"):
        listener._warn_device_found_without_label(
            _entity("sensor.other_room_temperature", "device.other"),
            [],
            {"device.devsensor2": Mock()},
        )

    messages = [rec.message for rec in caplog.records]
    assert not any("DEVICE FOUND WITHOUT LABEL" in msg for msg in messages)


def test_no_warning_for_ogb_internal_entities(monkeypatch, caplog):
    listener = _listener(monkeypatch)
    label_registry = _registry({})
    device = Mock()
    device.labels = []
    devices_in_room = {"device.ogb_int": device}

    import logging as logging_module
    with caplog.at_level(logging_module.WARNING, logger="custom_components.opengrowbox.OGBController.RegistryListener"):
        listener._warn_device_found_without_label(
            _entity("sensor.ogb_currentvpd_ambient", "device.ogb_int"),
            [],
            devices_in_room,
        )
        update_entity = _entity("update.opengrowbox_update_2", "device.ogb_update")
        update_entity.platform = "opengrowbox"
        listener._warn_device_found_without_label(
            update_entity,
            [],
            {"device.ogb_update": Mock()},
        )

    messages = [rec.message for rec in caplog.records]
    assert not any("DEVICE FOUND WITHOUT LABEL" in msg for msg in messages)


def test_warn_for_unknown_platform_entity(monkeypatch, caplog):
    listener = _listener(monkeypatch)
    device = Mock()
    device.labels = []
    devices_in_room = {"device.philips_hue_1": device}

    import logging as logging_module
    with caplog.at_level(logging_module.WARNING, logger="custom_components.opengrowbox.OGBController.RegistryListener"):
        listener._warn_device_found_without_label(
            _entity("sensor.hue_temperature", "device.philips_hue_1"),
            [],
            devices_in_room,
        )

    messages = [rec.message for rec in caplog.records]
    assert any("DEVICE FOUND WITHOUT LABEL" in msg for msg in messages)