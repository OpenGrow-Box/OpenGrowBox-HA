from __future__ import annotations


class FakeDataStore:
    def __init__(self, initial: dict | None = None):
        self.data = initial.copy() if initial else {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def getDeep(self, path, default=None):
        parts = path.split(".")
        cur = self.data
        for part in parts:
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def setDeep(self, path, value):
        parts = path.split(".")
        cur = self.data
        for part in parts[:-1]:
            if part not in cur or not isinstance(cur[part], dict):
                cur[part] = {}
            cur = cur[part]
        cur[parts[-1]] = value

    def delete(self, path):
        """Delete a value at a nested path."""
        parts = path.split(".")
        cur = self.data
        for part in parts[:-1]:
            if not isinstance(cur, dict) or part not in cur:
                return  # Path doesn't exist, nothing to delete
            cur = cur[part]
        if isinstance(cur, dict) and parts[-1] in cur:
            del cur[parts[-1]]

    def get_active_value(self, path, default=None):
        """Smart getter: Returns grow plan values when active, else normal values."""
        # For tests: simply delegate to getDeep
        return self.getDeep(path, default)


class FakeEventManager:
    def __init__(self):
        self.listeners = {}
        self.emitted = []

    def on(self, event_name, callback):
        self.listeners.setdefault(event_name, []).append(callback)

    async def emit(self, event_name, data, haEvent=False, debug_type=None):
        self.emitted.append(
            {
                "event_name": event_name,
                "data": data,
                "haEvent": haEvent,
                "debug_type": debug_type,
            }
        )


def action_names(action_map):
    return {(a.capability, a.action) for a in action_map}
