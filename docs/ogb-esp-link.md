# OGB-ESP connection (for the OGB console)

The integration can control OGB-ESP controllers: while Home Assistant sends a heartbeat, the ESP
mesh pauses its own local control; if HA stops, the ESP takes over again after its timeout.

The connections are **global** (not per room). One entry per ESP mesh is enough: the ESP forwards
the heartbeat to all of its nodes. HA rooms and ESP areas belong together automatically when their
names are equal (case-insensitive).

Only the ESP API key is stored (`.storage/opengrowbox_esp_links`), never the device password.

## Services (call with `return_response`)

```js
// add / re-login (also the fix for state "auth_failed")
await hass.callService("opengrowbox", "esp_link_add",
  { host: "192.168.1.50", password: "…", takeover: true }, undefined, false, true);
// -> { response: { ok: true, host: "http://192.168.1.50" } }
// -> { response: { ok: false, error: "invalid_auth" | "cannot_connect" | "invalid_host" } }

await hass.callService("opengrowbox", "esp_link_remove", { host }, undefined, false, true);

await hass.callService("opengrowbox", "esp_link_set_mode",
  { host, mode: "local" | "external" | "free", source: "global" }, undefined, false, true);

const { response } = await hass.callService("opengrowbox", "esp_link_status", {}, undefined, false, true);
```

`esp_link_status` response:

```json
{
  "links": [
    {
      "host": "http://192.168.1.50",
      "state": "ok",                    // ok | offline | auth_failed | starting
      "takeover": true,
      "heartbeat_running": true,
      "last_ok": 1792346100.5,          // unix time of the last successful heartbeat
      "last_error": null,
      "mode": "external",               // local | external | free (from the ESP)
      "local_paused": true,
      "external_active": true,
      "sources": [{"source": "global", "enabled": true, "active": true, "age_ms": 3000,
                   "remote": false, "timeout_ms": 30000}],
      "holds": [{"name": "Exhaust", "remaining_s": 754}],
      "areas": [{"id": "dryingtent", "name": "DryingTent", "room": "DryingTent"},
                {"id": "veg", "name": "Veg Tent", "room": null}]
    }
  ]
}
```

If the ESP cannot be asked for details, `details_error` is set and the ESP fields are missing.

## Suggested console page "OGB-ESP"

- List of `links` (poll `esp_link_status` every ~10 s while the page is open):
  - address, state badge (ok = green, offline = grey, auth_failed = red with "log in again").
  - mode selector local / HA (external, global) / free -> `esp_link_set_mode`
    (free: confirm, the ESP then has no safety functions).
  - areas with their HA room; areas without a room: hint "create a room with the same name".
  - manually held devices (`holds`).
  - remove button -> `esp_link_remove`.
- "Add OGB-ESP": address + device password (+ checkbox "take over control", default on) ->
  `esp_link_add`; show `error` (invalid_auth: wrong password, cannot_connect: not reachable).
