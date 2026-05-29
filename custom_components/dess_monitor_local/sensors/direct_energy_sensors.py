import logging
import math
import time
from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass, RestoreSensor
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfTime, PERCENTAGE
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import ExtraStoredData
from homeassistant.util import slugify

from custom_components.dess_monitor_local.sanity import (
    is_plausible_battery_current,
    is_plausible_battery_voltage,
    is_plausible_power,
    max_step_wh,
)
from custom_components.dess_monitor_local.soc_core import (
    BATTERY_MODE_LEAD_ACID,
    BATTERY_MODE_LI_BMS,
    BATTERY_MODE_LI_VOLTAGE,
    DEFAULT_FLOAT_NOISE_FLOOR_A,
    DEFAULT_FLOAT_VOLTAGE_WINDOW_V,
    SocEstimator,
)
from custom_components.dess_monitor_local.sensors.direct_sensor import (
    DirectSensorBase,
    DirectTypedSensorBase,
)

_LOGGER = logging.getLogger(__name__)


# The SoC algorithm (Coulomb counting, float deadband, snap-to-100%,
# integral-windup-safe debounce, BMS mirror) lives in the HA-free
# ``soc_core.SocEstimator`` so it can be unit-tested in isolation. The
# chemistry presets, debounce threshold and float-deadband defaults are
# all defined there; this module only re-uses the mode names and float
# defaults imported above. ``DirectBatteryStateOfChargeSensor`` is a thin
# adapter that resolves HA-bound inputs (capacity / mode / voltages from
# entities) and feeds them into a per-battery ``SocEstimator`` instance.
_FLOAT_VOLTAGE_WINDOW_V = DEFAULT_FLOAT_VOLTAGE_WINDOW_V
_FLOAT_NOISE_FLOOR_A = DEFAULT_FLOAT_NOISE_FLOOR_A


def _wall_now() -> datetime:
    """Wall-clock UTC ``now()``. Used for human-readable diagnostic
    timestamps (e.g. "last sync was at ..."), not for the trapezoidal
    integrator which uses ``time.monotonic`` to stay immune to clock
    jumps."""
    return datetime.now(timezone.utc)


