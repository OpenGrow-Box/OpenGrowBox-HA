"""Link to OGB-ESP controllers: one-time login, external-control heartbeat, status for the OGB console.

The links are global (not per room): the ESP forwards a heartbeat to every node of its mesh, so one
entry (address + device password of any node) per ESP mesh is enough. HA rooms and ESP areas belong
together when their names match; nothing has to be configured for that.

While HA sends the "global" heartbeat the ESP mesh pauses its own local control; if the heartbeat
stops (HA down, network gone) the ESP resumes on its own after its timeout (default 30 s).

ESP API used:
  POST /api/auth/login               {"password": ...} -> {"api_key": ...}   (no auth)
  GET  /api/csrf                     -> {"csrf_token": ...}                  (no auth)
  GET  /api/control/external         mode / sources / heartbeat age          (Bearer)
  POST /api/control/external         enable "global" / set mode              (Bearer + CSRF)
  POST /api/control/heartbeat/global                                         (Bearer + CSRF)
  GET  /api/zones                    areas of the mesh                       (Bearer)

Only the API key is stored (in .storage/opengrowbox_esp_links), never the device password.

Services (no secrets, all return a response):
  opengrowbox.esp_link_remove   host
  opengrowbox.esp_link_status   -> {"links": [...]}
  opengrowbox.esp_link_set_mode host, mode (local|external|free), [source=global]

The device password never goes through a service or a bus event (both end up in the recorder
database / logbook). It only travels over the websocket, which HA does not record:
  {"type": "opengrowbox/esp_link_add", "host", "password", "takeover"}   (admin)
  {"type": "opengrowbox/console_secret", "prompt_id", "value"}            (admin)
The console command "esp add <ip>" opens a one-time prompt (2 min) that the frontend answers
with console_secret; the console response event carries {"prompt": {"id", "type": "password"}}.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import secrets as _secrets

import aiohttp
import voluptuous as vol

from homeassistant.components import websocket_api

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import (
    CONF_ESP_API_KEY,
    CONF_ESP_HOST,
    DOMAIN,
    ESP_HEARTBEAT_INTERVAL_S,
)

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=8)
_MANAGER_KEY = "esp_link_manager"
_STORE_KEY = "opengrowbox_esp_links"
_STORE_VERSION = 1


class EspConnectError(Exception):
    """ESP not reachable or answered with something unexpected."""


class EspAuthError(Exception):
    """Password or API key rejected by the ESP."""


def normalize_host(host: str) -> str:
    """Return 'http://host[:port]' without trailing slash ('' for empty input)."""
    host = str(host or "").strip().rstrip("/")
    if not host:
        return ""
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host


class EspDefaultPasswordError(Exception):
    """The ESP still uses its factory password - it must be changed on the ESP first."""


async def async_esp_login(hass: HomeAssistant, host: str, password: str) -> str:
    """Exchange the ESP device password for its API key."""
    base = normalize_host(host)
    if not base:
        raise EspConnectError("no host")
    session = async_get_clientsession(hass)
    try:
        async with session.post(
            f"{base}/api/auth/login", json={"password": password}, timeout=_TIMEOUT
        ) as resp:
            if resp.status == 401:
                raise EspAuthError("invalid password")
            if resp.status != 200:
                raise EspConnectError(f"login HTTP {resp.status}")
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
        raise EspConnectError(str(err)) from err
    api_key = str((data or {}).get("api_key") or "")
    if not api_key:
        raise EspConnectError("no api_key in login response")
    if (data or {}).get("default_password"):
        raise EspDefaultPasswordError("factory password still active")
    return api_key


class EspLink:
    """One ESP mesh: sends the 'global' heartbeat and answers status requests."""

    def __init__(self, hass: HomeAssistant, host: str, api_key: str, takeover: bool) -> None:
        self.hass = hass
        self.base = normalize_host(host)
        self.api_key = api_key
        self.takeover = takeover
        self._csrf: str | None = None
        self._task: asyncio.Task | None = None
        self._enabled_on_esp = False
        self.state = "starting"            # starting | ok | offline | auth_failed
        self.last_ok: float | None = None
        self.last_error: str | None = None

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self.state == "auth_failed":
            return
        if self._task is None or self._task.done():
            self._task = self.hass.async_create_background_task(
                self._run(), f"{DOMAIN}_esp_heartbeat_{self.base}"
            )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ---- HTTP helpers ----------------------------------------------------
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def _fetch_csrf(self, session: aiohttp.ClientSession) -> None:
        async with session.get(f"{self.base}/api/csrf", timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                raise EspConnectError(f"csrf HTTP {resp.status}")
            data = await resp.json(content_type=None)
        self._csrf = str((data or {}).get("csrf_token") or "")

    async def _get(self, path: str) -> Any:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(f"{self.base}{path}", headers=self._auth(), timeout=_TIMEOUT) as resp:
                if resp.status == 401:
                    raise EspAuthError("api key rejected")
                if resp.status != 200:
                    raise EspConnectError(f"{path} HTTP {resp.status}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise EspConnectError(str(err)) from err

    async def _post(self, path: str, payload: dict[str, Any] | None = None) -> None:
        """POST with Bearer + CSRF; refreshes the CSRF token once (it changes on ESP reboot)."""
        session = async_get_clientsession(self.hass)
        try:
            for attempt in range(2):
                if not self._csrf:
                    await self._fetch_csrf(session)
                headers = {**self._auth(), "X-CSRF-Token": self._csrf or ""}
                async with session.post(
                    f"{self.base}{path}", json=payload or {}, headers=headers, timeout=_TIMEOUT
                ) as resp:
                    if resp.status == 200:
                        return
                    if resp.status == 403 and attempt == 0:
                        self._csrf = None           # stale token -> fetch a new one and retry
                        continue
                    if resp.status == 401:
                        raise EspAuthError("api key rejected")
                    raise EspConnectError(f"{path} HTTP {resp.status}")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise EspConnectError(str(err)) from err

    async def set_mode(self, mode: str, source: str = "global") -> None:
        body: dict[str, Any] = {"mode": mode}
        if mode == "external":
            body["source"] = source
        await self._post("/api/control/external", body)
        # an explicit mode choice wins over the automatic takeover
        self._enabled_on_esp = True

    async def fetch_details(self) -> dict[str, Any]:
        """Live data from the ESP for the console (mode, heartbeat, areas)."""
        out: dict[str, Any] = {}
        ext = await self._get("/api/control/external")
        out["mode"] = ext.get("mode")
        out["local_paused"] = ext.get("local_paused")
        out["external_active"] = ext.get("external_active")
        out["sources"] = ext.get("sources", [])
        out["holds"] = ext.get("holds", [])
        zones = await self._get("/api/zones")
        out["areas"] = [
            {"id": z.get("id", ""), "name": z.get("name", "")}
            for z in (zones or {}).get("zones", [])
            if z.get("id")
        ]
        return out

    # ---- loop ------------------------------------------------------------
    async def _run(self) -> None:
        while True:
            try:
                if self.takeover and not self._enabled_on_esp:
                    await self._post(
                        "/api/control/external", {"sources": [{"source": "global", "enabled": True}]}
                    )
                    self._enabled_on_esp = True
                    _LOGGER.info("OGB-ESP %s: external control 'global' enabled", self.base)
                await self._post("/api/control/heartbeat/global")
                if self.state != "ok":
                    _LOGGER.info("OGB-ESP %s: heartbeat ok", self.base)
                self.state = "ok"
                self.last_ok = time.time()
                self.last_error = None
            except EspAuthError:
                _LOGGER.error("OGB-ESP %s: API key rejected - log in again in the OGB console", self.base)
                self.state = "auth_failed"
                self.last_error = "auth_failed"
                _notify_auth_failed(self.hass, self.base)
                return
            except EspConnectError as err:
                if self.state != "offline":
                    _LOGGER.warning(
                        "OGB-ESP %s: heartbeat failed (%s) - ESP falls back to local control", self.base, err
                    )
                self.state = "offline"
                self.last_error = str(err)
                self._csrf = None
                self._enabled_on_esp = False     # ESP may have been reset meanwhile
            await asyncio.sleep(ESP_HEARTBEAT_INTERVAL_S)


def _notify_auth_failed(hass: HomeAssistant, base: str) -> None:
    try:
        from homeassistant.components import persistent_notification

        persistent_notification.async_create(
            hass,
            f"The OGB-ESP at {base} no longer accepts the stored key (e.g. after a reset). "
            "Add it again with its device password in the OpenGrowBox console.",
            title="OpenGrowBox: OGB-ESP login needed",
            notification_id=f"{DOMAIN}_esp_auth_{base}",
        )
    except Exception as err:  # notification is best effort
        _LOGGER.debug("notification failed: %s", err)


class EspLinkManager:
    """Global list of ESP links, persisted in HA storage."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, _STORE_VERSION, _STORE_KEY)
        self.links: dict[str, EspLink] = {}
        self._entries: set[str] = set()
        self._migrated: set[str] = set()     # room entries whose old per-room link was taken over
        self._loaded = False
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        async with self._lock:
            if self._loaded:
                return
            data = await self._store.async_load() or {}
            for item in data.get("links", []):
                host = normalize_host(item.get("host", ""))
                if host and item.get("api_key"):
                    self.links[host] = EspLink(
                        self.hass, host, item["api_key"], bool(item.get("takeover", True))
                    )
            self._migrated = set(data.get("migrated_entries", []))
            self._loaded = True

    async def _async_save(self) -> None:
        await self._store.async_save(
            {"links": [
                {"host": l.base, "api_key": l.api_key, "takeover": l.takeover}
                for l in self.links.values()
            ], "migrated_entries": sorted(self._migrated)}
        )

    def _active(self) -> bool:
        return bool(self._entries)

    # ---- entries (heartbeats run while the integration has a loaded room) ----
    async def async_attach_entry(self, entry) -> None:
        await self.async_load()
        await self._async_migrate_entry(entry)
        self._entries.add(entry.entry_id)
        for link in self.links.values():
            link.start()

    async def async_detach_entry(self, entry_id: str) -> None:
        self._entries.discard(entry_id)
        if not self._entries:
            for link in self.links.values():
                await link.stop()

    async def _async_migrate_entry(self, entry) -> None:
        """A link that an earlier version stored per room moves to the global list (once; the
        entry itself is not touched, an update would reload it during setup)."""
        host = normalize_host(entry.data.get(CONF_ESP_HOST, ""))
        key = entry.data.get(CONF_ESP_API_KEY, "")
        if not host or not key or entry.entry_id in self._migrated:
            return
        self._migrated.add(entry.entry_id)
        if host not in self.links:
            self.links[host] = EspLink(self.hass, host, key, True)
            _LOGGER.info("OGB-ESP link %s moved from room '%s' to the global list",
                         host, entry.data.get("room_name"))
        await self._async_save()

    # ---- one-time secret prompts (console) ------------------------------------
    def create_prompt(self, on_secret) -> str:
        """on_secret(value) -> awaitable result dict; valid once for 2 minutes."""
        now = time.monotonic()
        self._prompts = {k: v for k, v in getattr(self, "_prompts", {}).items() if v[0] > now}
        pid = _secrets.token_urlsafe(16)
        self._prompts[pid] = (now + 120, on_secret)
        return pid

    async def async_resolve_prompt(self, pid: str, value: str) -> dict[str, Any]:
        entry = getattr(self, "_prompts", {}).pop(pid, None)
        if not entry or entry[0] < time.monotonic():
            return {"ok": False, "error": "prompt_expired"}
        return await entry[1](value)

    # ---- console actions ----------------------------------------------------
    async def async_add(self, host: str, password: str, takeover: bool) -> dict[str, Any]:
        await self.async_load()
        base = normalize_host(host)
        if not base:
            return {"ok": False, "error": "invalid_host"}
        try:
            api_key = await async_esp_login(self.hass, base, password)
        except EspDefaultPasswordError:
            return {"ok": False, "error": "default_password"}
        except EspAuthError:
            return {"ok": False, "error": "invalid_auth"}
        except EspConnectError as err:
            return {"ok": False, "error": "cannot_connect", "detail": str(err)}
        old = self.links.pop(base, None)
        if old:
            await old.stop()
        link = EspLink(self.hass, base, api_key, takeover)
        self.links[base] = link
        await self._async_save()
        if self._active():
            link.start()
        try:
            from homeassistant.components import persistent_notification

            persistent_notification.async_dismiss(self.hass, f"{DOMAIN}_esp_auth_{base}")
        except Exception:
            pass
        return {"ok": True, "host": base}

    async def async_remove(self, host: str) -> dict[str, Any]:
        await self.async_load()
        base = normalize_host(host)
        link = self.links.pop(base, None)
        if not link:
            return {"ok": False, "error": "not_found"}
        await link.stop()
        await self._async_save()
        return {"ok": True, "host": base}

    async def async_set_mode(self, host: str, mode: str, source: str) -> dict[str, Any]:
        await self.async_load()
        link = self.links.get(normalize_host(host))
        if not link:
            return {"ok": False, "error": "not_found"}
        try:
            await link.set_mode(mode, source)
        except EspAuthError:
            return {"ok": False, "error": "invalid_auth"}
        except EspConnectError as err:
            return {"ok": False, "error": "cannot_connect", "detail": str(err)}
        # the explicit choice sticks: no automatic takeover after an outage unless HA is in charge
        link.takeover = mode == "external" and source == "global"
        await self._async_save()
        return {"ok": True, "host": link.base, "mode": mode}

    @staticmethod
    def _room_key(name: Any) -> str:
        """Compare room / area names by letters and digits only: "Ambient" == "ambient",
        "Veg Tent" == "veg_tent" == "VegTent" (HA room names allow no spaces)."""
        return "".join(c for c in str(name or "").casefold() if c.isalnum())

    def _room_names(self) -> dict[str, str]:
        rooms = {}
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            name = str(entry.data.get("room_name", "")).strip()
            if name:
                rooms[self._room_key(name)] = name
        return rooms

    async def async_status(self) -> dict[str, Any]:
        await self.async_load()
        rooms = self._room_names()
        out = []
        for link in self.links.values():
            item: dict[str, Any] = {
                "host": link.base,
                "state": link.state,
                "takeover": link.takeover,
                "heartbeat_running": link.running,
                "last_ok": link.last_ok,
                "last_error": link.last_error,
            }
            if link.state != "auth_failed":
                try:
                    details = await link.fetch_details()
                    for area in details.get("areas", []):
                        area["room"] = rooms.get(self._room_key(area["name"])) or rooms.get(self._room_key(area["id"]))
                    item.update(details)
                except EspAuthError:
                    item["details_error"] = "invalid_auth"
                except EspConnectError as err:
                    item["details_error"] = str(err)
            out.append(item)
        return {"links": out}


