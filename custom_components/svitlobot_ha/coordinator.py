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
    POWER_ON_THRESHOLD,
)
from .svitlobot import async_channel_ping

_LOGGER = logging.getLogger(__name__)

SVITLOBOT_PING_INTERVAL_S = 65


@dataclass(frozen=True)
class WatchdogData:
    power_on: bool
    watched_entity_id: str
    state: str | None
    voltage: float | None


def _parse_voltage(state_str: str | None) -> float | None:
    """Best-effort float parsing. Returns None when state is non-numeric."""
    if state_str is None:
        return None
    s = str(state_str).strip().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _is_power_on(state_str: str | None) -> tuple[bool, float | None]:
    if state_str is None:
        return (False, None)

    if state_str in OFFLINE_STATES:
        return (False, None)

    v = _parse_voltage(state_str)

    if v is None:
        return (True, None)

    return (v >= POWER_ON_THRESHOLD, v)


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

        self._last_meaningful_update = dt_util.utcnow()

    def _mark_meaningful_now(self) -> None:
        self._last_meaningful_update = dt_util.utcnow()

    def _init_meaningful_from_state(self) -> None:
        st = self.hass.states.get(self._voltage_entity_id)
        if st is None:
            self._last_meaningful_update = dt_util.utcnow()
            return
        self._last_meaningful_update = st.last_changed

    def _compute_power(self) -> tuple[bool, str | None, float | None, float | None]:
        st = self.hass.states.get(self._voltage_entity_id)
        if st is None:
            return (False, None, None, None)

        state_str = st.state
        power_on, voltage = _is_power_on(state_str)

        age = (dt_util.utcnow() - self._last_meaningful_update).total_seconds()
        if power_on and self._stale_timeout > 0 and age > self._stale_timeout:
            return (False, state_str, voltage, age)

        return (power_on, state_str, voltage, age)

    def _set_data(self, power_on: bool, state: str | None, voltage: float | None) -> None:
        self.async_set_updated_data(
            WatchdogData(
                power_on=power_on,
                watched_entity_id=self._voltage_entity_id,
                state=state,
                voltage=voltage,
            )
        )

    def _fire_svitlobot_ping_if_needed(self) -> None:
        if not self._svitlobot_channel_key:
            return

        now_ts = time.time()
        if self._last_svitlobot_ping_ts and (now_ts - self._last_svitlobot_ping_ts) < SVITLOBOT_PING_INTERVAL_S:
            return

        self._last_svitlobot_ping_ts = now_ts
        self.hass.async_create_task(async_channel_ping(self.hass, self._svitlobot_channel_key))

    async def async_start(self) -> None:
        self._init_meaningful_from_state()

        power_on, state, voltage, _age = self._compute_power()
        self._set_data(power_on, state, voltage)

        if power_on:
            self._fire_svitlobot_ping_if_needed()

        @callback
        def _handle(event):
            old_state = event.data.get("old_state")
            new_state = event.data.get("new_state")

            old_state_str = old_state.state if old_state else None
            new_state_str = new_state.state if new_state else None

            if old_state is None or new_state is None:
                self._mark_meaningful_now()
            else:
                if (new_state_str != old_state_str) or (new_state.attributes != old_state.attributes):
                    self._mark_meaningful_now()

            new_power_on, new_voltage = _is_power_on(new_state_str)

            if self.data is not None and self.data.power_on == new_power_on:
                if self.data.state != new_state_str or self.data.voltage != new_voltage:
                    self._set_data(new_power_on, new_state_str, new_voltage)
                return

            if self._pending_task and not self._pending_task.done():
                self._pending_task.cancel()

            self._pending_task = self.hass.async_create_task(
                self._debounced_commit(new_power_on=new_power_on)
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
            if self.data and self.data.power_on:
                self._fire_svitlobot_ping_if_needed()

            now_ts = time.time()

            if self.data and self._probe_when_offline and (not self.data.power_on):
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

            power_on, state, voltage, _age = self._compute_power()
            current_power_on = self.data.power_on if self.data else None
            if current_power_on is None:
                return

            if power_on == current_power_on:
                if self.data and (self.data.state != state or self.data.voltage != voltage):
                    self._set_data(power_on, state, voltage)
                return

            if self._pending_task and not self._pending_task.done():
                self._pending_task.cancel()

            self._pending_task = self.hass.async_create_task(
                self._debounced_commit(new_power_on=power_on)
            )

    async def _debounced_commit(self, new_power_on: bool) -> None:
        try:
            if self._debounce > 0:
                await asyncio.sleep(self._debounce)

            current_power_on, current_state, current_voltage, _age = self._compute_power()
            if current_power_on != new_power_on:
                return

            self._set_data(new_power_on, current_state, current_voltage)

            if new_power_on:
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
