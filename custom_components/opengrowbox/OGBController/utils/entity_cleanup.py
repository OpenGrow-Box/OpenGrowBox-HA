"""Pure decision logic for pruning orphaned entities from the registry.

Kept dependency-free so logic tests can import it without the integration's
HA-heavy ``__init__.py``.
"""


def should_remove_entity(
    disabled: bool,
    config_entry_id,
    active_entry_ids,
    has_state: bool,
) -> bool:
    """Return True when a registered entity should be removed.

    - A disabled entity was explicitly disabled by the user - keep it.
    - An entity bound to a deleted config entry is a true orphan.
    - An entity the platform no longer provides (no live state) is a leftover
      from a retired/renamed entity - generically covers past renames without
      maintaining a pattern list.
    """
    if disabled:
        return False

    has_invalid_entry = bool(config_entry_id) and config_entry_id not in active_entry_ids

    return not has_state or has_invalid_entry


def is_vanished(entity_id, known_entity_ids) -> bool:
    """Return True when an entity disappeared from the registry mid-processing.

    The registry listener collects its candidate list once and then retries
    values for several seconds. The orphan cleanup can remove those entities in
    that window, so a stale candidate must be dropped instead of being
    reported as a value error.
    """
    return entity_id not in known_entity_ids