def _manager(hass: HomeAssistant) -> EspLinkManager:
    data = hass.data.setdefault(DOMAIN, {})
    mgr = data.get(_MANAGER_KEY)
    if mgr is None:
        mgr = data[_MANAGER_KEY] = EspLinkManager(hass)
        _register_services(hass, mgr)
    return mgr


@websocket_api.websocket_command({
    vol.Required("type"): "opengrowbox/esp_link_add",
    vol.Required("host"): str,
    vol.Required("password"): str,
    vol.Optional("takeover", default=True): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_esp_link_add(hass, connection, msg):
    res = await _manager(hass).async_add(msg["host"], msg["password"], msg["takeover"])
    connection.send_result(msg["id"], res)


@websocket_api.websocket_command({
    vol.Required("type"): "opengrowbox/console_secret",
    vol.Required("prompt_id"): str,
    vol.Required("value"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_console_secret(hass, connection, msg):
    res = await _manager(hass).async_resolve_prompt(msg["prompt_id"], msg["value"])
    connection.send_result(msg["id"], res)


def _register_services(hass: HomeAssistant, mgr: EspLinkManager) -> None:
    # secrets only over the websocket (not recorded), never as service data
    websocket_api.async_register_command(hass, _ws_esp_link_add)
    websocket_api.async_register_command(hass, _ws_console_secret)

    async def remove(call: ServiceCall):
        return await mgr.async_remove(call.data["host"])

    async def status(call: ServiceCall):
        return await mgr.async_status()

    async def set_mode(call: ServiceCall):
        return await mgr.async_set_mode(
            call.data["host"], call.data["mode"], call.data.get("source", "global")
        )

    hass.services.async_register(
        DOMAIN, "esp_link_remove", remove,
        schema=vol.Schema({vol.Required("host"): str}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "esp_link_status", status,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, "esp_link_set_mode", set_mode,
        schema=vol.Schema({
            vol.Required("host"): str,
            vol.Required("mode"): vol.In(["local", "external", "free"]),
            vol.Optional("source", default="global"): vol.In(["global", "ogb_api"]),
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )


async def async_attach_entry(hass: HomeAssistant, entry) -> None:
    """Called from async_setup_entry: services available, heartbeats running."""
    await _manager(hass).async_attach_entry(entry)


async def async_detach_entry(hass: HomeAssistant, entry_id: str) -> None:
    """Called from async_unload_entry: heartbeats stop with the last room (ESP resumes after timeout)."""
    mgr = hass.data.get(DOMAIN, {}).get(_MANAGER_KEY)
    if mgr:
        await mgr.async_detach_entry(entry_id)
