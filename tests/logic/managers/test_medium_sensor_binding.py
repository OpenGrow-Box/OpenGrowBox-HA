"""Medium binding: label-name routing, single-medium ownership, value ranges.

These cover three defects that together turned a conductivity probe into a
"400 %" substrate moisture reading:

* ``_update_aggregated_value`` averaged any number, so a mis-typed sensor
  silently produced an impossible moisture value.
* the medium was resolved by parsing digits out of the label *id*, which Home
  Assistant keeps when a label is renamed (``coco_1`` -> id ``medium_2``).
* a sensor restored from the datastore could stay in its old medium after the
  live labels pointed at another one, so two media aggregated the same probe.
"""

import logging

import pytest

from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBMedium import (
    GrowMedium,
    MediumType,
)
from custom_components.opengrowbox.OGBController.managers.medium.OGBMediumManager import (
    OGBMediumManager,
)
from custom_components.opengrowbox.OGBController.OGBDevices.Sensor import (
    select_medium_label_id,
    select_medium_label_names,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


def _medium(name, room="test_room"):
    return GrowMedium(
        eventManager=FakeEventManager(),
        dataStore=FakeDataStore({}),
        room=room,
        medium_type=MediumType.COCO,
        name=name,
    )


def _manager(room="test_room"):
    return OGBMediumManager(
        hass=None,
        data_store=FakeDataStore({}),
        event_manager=FakeEventManager(),
        room=room,
    )


def _reading(entity_id, value):
    return {
        "entity_id": entity_id,
        "sensor_type": "moisture",
        "value": value,
        "unit": "%",
        "device_name": "probe",
        "room": "test_room",
    }


# ---------------------------------------------------------------------------
# 3 - aggregation range guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregation_drops_out_of_range_moisture():
    medium = _medium("coco_1")
    await medium.register_sensor(_reading("sensor.mec_moisture", 37.65))
    await medium.register_sensor(_reading("sensor.mec_conductivity", 542))
    await medium.register_sensor(_reading("sensor.thcs_conductivity", 953))

    assert medium.current_moisture == 37.65
    assert medium.registered_sensors["moisture"] == [
        "sensor.mec_moisture",
        "sensor.mec_conductivity",
        "sensor.thcs_conductivity",
    ]


@pytest.mark.asyncio
async def test_aggregation_still_averages_valid_readings():
    medium = _medium("coco_1")
    await medium.register_sensor(_reading("sensor.a", 37.65))
    await medium.register_sensor(_reading("sensor.b", 72.0))

    assert medium.current_moisture == pytest.approx(54.825)


@pytest.mark.asyncio
async def test_out_of_range_readings_are_logged_once(caplog):
    medium = _medium("coco_1")
    with caplog.at_level(logging.WARNING):
        await medium.register_sensor(_reading("sensor.mec_conductivity", 542))
        await medium.register_sensor(_reading("sensor.mec_conductivity", 543))

    warnings = [
        record for record in caplog.records if "erlaubt sind" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert "sensor.mec_conductivity" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_unregister_clears_aggregated_value_of_last_sensor():
    medium = _medium("coco_1")
    await medium.register_sensor(_reading("sensor.only", 44.0))
    assert medium.current_moisture == 44.0

    assert medium.unregister_sensor("sensor.only") is True
    assert medium.current_moisture is None


@pytest.mark.asyncio
async def test_unregister_recomputes_from_remaining_sensors():
    medium = _medium("coco_1")
    await medium.register_sensor(_reading("sensor.a", 40.0))
    await medium.register_sensor(_reading("sensor.b", 60.0))
    assert medium.current_moisture == 50.0

    medium.unregister_sensor("sensor.a")
    assert medium.current_moisture == 60.0


# ---------------------------------------------------------------------------
# 4 - medium resolved by label name, not by the stale label id
# ---------------------------------------------------------------------------


def test_label_name_beats_stale_label_id():
    manager = _manager()
    manager.media = [_medium("coco_1"), _medium("coco_2")]

    # Label was renamed to coco_1 but Home Assistant kept the id medium_2.
    index = manager._resolve_medium_index("medium_2", ["coco_1"])

    assert index == 0
    assert manager.media[index].name == "coco_1"


def test_label_name_matching_second_medium():
    manager = _manager()
    manager.media = [_medium("coco_1"), _medium("coco_2")]

    assert manager._resolve_medium_index("medium_1", ["coco_2"]) == 1


def test_legacy_index_used_when_name_does_not_match(caplog):
    manager = _manager()
    manager.media = [_medium("coco_1"), _medium("coco_2")]

    with caplog.at_level(logging.WARNING):
        index = manager._resolve_medium_index("medium_1", ["coco_3"], "sensor.x")

    assert index == 0
    assert any("matches no medium" in record.getMessage() for record in caplog.records)


def test_legacy_index_without_entity_names_does_not_warn(caplog):
    manager = _manager()
    manager.media = [_medium("coco_1"), _medium("coco_2")]

    with caplog.at_level(logging.WARNING):
        index = manager._resolve_medium_index("medium_1_2", [])

    assert index == 0
    assert not [r for r in caplog.records if "matches no medium" in r.getMessage()]


def test_unresolvable_label_returns_none():
    manager = _manager()
    manager.media = [_medium("coco_1")]

    assert manager._resolve_medium_index("other_label", ["unknown"]) is None
    assert manager._resolve_medium_index("medium_9", []) is None


# ---------------------------------------------------------------------------
# 5 - one entity lives in exactly one medium
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registration_moves_entity_out_of_restored_medium():
    manager = _manager()
    coco_1 = _medium("coco_1")
    coco_2 = _medium("coco_2")
    manager.media = [coco_1, coco_2]

    # Restore puts the probe into coco_2 ...
    await coco_2.register_sensor(_reading("sensor.mec_moisture", 37.65))
    manager._entity_to_medium_index["sensor.mec_moisture"] = 1

    # ... but the current labels say coco_1.
    await manager._process_sensor_registration(
        {
            "entity_id": "sensor.mec_moisture",
            "sensor_type": "moisture",
            "medium_label": "medium_2",
            "medium_label_names": ["coco_1"],
            "room": "test_room",
            "value": 37.65,
            "unit": "%",
            "context": "soil",
        }
    )

    assert "sensor.mec_moisture" not in coco_2.registered_sensors.get("moisture", [])
    assert "sensor.mec_moisture" in coco_1.registered_sensors["moisture"]
    assert manager._entity_to_medium_index["sensor.mec_moisture"] == 0


@pytest.mark.asyncio
async def test_move_resets_stale_value_of_abandoned_medium():
    manager = _manager()
    coco_1 = _medium("coco_1")
    coco_2 = _medium("coco_2")
    manager.media = [coco_1, coco_2]

    await coco_2.register_sensor(_reading("sensor.mec_moisture", 37.65))

    await manager._process_sensor_registration(
        {
            "entity_id": "sensor.mec_moisture",
            "sensor_type": "moisture",
            "medium_label": "medium_2",
            "medium_label_names": ["coco_1"],
            "room": "test_room",
            "value": 37.65,
            "unit": "%",
            "context": "soil",
        }
    )

    assert coco_2.current_moisture is None


@pytest.mark.asyncio
async def test_registration_skipped_when_no_medium_matches(caplog):
    manager = _manager()
    coco_1 = _medium("coco_1")
    manager.media = [coco_1]

    with caplog.at_level(logging.WARNING):
        await manager._process_sensor_registration(
            {
                "entity_id": "sensor.stray",
                "sensor_type": "moisture",
                "medium_label": "other_label",
                "medium_label_names": ["unknown_medium"],
                "room": "test_room",
                "value": 10.0,
                "unit": "%",
                "context": "soil",
            }
        )

    assert coco_1.registered_sensors.get("moisture", []) == []
    assert "sensor.stray" not in manager._entity_to_medium_index
    assert any("SENSOR NOT REGISTERED" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 4 - Sensor side: scope-prioritised label selection
# ---------------------------------------------------------------------------


DEVICE_LABELS = [
    {"id": "medium_1_2", "name": "Medium_1", "scope": "device", "entity": None},
    {"id": "soil", "name": "Soil", "scope": "device", "entity": None},
]


def test_entity_label_wins_even_though_device_labels_come_first():
    labels = DEVICE_LABELS + [
        {"id": "medium_2", "name": "coco_1", "scope": "entity", "entity": "sensor.x"}
    ]

    assert select_medium_label_id(labels) == "medium_2"
    assert select_medium_label_names(labels) == ["coco_1"]


def test_device_label_is_used_when_entity_has_none():
    assert select_medium_label_id(DEVICE_LABELS) == "medium_1_2"
    assert select_medium_label_names(DEVICE_LABELS) == []


def test_context_labels_are_not_medium_names():
    labels = [
        {"id": "soil", "name": "Soil", "scope": "entity", "entity": "sensor.x"},
        {"id": "coco_2", "name": "coco_2", "scope": "entity", "entity": "sensor.x"},
    ]

    assert select_medium_label_names(labels) == ["coco_2"]


def test_device_scoped_labels_are_not_name_candidates():
    labels = [{"id": "heizung", "name": "Heizung", "scope": "device", "entity": None}]

    assert select_medium_label_id(labels) is None
    assert select_medium_label_names(labels) == []


# ---------------------------------------------------------------------------
# End-to-end: the exact setup from the 400 % report
# ---------------------------------------------------------------------------


async def _bind(manager, entity_id, sensor_type, value, label_id, label_name):
    await manager._process_sensor_registration(
        {
            "entity_id": entity_id,
            "sensor_type": sensor_type,
            "medium_label": label_id,
            "medium_label_names": [label_name],
            "room": "test_room",
            "value": value,
            "unit": "%" if sensor_type == "moisture" else "µS/cm",
            "context": "soil",
        }
    )


@pytest.mark.asyncio
async def test_groom_layout_binds_each_probe_to_its_labelled_medium():
    manager = _manager()
    coco_1 = _medium("coco_1")
    coco_2 = _medium("coco_2")
    manager.media = [coco_1, coco_2]

    # Restore state from the report: sensormec sat in coco_2, sensorthcs in coco_1.
    await coco_2.register_sensor(_reading("sensor.sensormec_moisture", 37.65))
    manager._entity_to_medium_index["sensor.sensormec_moisture"] = 1
    await coco_1.register_sensor(_reading("sensor.sensorthcs_moisture", 72.0))
    manager._entity_to_medium_index["sensor.sensorthcs_moisture"] = 0

    # coco_1 / id medium_2 (stale id), coco_2 / id medium_1, coco_3 does not exist.
    await _bind(manager, "sensor.sensormec_moisture", "moisture", 37.65, "medium_2", "coco_1")
    await _bind(manager, "sensor.sensormec_conductivity", "ec", 542, "medium_2", "coco_1")
    await _bind(manager, "sensor.sensorthcs_moisture", "moisture", 72.0, "coco_2", "coco_2")
    await _bind(manager, "sensor.sensorthcs_conductivity", "ec", 953, "medium_1", "coco_3")

    # Every entity lives in exactly one medium.
    ownership = {}
    for index, medium in enumerate(manager.media):
        for entities in medium.registered_sensors.values():
            for entity_id in entities:
                ownership.setdefault(entity_id, []).append(index)
    assert all(len(indices) == 1 for indices in ownership.values())

    # The stale id medium_2 must not drag the probe into coco_2.
    assert manager._entity_to_medium_index["sensor.sensormec_moisture"] == 0
    assert manager._entity_to_medium_index["sensor.sensorthcs_moisture"] == 1

    # No medium shows an impossible moisture value.
    assert coco_1.current_moisture == 37.65
    assert coco_2.current_moisture == 72.0
    assert coco_1.current_ec == pytest.approx((542 + 953) / 2)


# ---------------------------------------------------------------------------
# Legacy Medium_x labels must keep working unchanged
# ---------------------------------------------------------------------------


def test_medium_x_label_only_binds_like_before():
    """Device carries only the classic Medium_1 label, no entity labels."""
    manager = _manager()
    manager.media = [_medium("Medium_1"), _medium("Medium_2")]

    labels = [{"id": "medium_1_2", "name": "Medium_1", "scope": "device", "entity": None}]
    label_id = select_medium_label_id(labels)
    names = select_medium_label_names(labels)

    assert label_id == "medium_1_2"
    assert names == []
    assert manager._resolve_medium_index(label_id, names) == 0


def test_medium_x_label_number_still_selects_the_medium():
    manager = _manager()
    manager.media = [_medium("Medium_1"), _medium("Medium_2")]

    assert manager._resolve_medium_index("medium_2", []) == 1
    assert manager._resolve_medium_index("medium_1", ["Medium_1"]) == 0
    assert manager._resolve_medium_index("medium_2", ["Medium_2"]) == 1


@pytest.mark.asyncio
async def test_medium_x_entity_labels_bind_end_to_end():
    """Classic install: both probes labelled Medium_1 by their id/name."""
    manager = _manager()
    medium_1 = _medium("Medium_1")
    medium_2 = _medium("Medium_2")
    manager.media = [medium_1, medium_2]

    await _bind(manager, "sensor.probe_a", "moisture", 41.0, "medium_1", "Medium_1")
    await _bind(manager, "sensor.probe_b", "moisture", 55.0, "medium_2", "Medium_2")

    assert manager._entity_to_medium_index["sensor.probe_a"] == 0
    assert manager._entity_to_medium_index["sensor.probe_b"] == 1
    assert medium_1.current_moisture == 41.0
    assert medium_2.current_moisture == 55.0
