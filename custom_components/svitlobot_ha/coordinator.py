from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_VOLTAGE_ENTITY_ID,
    CONF_DEBOUNCE_SECONDS,
    DEFAULT_DEBOUNCE_SECONDS,
    OFFLINE_STATES,
    CONF_STALE_TIMEOUT_SECONDS,
    DEFAULT_STALE_TIMEOUT_SECONDS,
    CONF_REFRESH_SECONDS,
    DEFAULT_REFRESH_SECONDS,
    CONF_SVITLOBOT_CHANNEL_KEY,
    DEFAULT_SVITLOBOT_CHANNEL_KEY,
)
from .svitlobot import async_channel_ping

_LOGGER = logging.getLogger(__name__)

SVITLOBOT_PING_INTERVAL_S = 65


@dataclass(frozen=True)
class WatchdogData:
    online: bool
    watched_entity_id: str
    state: str | None


def _is_online(state_str: str | None) -> bool:
    if state_str is None:
        return False
    return state_str not in OFFLINE_STATES


class PowerWatchdogCoordinator(DataUpdateCoordinator[WatchdogData]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name="svitlobot_ha",
            update_interval=None,
        )
        self.entry = entry

        def _cfg(key, default=None):
            return entry.options.get(key, entry.data.get(key, default))

        self._voltage_entity_id = str(_cfg(CONF_VOLTAGE_ENTITY_ID)).strip()

        self._debounce = int(_cfg(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS))
        self._stale_timeout = int(_cfg(CONF_STALE_TIMEOUT_SECONDS, DEFAULT_STALE_TIMEOUT_SECONDS))
        self._refresh_every = int(_cfg(CONF_REFRESH_SECONDS, DEFAULT_REFRESH_SECONDS))

        self._svitlobot_channel_key = str(
            _cfg(CONF_SVITLOBOT_CHANNEL_KEY, DEFAULT_SVITLOBOT_CHANNEL_KEY)
        ).strip()

        self._pending_task: asyncio.Task | None = None
        self._unsub_state = None
        self._unsub_timer = None

        self._check_interval = 5
        self._periodic_lock = asyncio.Lock()

        self._last_refresh_ts = 0.0
        self._last_svitlobot_ping_ts = 0.0

        self._probe_when_offline = True
        self._probe_every = 20
        self._last_probe_ts = 0.0

    def _get_report_time(self, st) -> object:
        rep = getattr(st, "last_reported", None)
        return rep or st.last_updated

    def _compute_online(self) -> tuple[bool, str | None, float | None]:
        st = self.hass.states.get(self._voltage_entity_id)
        if st is None:
            return (False, None, None)

        state_str = st.state
        online = _is_online(state_str)

        age = (dt_util.utcnow() - self._get_report_time(st)).total_seconds()
        if online and self._stale_timeout > 0 and age > self._stale_timeout:
            return (False, state_str, age)

        return (online, state_str, age)

    def _set_data(self, online: bool, state: str | None) -> None:
        self.async_set_updated_data(
            WatchdogData(
                online=online,
                watched_entity_id=self._voltage_entity_id,
                state=state,
            )
        )

    def _fire_svitlobot_ping_if_needed(self) -> None:
        if not self._svitlobot_channel_key:
            return

        now_ts = time.time()
        if self._last_svitlobot_ping_ts and (now_ts - self._last_svitlobot_ping_ts) < SVITLOBOT_PING_INTERVAL_S:
            return

        self._last_svitlobot_ping_ts = now_ts
        self.hass.async_create_task(
            async_channel_ping(self.hass, self._svitlobot_channel_key)
        )

    async def async_start(self) -> None:
        online, state, _age = self._compute_online()
        self._set_data(online, state)

        if online:
            self._fire_svitlobot_ping_if_needed()

        @callback
        def _handle(event):
            new_state = event.data.get("new_state")
            new_state_str = new_state.state if new_state else None
            new_online = _is_online(new_state_str)

            # якщо статус не змінився — просто оновимо стан
            if self.data is not None and self.data.online == new_online:
                if self.data.state != new_state_str:
                    self._set_data(new_online, new_state_str)
                return

            if self._pending_task and not self._pending_task.done():
                self._pending_task.cancel()

            self._pending_task = self.hass.async_create_task(
                self._debounced_commit(new_online=new_online)
            )

        self._unsub_state = async_track_state_change_event(
            self.hass,
            [self._voltage_entity_id],
            _handle,
        )

        self._unsub_timer = async_track_time_interval(
            self.hass,
            self._periodic_check,
            timedelta(seconds=self._check_interval),
        )

    async def _periodic_check(self, _now) -> None:
        async with self._periodic_lock:
            # ping поки онлайн
            if self.data and self.data.online:
                self._fire_svitlobot_ping_if_needed()

            now_ts = time.time()

            if self.data and self._probe_when_offline and (not self.data.online):
                if now_ts - self._last_probe_ts >= self._probe_every:
                    self._last_probe_ts = now_ts
                    try:
                        await self.hass.services.async_call(
                            "homeassistant",
                            "update_entity",
                            {"entity_id": self._voltage_entity_id},
                            blocking=True,
                        )
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception("update_entity probe failed")

            # опційний refresh сенсора (як було)
            if self._refresh_every > 0 and (now_ts - self._last_refresh_ts >= self._refresh_every):
                self._last_refresh_ts = now_ts
                try:
                    await self.hass.services.async_call(
                        "homeassistant",
                        "update_entity",
                        {"entity_id": self._voltage_entity_id},
                        blocking=True,
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("update_entity refresh failed")

            online, state, _age = self._compute_online()
            current_online = self.data.online if self.data else None
            if current_online is None:
                return

            if online == current_online:
                if self.data and self.data.state != state:
                    self._set_data(online, state)
                return

            if self._pending_task and not self._pending_task.done():
                self._pending_task.cancel()

            self._pending_task = self.hass.async_create_task(
                self._debounced_commit(new_online=online)
            )

    async def _debounced_commit(self, new_online: bool) -> None:
        try:
            if self._debounce > 0:
                await asyncio.sleep(self._debounce)

            current_online, current_state, _age = self._compute_online()
            if current_online != new_online:
                return

            self._set_data(new_online, current_state)

            # коли стає онлайн — одразу ping
            if new_online:
                self._fire_svitlobot_ping_if_needed()

        except asyncio.CancelledError:
            return

    async def async_stop(self) -> None:
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()

        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None

        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