class DirectEnergySensorBase(RestoreSensor, DirectTypedSensorBase):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_suggested_display_precision = 0
    _sensor_option_display_precision = 0

    def __init__(
            self,
            inverter_device,
            coordinator,
            data_section: str,
            data_key: str,
            sensor_suffix: str = "",
            name_suffix: str = "",
    ):
        # Инициализируем как DirectTypedSensorBase (он выставит unique_id и имя)
        super().__init__(
            inverter_device,
            coordinator,
            data_section,
            data_key,
            sensor_suffix,
            name_suffix,
        )
        # Гарантируем, что _attr_native_value сразу — число, а не None
        self._attr_native_value = 0.0

        # Для интеграции
        self._prev_power = None
        self._prev_ts = time.monotonic()
        self._restored = False

    async def async_added_to_hass(self) -> None:
        # При восстановлении из базы кладём значение, но если оно None — ставим 0
        last_data = await self.async_get_last_extra_data()
        if last_data is not None:
            restored = last_data.as_dict().get("native_value", None)
            # Если в базе было None, заменяем на 0
            self._attr_native_value = float(restored) if restored is not None else 0.0
        else:
            self._attr_native_value = 0.0

        self._restored = True
        await super().async_added_to_hass()

    @property
    def available(self) -> bool:
        """Сенсор доступен, только если устройство в сети и значение восстановлено."""
        return super().available and self._restored

    def update_energy_value(self, current_value: float):
        now = time.monotonic()
        elapsed_seconds = now - self._prev_ts

        # Гарантируем, что self._attr_native_value не None
        if self._attr_native_value is None:
            self._attr_native_value = 0.0

        if self._prev_power is not None:
            # Trapezoidal average power × dt (в часах)
            step_wh = (elapsed_seconds / 3600) * (self._prev_power + current_value) / 2
            ceiling = max_step_wh(elapsed_seconds)
            if 0 <= step_wh <= ceiling:
                self._attr_native_value += step_wh
            else:
                # Единичный битый сэмпл (CRC-валидный, но семантически невозможный)
                # обрывает трапецию: иначе он отравит и следующий тик через _prev_power.
                _LOGGER.warning(
                    "%s: trapezoidal step out of bounds "
                    "(%.1f Wh, ceiling %.1f Wh, prev=%.1f W, curr=%.1f W, dt=%.1fs); "
                    "dropping sample and resetting integrator state",
                    self.entity_id or self._attr_unique_id,
                    step_wh,
                    ceiling,
                    self._prev_power,
                    current_value,
                    elapsed_seconds,
                )
                self._prev_power = None
                self._prev_ts = now
                self.async_write_ha_state()
                return

        # Обновляем предыдущее значение мощности и время
        self._prev_power = current_value
        self._prev_ts = now
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Каждое обновление координатора — берём свежую мощность и накапливаем энергию."""
        section = self.data.get(self.data_section, {})
        raw = section.get(self.data_key)
        try:
            power = float(raw)
        except (TypeError, ValueError):
            power = None

        # Sanity-bound: a single sample within the trapezoidal step-guard's
        # 50 kW ceiling but still wildly above this inverter's actual rating
        # would slip past update_energy_value() and silently bloat the
        # accumulator. Reject upfront — keeps PV / InverterOut / Apparent
        # integrators honest the same way the battery integrators are.
        if power is not None and not is_plausible_power(power):
            _LOGGER.debug(
                "%s: implausible power reading (%.1f W); dropping sample",
                self.entity_id or self._attr_unique_id,
                power,
            )
            self._prev_power = None
            self._prev_ts = time.monotonic()
            self.async_write_ha_state()
            return

        if power is not None:
            # update_energy_value() writes the HA state internally; calling
            # async_write_ha_state() again here would double the event-loop
            # work per tick (HA logs "took 0.9s" when this stacks across
            # all energy sensors on a busy loop).
            self.update_energy_value(power)
        else:
            # No new sample — still publish so the accumulator stays visible.
            self.async_write_ha_state()


class DirectPVEnergySensor(DirectEnergySensorBase):
    """Энергия по мощности PV (qpigs['pv_charging_power'])."""

    def __init__(self, inverter_device, coordinator):
        super().__init__(
            inverter_device=inverter_device,
            coordinator=coordinator,
            data_section="qpigs",
            data_key="pv_charging_power",
            sensor_suffix="direct_pv_power_energy",
            name_suffix="PV Power Energy",
        )


class DirectPV2EnergySensor(DirectEnergySensorBase):
    """Энергия по мощности PV2 (qpigs2['pv_current']*['pv_voltage'])."""

    def __init__(self, inverter_device, coordinator):
        super().__init__(
            inverter_device=inverter_device,
            coordinator=coordinator,
            data_section="qpigs2",
            data_key="pv2_power",
            sensor_suffix="direct_pv2_power_energy",
            name_suffix="PV2 Power Energy",
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        try:
            sec = self.data["qpigs2"]
            current = float(sec["pv_current"])
            voltage = float(sec["pv_voltage"])
        except (KeyError, ValueError, TypeError):
            self._prev_power = None
            self._prev_ts = time.monotonic()
            self.async_write_ha_state()
            return

        power = current * voltage
        if not is_plausible_power(power):
            _LOGGER.debug(
                "%s: implausible PV2 reading (I=%.2f A, V=%.2f V, P=%.1f W); dropping sample",
                self.entity_id or self._attr_unique_id,
                current,
                voltage,
                power,
            )
            self._prev_power = None
            self._prev_ts = time.monotonic()
            self.async_write_ha_state()
            return

        # update_energy_value() already writes state; don't double-write.
        self.update_energy_value(power)


class DirectInverterOutputEnergySensor(DirectEnergySensorBase):
    """Энергия по мощности выхода инвертора (qpigs['output_active_power'])."""

    def __init__(self, inverter_device, coordinator):
        super().__init__(
            inverter_device=inverter_device,
            coordinator=coordinator,
            data_section="qpigs",
            data_key="output_active_power",
            sensor_suffix="direct_inverter_out_power_energy",
            name_suffix="Inverter Out Power Energy",
        )


class DirectOutputApparentEnergySensor(DirectEnergySensorBase):
    """Энергия по кажущейся мощности (qpigs['output_apparent_power'])."""

    def __init__(self, inverter_device, coordinator):
        super().__init__(
            inverter_device=inverter_device,
            coordinator=coordinator,
            data_section="qpigs",
            data_key="output_apparent_power",
            sensor_suffix="direct_output_apparent_power_energy",
            name_suffix="Apparent Power Energy",
        )


class DirectBatteryInEnergySensor(DirectEnergySensorBase):
    """Энергия по мощности зарядки батареи (battery_charging_current * battery_voltage)."""

    def __init__(self, inverter_device, coordinator):
        super().__init__(
            inverter_device=inverter_device,
            coordinator=coordinator,
            data_section="qpigs",
            data_key="battery_charging_current",
            sensor_suffix="battery_in_power_energy",
            name_suffix="Battery In Energy",
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        qpigs = self.data.get("qpigs", {})

        try:
            current_raw = qpigs.get("battery_charging_current")
            # Use the live terminal voltage from QPIGS, not the static
            # bulk_charging_voltage setpoint from QPIRI. Reasons:
            #  - Physical correctness: P = I × V_live. The bulk setpoint
            #    (e.g. 28.4 V) overstates power during bulk-rise (V_live
            #    is 26-28 V) and understates during float (V_live ~27.2 V).
            #  - Reliability: qpiri can be empty for a tick after a CRC
            #    fail / coordinator freeze, while qpigs already has the
            #    full reading. Reading both forced the sensor to drop
            #    valid charge samples whenever qpiri momentarily lagged,
            #    causing Battery IN Energy to chronically undercount vs
            #    Battery OUT (round-trip > 100% in the stats).
            voltage_raw = qpigs.get("battery_voltage")
            if current_raw is None or voltage_raw is None:
                raise ValueError("no data")
            current = float(current_raw)
            voltage = float(voltage_raw)
            if math.isnan(current) or math.isnan(voltage):
                raise ValueError("NaN")
            # All-zeros == bridge offline / empty payload — skip silently.
            if current == 0.0 and voltage == 0.0:
                raise ValueError("no data")
            if not is_plausible_battery_current(current) or not is_plausible_battery_voltage(voltage):
                _LOGGER.debug(
                    "%s: implausible reading (I=%.2f A, V=%.2f V); dropping sample",
                    self.entity_id or self._attr_unique_id,
                    current,
                    voltage,
                )
                raise ValueError("out of plausible range")
        except (KeyError, ValueError, TypeError):
            self._prev_power = None
            self._prev_ts = time.monotonic()
            self.async_write_ha_state()
            return
        if current > 0:
            power = current * voltage
        else:
            power = 0.0

        # update_energy_value() already writes state; don't double-write.
        self.update_energy_value(power)


class DirectBatteryOutEnergySensor(DirectEnergySensorBase):
    """Энергия по мощности разрядки батареи (battery_discharge_current * battery_voltage)."""

    def __init__(self, inverter_device, coordinator):
        super().__init__(
            inverter_device=inverter_device,
            coordinator=coordinator,
            data_section="qpigs",
            data_key="battery_discharge_current",
            sensor_suffix="battery_out_power_energy",
            name_suffix="Battery Out Energy",
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        qpigs = self.data.get("qpigs", {})
        try:
            current_raw = qpigs.get("battery_discharge_current")
            voltage_raw = qpigs.get("battery_voltage")
            if current_raw is None or voltage_raw is None:
                raise ValueError("no data")
            current = float(current_raw)
            voltage = float(voltage_raw)
            if math.isnan(current) or math.isnan(voltage):
                raise ValueError("NaN")
            # All-zeros == bridge offline / empty payload — skip silently.
            if current == 0.0 and voltage == 0.0:
                raise ValueError("no data")
            if not is_plausible_battery_current(current) or not is_plausible_battery_voltage(voltage):
                _LOGGER.debug(
                    "%s: implausible reading (I=%.2f A, V=%.2f V); dropping sample",
                    self.entity_id or self._attr_unique_id,
                    current,
                    voltage,
                )
                raise ValueError("out of plausible range")
        except (KeyError, ValueError, TypeError):
            self._prev_power = None
            self._prev_ts = time.monotonic()
            self.async_write_ha_state()
            return
        power = current * voltage
        if power <= 0:
            power = 0.0
        # update_energy_value() already writes state; don't double-write.
        self.update_energy_value(power)


class BatteryStoredData(ExtraStoredData):

    def __init__(
        self,
        native_value: float | None,
        accumulated_charge_ah: float,
        last_sync_at: datetime | None = None,
    ):
        self.native_value = native_value
        self.accumulated_charge_ah = accumulated_charge_ah
        self.last_sync_at = last_sync_at

    def as_dict(self) -> dict:
        return {
            "native_value": self.native_value,
            "accumulated_charge_ah": self.accumulated_charge_ah,
            "last_sync_at": (
                self.last_sync_at.isoformat() if self.last_sync_at else None
            ),
        }


class DirectBatteryStateOfChargeSensor(RestoreSensor, DirectTypedSensorBase):
    _attr_device_class = SensorDeviceClass.BATTERY
    # MEASUREMENT enables HA long-term statistics, which write a row every
    # 5 minutes regardless of state-change. Without it, when SoC pegs at
    # 100% (battery full) or 0% (drained) and stays there, ``last_changed``
    # freezes and the History card / Apex / mini-graph show no further
    # points — looking exactly like the sensor died.
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1
    # Emit state_changed on every coordinator tick even when the value is
    # unchanged. Cost: ~8640 extra Recorder rows/day per sensor at the
    # default 10-second poll — acceptable for one SoC sensor and crucial
    # for short-window (history-graph) cards that don't extrapolate flat
    # state forward.
    _attr_force_update = True

    def __init__(self, inverter_device, coordinator, hass):
        super().__init__(
            inverter_device=inverter_device,
            coordinator=coordinator,
            data_section="qpigs",
            data_key="battery_voltage",
            sensor_suffix="battery_state_of_charge",
            name_suffix="Battery State of Charge",
        )
        # All the Coulomb-counting / snap / deadband state and logic lives
        # in the HA-free estimator (unit-tested in tests/test_soc_core.py).
        # This entity just resolves HA-bound inputs and copies results out.
        self._estimator = SocEstimator()
        self._restored = False
        self._hass = hass

        device_slug = slugify(self._inverter_device.name)
        # The "_ah" suffix matches the new BatteryCapacityNumber name
        # "vSoC Battery Capacity (Ah)" — HA slugifies that to
        # "vsoc_battery_capacity_ah". The legacy Wh-based entity at
        # "{slug}_vsoc_battery_capacity" stays orphan after upgrade and
        # is not tracked here.
        self._capacity_entity_id = f"number.{device_slug}_vsoc_battery_capacity_ah"
        self._sync_voltage_entity_id = (
            f"number.{device_slug}_vsoc_full_charge_sync_voltage"
        )
        self._battery_mode_entity_id = f"select.{device_slug}_vsoc_battery_mode"
        # Float-mode deadband controls (switch + two numbers). Defaults
        # mirror the historical hardcoded constants so the feature is a
        # no-op until the user touches the entities.
        self._float_deadband_switch_id = (
            f"switch.{device_slug}_vsoc_float_deadband"
        )
        self._float_voltage_window_id = (
            f"number.{device_slug}_vsoc_float_voltage_window"
        )
        self._float_noise_floor_id = (
            f"number.{device_slug}_vsoc_float_noise_floor"
        )
        # User-defined override for the SoC snap-to-100% voltage. 0 = use
        # inverter's bulk_charging_voltage. Resolution stays in the entity
        # (it reads HA state); the resolved value is passed into the
        # estimator on each tick.
        self._full_charge_sync_voltage = 0.0

        async_track_state_change_event(
            self._hass,
            [self._capacity_entity_id],
            self._handle_battery_capacity_change,
        )
        async_track_state_change_event(
            self._hass,
            [self._sync_voltage_entity_id],
            self._handle_sync_voltage_change,
        )
        async_track_state_change_event(
            self._hass,
            [self._battery_mode_entity_id],
            self._handle_battery_mode_change,
        )
        async_track_state_change_event(
            self._hass,
            [
                self._float_deadband_switch_id,
                self._float_voltage_window_id,
                self._float_noise_floor_id,
            ],
            self._handle_float_deadband_change,
        )

    async def async_added_to_hass(self) -> None:
        last_extra = await self.async_get_last_extra_data()
        if last_extra is not None:
            data = last_extra.as_dict()
            restored_value = data.get("native_value")
            soc = float(restored_value) if restored_value is not None else 100.0
            # Pre-Ah saves used "accumulated_energy_wh" — that value is in
            # Wh, not Ah, so it's not meaningfully restorable. Fall back to
            # 0 and let the next full-charge snap re-anchor the integrator.
            accumulated = float(data.get("accumulated_charge_ah", 0))
            # Restore the last-snap timestamp so the diagnostic survives
            # HA restarts. ``fromisoformat`` raises on malformed input —
            # tolerate by leaving the timestamp as None.
            last_sync = None
            iso = data.get("last_sync_at")
            if isinstance(iso, str) and iso:
                try:
                    last_sync = datetime.fromisoformat(iso)
                except ValueError:
                    last_sync = None
        else:
            soc = 100.0
            accumulated = 0.0
            last_sync = None

        self._estimator.restore(
            soc_percent=soc,
            accumulated_charge_ah=accumulated,
            last_sync_at=last_sync,
        )
        self._attr_native_value = soc

        state = self._hass.states.get(self._capacity_entity_id)
        self._update_battery_capacity_from_state(state)

        sync_state = self._hass.states.get(self._sync_voltage_entity_id)
        self._update_sync_voltage_from_state(sync_state)

        mode_state = self._hass.states.get(self._battery_mode_entity_id)
        self._update_battery_mode_from_state(mode_state)

        # Float-deadband controls — read whatever's already restored.
        self._refresh_float_deadband_config()

        self._restored = True
        await super().async_added_to_hass()

    async def async_get_extra_data(self) -> ExtraStoredData:
        """Сохранение данных при выгрузке / рестарте."""
        return BatteryStoredData(
            self._attr_native_value,
            self._estimator.accumulated_charge_ah,
            self._estimator.last_sync_at,
        )

    @property
    def available(self) -> bool:
        # Доступен только если восстановлен и емкость задана положительно.
        # Допустимый порог snap-to-100% есть либо от инвертора (bulk), либо
        # от пользовательского override — нужно хоть что-то одно.
        sync_voltage = self.get_full_charge_sync_voltage()
        capacity = self._estimator.capacity_ah
        return super().available and self._restored and (
                capacity is not None and capacity > 0) and (
                sync_voltage is not None)

    @callback
    def _handle_battery_capacity_change(self, event):
        state = event.data.get("new_state")
        self._update_battery_capacity_from_state(state)

    def _update_battery_capacity_from_state(self, state):
        # Parse the capacity number entity's HA state and push it into the
        # estimator. The proportional SoC-preserving rescale lives in
        # SocEstimator.set_capacity(); missing / invalid / ≤0 -> None marks
        # the sensor unavailable until a valid capacity is entered.
        if state is None or state.state in ("unknown", "unavailable", None):
            self._estimator.set_capacity(None)
            self.async_write_ha_state()
            return
        try:
            value = float(state.state)
        except (ValueError, TypeError):
            self._estimator.set_capacity(None)
            self.async_write_ha_state()
            return

        self._estimator.set_capacity(value)
        if self._estimator.capacity_ah is not None and self._estimator.soc_percent is not None:
            self._attr_native_value = self._estimator.soc_percent
        self.async_write_ha_state()

    def get_bulk_charging_voltage(self) -> float | None:
        try:
            qpiri = self.data.get("qpiri", {})
            voltage = float(qpiri.get("bulk_charging_voltage"))
            if voltage > 0:
                return voltage
        except (KeyError, ValueError, TypeError):
            pass
        return None

    def get_full_charge_sync_voltage(self) -> float | None:
        """Threshold at which SoC snaps to 100%.

        User override (FullChargeSyncVoltageNumber > 0) takes precedence,
        otherwise we fall back to the inverter's configured bulk voltage.
        Returns None only when neither source is available — that's when
        the SoC sensor reports unavailable.
        """
        if self._full_charge_sync_voltage > 0:
            return self._full_charge_sync_voltage
        return self.get_bulk_charging_voltage()

    @callback
    def _handle_sync_voltage_change(self, event):
        state = event.data.get("new_state")
        self._update_sync_voltage_from_state(state)

    def _update_sync_voltage_from_state(self, state):
        if state is None or state.state in ("unknown", "unavailable", None):
            self._full_charge_sync_voltage = 0.0
            return
        try:
            value = float(state.state)
        except (ValueError, TypeError):
            self._full_charge_sync_voltage = 0.0
            return
        self._full_charge_sync_voltage = max(0.0, value)

    @callback
    def _handle_battery_mode_change(self, event):
        state = event.data.get("new_state")
        self._update_battery_mode_from_state(state)

    def _update_battery_mode_from_state(self, state):
        if state is None:
            return
        # set_mode ignores unknown values (so a transient 'unknown' during
        # restart is a no-op) and resets the snap debounce only on a real
        # chemistry change.
        self._estimator.set_mode(state.state)

    @callback
    def _handle_float_deadband_change(self, event):
        # Any of the three float-deadband controls changed — re-read all.
        self._refresh_float_deadband_config()

    def _refresh_float_deadband_config(self) -> None:
        """Pull current values of the float-deadband switch + numbers and
        push them into the estimator.

        Each control falls back to its historical default when the
        entity is missing / unavailable, so the deadband keeps working
        exactly as before for users who never touch the new controls."""
        enabled = True
        switch_state = self._hass.states.get(self._float_deadband_switch_id)
        if switch_state is not None and switch_state.state in ("on", "off"):
            enabled = switch_state.state == "on"

        window = _FLOAT_VOLTAGE_WINDOW_V
        window_state = self._hass.states.get(self._float_voltage_window_id)
        if window_state is not None and window_state.state not in (
            "unknown", "unavailable", None
        ):
            try:
                window = max(0.0, float(window_state.state))
            except (ValueError, TypeError):
                window = _FLOAT_VOLTAGE_WINDOW_V

        noise_floor = _FLOAT_NOISE_FLOOR_A
        floor_state = self._hass.states.get(self._float_noise_floor_id)
        if floor_state is not None and floor_state.state not in (
            "unknown", "unavailable", None
        ):
            try:
                noise_floor = max(0.0, float(floor_state.state))
            except (ValueError, TypeError):
                noise_floor = _FLOAT_NOISE_FLOOR_A

        self._estimator.set_deadband(
            enabled=enabled, window=window, noise_floor=noise_floor
        )

    def get_floating_charging_voltage(self) -> float | None:
        try:
            qpiri = self.data.get("qpiri", {})
            voltage = float(qpiri.get("float_charging_voltage"))
            if voltage > 0:
                return voltage
        except (KeyError, ValueError, TypeError):
            pass
        return None

    @property
    def capacity_ah(self) -> float | None:
        """Expose the user-set capacity to downstream sensors (time-to-* etc.)."""
        return self._estimator.capacity_ah

    @property
    def last_sync_at(self) -> datetime | None:
        """Wall-clock UTC moment of the most recent snap-to-100% event."""
        return self._estimator.last_sync_at

    def update_soc(self, signed_current_a: float, current_voltage: float):
        """Advance the SoC estimator and publish the result.

        Resolves the HA-bound inputs (snap voltage from the override
        number / inverter bulk, float voltage from QPIRI, BMS SoC from
        QPIGS) and hands them — plus injected wall/monotonic time — to the
        pure ``SocEstimator``. All the algorithm (Coulomb counting, float
        deadband, snap-to-100%, integral-windup-safe debounce) lives there
        and is unit-tested in tests/test_soc_core.py.
        """
        sync_voltage = self.get_full_charge_sync_voltage()
        floating_voltage = self.get_floating_charging_voltage()
        bms_soc = (
            self._read_bms_soc()
            if self._estimator.mode == BATTERY_MODE_LI_BMS
            else None
        )

        result = self._estimator.update(
            signed_current_a=signed_current_a,
            voltage=current_voltage,
            now=time.monotonic(),
            sync_voltage=sync_voltage,
            floating_voltage=floating_voltage,
            bms_soc=bms_soc,
            wall_now=_wall_now(),
        )

        self._attr_native_value = result
        self.async_write_ha_state()

    def _read_bms_soc(self) -> float | None:
        """Read battery_capacity (BMS-sourced SoC %) from the latest qpigs.

        Returns None when the value is missing, unparseable, or sentinel
        (some inverters emit 0 or 100 as placeholders before the BMS
        finishes handshake).
        """
        try:
            section = self.data.get(self.data_section, {})
            raw = section.get("battery_capacity")
            if raw is None or raw == "":
                return None
            value = float(raw)
            if math.isnan(value):
                return None
            return max(0.0, min(100.0, value))
        except (KeyError, ValueError, TypeError):
            return None

    @property
    def native_value(self):
        return self._attr_native_value

    @callback
    def _handle_coordinator_update(self) -> None:
        section = self.data.get(self.data_section, {})
        try:
            current_voltage = float(section.get("battery_voltage", 0))
            charging_current = float(section.get("battery_charging_current", 0))
            discharging_current = float(section.get("battery_discharge_current", 0))
            # Signed: + charging, − discharging. Both fields shouldn't be
            # non-zero simultaneously per protocol — but if they are, the
            # net direction is what we want.
            signed_current_a = charging_current - discharging_current

            self.update_soc(signed_current_a, current_voltage)
        except (KeyError, ValueError, TypeError):
            self._attr_native_value = None
            self.async_write_ha_state()


# ---------------------------------------------------------------------------
# Time-to-* sensors. Project the current battery activity rate onto the SoC
# bracket to estimate how long until the battery reaches a target percentage.
#
# Math is straightforward Coulomb-counting (kept consistent with vSoC):
#
#     hours = (Δsoc / 100) × capacity_ah / current_a
#
# where ``current_a`` is the abs() of the active direction (discharge for
# floor-eta, charge for full-eta). Reads SoC and capacity from the live
# vSoC sensor instance so all derived numbers share one source of truth —
# if the user adjusts capacity mid-day, both update at the same tick.
#
# Behavior summary:
#   * Sensor returns ``None`` (→ unknown) when the relevant direction
#     isn't active (battery idle, or charging when we're computing
#     time-to-empty).
#   * Sensor returns ``0`` when the target is already met (already at
#     floor / already at 100%).
#   * Otherwise: hours, rounded to 3 decimals.
# ---------------------------------------------------------------------------


# Below this current threshold (Amperes) we don't bother computing a time
# estimate — the result would either be misleading huge or jitter wildly
# on small parasitic load / charge values that don't reflect a real
# discharge or charge cycle.
_IDLE_CURRENT_THRESHOLD_A = 0.1


class _TimeEstimateBase(DirectSensorBase):
    """Common scaffolding for the time-to-* sensors."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, inverter_device, coordinator, soc_sensor):
        super().__init__(inverter_device, coordinator)
        self._soc_sensor = soc_sensor

    def _read_current_a(self, key: str) -> float:
        try:
            section = self.data.get("qpigs", {})
            return float(section.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0


class DirectBatteryTimeToFloorSensor(_TimeEstimateBase):
    """Hours until vSoC reaches the user-configured discharge floor.

    Reads the floor percentage from the ``vSoC Discharge Floor`` number
    entity on every tick rather than tracking state-change events — keeps
    the implementation tiny and the floor isn't latency-sensitive.
    """

    _attr_icon = "mdi:battery-clock"

    def __init__(self, inverter_device, coordinator, soc_sensor, hass):
        super().__init__(inverter_device, coordinator, soc_sensor)
        self._hass = hass
        self._attr_unique_id = (
            f"{inverter_device.inverter_id}_time_to_floor"
        )
        self._attr_name = (
            f"{inverter_device.name} vSoC Time to Discharge Floor"
        )
        device_slug = slugify(inverter_device.name)
        self._floor_entity_id = f"number.{device_slug}_vsoc_discharge_floor"

    def _read_target_floor(self) -> float:
        state = self._hass.states.get(self._floor_entity_id)
        if state is None or state.state in ("unknown", "unavailable", None):
            return 15.0  # match DischargeFloorSoCNumber default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return 15.0

    @callback
    def _handle_coordinator_update(self) -> None:
        soc = self._soc_sensor.native_value
        capacity_ah = self._soc_sensor.capacity_ah
        discharge_a = self._read_current_a("battery_discharge_current")
        target = self._read_target_floor()

        if soc is None or capacity_ah is None or capacity_ah <= 0:
            self._attr_native_value = None
        elif discharge_a < _IDLE_CURRENT_THRESHOLD_A:
            # Not discharging — no meaningful ETA.
            self._attr_native_value = None
        elif soc <= target:
            # Already at or below floor.
            self._attr_native_value = 0.0
        else:
            ah_remaining = (soc - target) / 100.0 * capacity_ah
            hours = ah_remaining / discharge_a
            self._attr_native_value = round(hours, 3)

        self.async_write_ha_state()


class DirectBatteryTimeToFullSensor(_TimeEstimateBase):
    """Hours until vSoC reaches 100% at the current charging rate."""

    _attr_icon = "mdi:battery-charging-100"

    def __init__(self, inverter_device, coordinator, soc_sensor):
        super().__init__(inverter_device, coordinator, soc_sensor)
        self._attr_unique_id = (
            f"{inverter_device.inverter_id}_time_to_full"
        )
        self._attr_name = f"{inverter_device.name} vSoC Time to Full"

    @callback
    def _handle_coordinator_update(self) -> None:
        soc = self._soc_sensor.native_value
        capacity_ah = self._soc_sensor.capacity_ah
        charge_a = self._read_current_a("battery_charging_current")

        if soc is None or capacity_ah is None or capacity_ah <= 0:
            self._attr_native_value = None
        elif charge_a < _IDLE_CURRENT_THRESHOLD_A:
            self._attr_native_value = None
        elif soc >= 100:
            self._attr_native_value = 0.0
        else:
            ah_needed = (100.0 - soc) / 100.0 * capacity_ah
            hours = ah_needed / charge_a
            self._attr_native_value = round(hours, 3)

        self.async_write_ha_state()


class DirectBatteryBackupTimeSensor(_TimeEstimateBase):
    """Hours of backup runtime at the current AC load.

    Answers the question "if the grid drops *right now*, how long will the
    battery last?" — unlike ``Time to Discharge Floor`` which uses the
    actual battery discharge current (so reads None while the system is
    grid-tied or charging), this sensor synthesises the discharge by
    projecting the *current AC load* (output_active_power) onto the
    battery, less whatever PV is contributing (PV typically keeps working
    during a grid outage on hybrid inverters).

    Calculation:

        equivalent_discharge_a = max(0, (load_w − pv_w)) / battery_voltage
        hours = (soc − floor) / 100 × capacity_ah / equivalent_discharge_a

    Caveats:
      * Inverter conversion losses (~5-8%) are ignored, so this slightly
        overestimates runtime. Treat the number as an upper bound for
        planning, not a precise prediction.
      * If PV is currently producing more than the load, this returns
        ``None`` (battery would actually be charging during the outage —
        runtime is effectively infinite).
      * If load is essentially zero, also ``None`` (no meaningful ETA).
    """

    _attr_icon = "mdi:home-battery"

    def __init__(self, inverter_device, coordinator, soc_sensor, hass):
        super().__init__(inverter_device, coordinator, soc_sensor)
        self._hass = hass
        self._attr_unique_id = (
            f"{inverter_device.inverter_id}_backup_time"
        )
        self._attr_name = (
            f"{inverter_device.name} vSoC Backup Time at Current Load"
        )
        device_slug = slugify(inverter_device.name)
        self._floor_entity_id = f"number.{device_slug}_vsoc_discharge_floor"

    def _read_target_floor(self) -> float:
        state = self._hass.states.get(self._floor_entity_id)
        if state is None or state.state in ("unknown", "unavailable", None):
            return 15.0
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return 15.0

    @callback
    def _handle_coordinator_update(self) -> None:
        soc = self._soc_sensor.native_value
        capacity_ah = self._soc_sensor.capacity_ah
        target = self._read_target_floor()

        try:
            section = self.data.get("qpigs", {})
            load_w = float(section.get("output_active_power", 0) or 0)
            pv_w = float(section.get("pv_charging_power", 0) or 0)
            v_bat = float(section.get("battery_voltage", 0) or 0)
        except (TypeError, ValueError):
            self._attr_native_value = None
            self.async_write_ha_state()
            return

        # Net power the battery would have to supply if the grid dropped
        # right now. PV stays online during grid outage on hybrid systems,
        # so subtract it. Clamp to zero so we never produce a negative
        # equivalent current (PV oversupply → no discharge).
        net_load_w = max(0.0, load_w - pv_w)
        equivalent_discharge_a = (
            net_load_w / v_bat if v_bat > 0 else 0.0
        )

        if soc is None or capacity_ah is None or capacity_ah <= 0:
            self._attr_native_value = None
        elif equivalent_discharge_a < _IDLE_CURRENT_THRESHOLD_A:
            # PV covers the load (or load near zero) — no meaningful ETA.
            self._attr_native_value = None
        elif soc <= target:
            self._attr_native_value = 0.0
        else:
            ah_remaining = (soc - target) / 100.0 * capacity_ah
            hours = ah_remaining / equivalent_discharge_a
            self._attr_native_value = round(hours, 3)

        self.async_write_ha_state()


class DirectBatteryVSocLastSyncSensor(DirectSensorBase):
    """Diagnostic: wall-clock timestamp of the last vSoC snap-to-100% event.

    Why this matters: Coulomb counting drifts gradually (small current
    measurement errors integrate). The trapezoid only self-calibrates
    when the battery reaches a real "full" state and the snap fires.
    If the user never lets the bank fully charge (e.g., heavy daily
    cycling that tops out at 90%), drift accumulates indefinitely.

    This sensor surfaces the staleness of the last calibration anchor
    as a TIMESTAMP. HA's UI auto-renders it as "X ago"; automations can
    compare against ``now()`` for "alert if no sync for >48h".

    State is ``None`` until the first snap fires (or the first successful
    extra-state restore after upgrade).
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:battery-sync-outline"

    def __init__(self, inverter_device, coordinator, soc_sensor):
        super().__init__(inverter_device, coordinator)
        self._soc_sensor = soc_sensor
        self._attr_unique_id = (
            f"{inverter_device.inverter_id}_vsoc_last_sync"
        )
        self._attr_name = (
            f"{inverter_device.name} vSoC Last Full Charge Sync"
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_native_value = self._soc_sensor.last_sync_at
        self.async_write_ha_state()
