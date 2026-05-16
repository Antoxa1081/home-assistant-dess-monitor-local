import logging
import math
import time

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
from custom_components.dess_monitor_local.sensors.direct_sensor import (
    DirectSensorBase,
    DirectTypedSensorBase,
)

_LOGGER = logging.getLogger(__name__)


# Battery chemistry / connection presets driving the SoC algorithm.
# Keep names in sync with select.BATTERY_MODE_* constants — duplicated here
# rather than imported to keep this module free of select-platform deps.
BATTERY_MODE_LI_VOLTAGE = "Lithium (Voltage)"
BATTERY_MODE_LI_BMS = "Lithium (BMS)"
BATTERY_MODE_LEAD_ACID = "Lead-acid"

# Per-mode tuning. Since the integrator now operates on Ah (Coulomb
# counting) instead of Wh, ``charge_eff`` reflects *Coulombic* efficiency:
# how much charge actually stays in the cells per Ah pushed in. LFP is
# nearly lossless at the Coulomb level (~99%); voltage hysteresis between
# charge and discharge curves accounts for most of the LFP round-trip loss
# but it's a *voltage* effect, not a Coulomb effect, so it disappears here.
# Lead-acid loses real Coulombs to gassing and self-discharge — hence the
# lower factor. ``tail_c_rate`` is the absorption-tail current threshold
# as a fraction of nominal capacity (0.05C = 5 A on a 100 Ah bank). Lead
# needs a tighter tail (0.02C) because absorption is slower and we want
# to wait until current really tapers off.
_CHEMISTRY_PARAMS = {
    BATTERY_MODE_LI_VOLTAGE: {
        "charge_eff": 0.99,
        "discharge_eff": 1.0,
        "tail_c_rate": 0.05,
    },
    BATTERY_MODE_LEAD_ACID: {
        "charge_eff": 0.90,
        "discharge_eff": 0.95,
        "tail_c_rate": 0.02,
    },
    # BMS mode bypasses the integrator entirely — params unused.
    BATTERY_MODE_LI_BMS: {
        "charge_eff": 1.0,
        "discharge_eff": 1.0,
        "tail_c_rate": 0.05,
    },
}

# Snap-to-100% only fires after this many consecutive ticks at/above the
# sync voltage. With the default 10s poll interval that's ~30 sec, which
# safely filters transient voltage spikes from load steps or parser glitches
# while staying short enough that real absorption phase always trips it.
_SYNC_DEBOUNCE_TICKS = 3


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
            self.update_energy_value(power)

        # Обновляем state (даже если power оказался None, рисуем текущее значение накопленной энергии)
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

        self.update_energy_value(power)
        self.async_write_ha_state()


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
        qpiri = self.data.get("qpiri", {})

        try:
            current_raw = qpigs.get("battery_charging_current")
            voltage_raw = qpiri.get("bulk_charging_voltage")
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

        self.update_energy_value(power)
        self.async_write_ha_state()


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
        self.update_energy_value(power)
        self.async_write_ha_state()


class BatteryStoredData(ExtraStoredData):

    def __init__(self, native_value: float | None, accumulated_charge_ah: float):
        self.native_value = native_value
        self.accumulated_charge_ah = accumulated_charge_ah

    def as_dict(self) -> dict:
        return {
            "native_value": self.native_value,
            "accumulated_charge_ah": self.accumulated_charge_ah,
        }


class DirectBatteryStateOfChargeSensor(RestoreSensor, DirectTypedSensorBase):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1

    def __init__(self, inverter_device, coordinator, hass):
        super().__init__(
            inverter_device=inverter_device,
            coordinator=coordinator,
            data_section="qpigs",
            data_key="battery_voltage",
            sensor_suffix="battery_state_of_charge",
            name_suffix="Battery State of Charge",
        )
        # Coulomb counter: accumulated *charge* in Ah, not energy in Wh.
        # SoC% = (_accumulated_charge_ah / _battery_capacity_ah) × 100.
        self._accumulated_charge_ah = 0.0
        # Latest signed current (A): + charging, − discharging.
        self._prev_current_a = None
        # Efficiency-adjusted twin of _prev_current_a used by the integrator.
        # Kept separate so the raw value stays available for diagnostics.
        self._prev_effective_current_a = None
        self._prev_ts = time.monotonic()
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
        self._battery_capacity_ah = None
        # User-defined override for the SoC snap-to-100% voltage. 0 = use
        # inverter's bulk_charging_voltage. See FullChargeSyncVoltageNumber
        # for the rationale (LiFePO4 chemistries reach 100% below the
        # inverter's bulk target).
        self._full_charge_sync_voltage = 0.0
        # Active chemistry preset; falls back to the LFP-voltage strategy
        # whenever the select hasn't restored yet — that matches existing
        # behavior so the upgrade is a no-op for users who don't touch
        # the mode dropdown.
        self._battery_mode = BATTERY_MODE_LI_VOLTAGE
        # Debounce counter for the voltage-based snap (trigger A). Counts
        # consecutive ticks at/above sync_voltage; resets when V falls
        # below sync_voltage − hysteresis_v.
        self._at_sync_ticks = 0

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

    async def async_added_to_hass(self) -> None:
        last_extra = await self.async_get_last_extra_data()
        if last_extra is not None:
            data = last_extra.as_dict()
            restored_value = data.get("native_value")
            self._attr_native_value = float(restored_value) if restored_value is not None else 100.0
            # Pre-Ah saves used "accumulated_energy_wh" — that value is in
            # Wh, not Ah, so it's not meaningfully restorable. Fall back to
            # 0 and let the next full-charge snap re-anchor the integrator.
            self._accumulated_charge_ah = float(data.get("accumulated_charge_ah", 0))
        else:
            self._attr_native_value = 100.0
            self._accumulated_charge_ah = 0.0

        state = self._hass.states.get(self._capacity_entity_id)
        self._update_battery_capacity_from_state(state)

        sync_state = self._hass.states.get(self._sync_voltage_entity_id)
        self._update_sync_voltage_from_state(sync_state)

        mode_state = self._hass.states.get(self._battery_mode_entity_id)
        self._update_battery_mode_from_state(mode_state)

        self._restored = True
        await super().async_added_to_hass()

    async def async_get_extra_data(self) -> ExtraStoredData:
        """Сохранение данных при выгрузке / рестарте."""
        return BatteryStoredData(
            self._attr_native_value,
            self._accumulated_charge_ah,
        )

    @property
    def available(self) -> bool:
        # Доступен только если восстановлен и емкость задана положительно.
        # Допустимый порог snap-to-100% есть либо от инвертора (bulk), либо
        # от пользовательского override — нужно хоть что-то одно.
        sync_voltage = self.get_full_charge_sync_voltage()
        return super().available and self._restored and (
                self._battery_capacity_ah is not None and self._battery_capacity_ah > 0) and (
                sync_voltage is not None)

    @callback
    def _handle_battery_capacity_change(self, event):
        state = event.data.get("new_state")
        self._update_battery_capacity_from_state(state)

    def _update_battery_capacity_from_state(self, state):
        # Ёмкость ещё не доступна (например, number-сущность не восстановилась
        # на старте HA). Не трогаем восстановленный _attr_native_value и
        # _accumulated_energy_wh — сенсор просто станет недоступен, пока
        # ёмкость не появится.
        if state is None or state.state in ("unknown", "unavailable", None):
            self._battery_capacity_ah = None
            self.async_write_ha_state()
            return
        try:
            value = float(state.state)
        except (ValueError, TypeError):
            self._battery_capacity_ah = None
            self.async_write_ha_state()
            return

        if value <= 0:
            self._battery_capacity_ah = None
            self.async_write_ha_state()
            return

        old_capacity = self._battery_capacity_ah
        self._battery_capacity_ah = value

        if old_capacity is None:
            # Ёмкость появилась впервые (или после restart). Сохраняем SoC в
            # процентах: пересчитываем _accumulated_energy_wh из restored SoC,
            # чтобы не потерять значение после перезапуска HA.
            if self._attr_native_value is not None:
                soc_fraction = max(0.0, min(1.0, float(self._attr_native_value) / 100.0))
                self._accumulated_charge_ah = soc_fraction * value
            else:
                self._accumulated_charge_ah = value
                self._attr_native_value = 100.0
        else:
            # Пользователь изменил ёмкость батареи — сохраняем процент SoC
            # (пропорционально пересчитываем накопленную энергию).
            if old_capacity > 0:
                soc_fraction = self._accumulated_charge_ah / old_capacity
            elif self._attr_native_value is not None:
                soc_fraction = float(self._attr_native_value) / 100.0
            else:
                soc_fraction = 1.0
            soc_fraction = max(0.0, min(1.0, soc_fraction))
            self._accumulated_charge_ah = soc_fraction * value
            self._attr_native_value = soc_fraction * 100.0

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
        if state is None or state.state not in _CHEMISTRY_PARAMS:
            # Keep current mode; don't flip back to default on transient
            # 'unknown' states during HA restart.
            return
        if state.state != self._battery_mode:
            # Reset the snap debounce so a mode flip doesn't carry over
            # stale "near sync" state from the previous chemistry.
            self._at_sync_ticks = 0
        self._battery_mode = state.state

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
        return self._battery_capacity_ah

    def update_soc(self, signed_current_a: float, current_voltage: float):
        """Coulomb-counting SoC update.

        Args:
            signed_current_a: + when charging, − when discharging, A.
            current_voltage: terminal voltage for the snap triggers.
        """
        if self._battery_capacity_ah is None or self._battery_capacity_ah <= 0:
            # Емкость не задана — сенсор unavailable.
            self._attr_native_value = None
            self.async_write_ha_state()
            return

        max_capacity_ah = self._battery_capacity_ah
        params = _CHEMISTRY_PARAMS[self._battery_mode]

        # ---------- BMS mirror branch ---------------------------------
        # COMM-LI: QPIGS.battery_capacity carries the BMS-reported SoC %.
        # Use it verbatim and keep the integrator state aligned with it —
        # mode switch back to voltage tracking then starts from a sane
        # Coulomb baseline instead of stale junk.
        if self._battery_mode == BATTERY_MODE_LI_BMS:
            bms_soc = self._read_bms_soc()
            if bms_soc is not None:
                self._accumulated_charge_ah = (bms_soc / 100.0) * max_capacity_ah
                # Null the effective-current twin so the first tick after
                # the user flips back to voltage mode skips the trapezoid
                # (no stale baseline → no spurious step).
                self._prev_current_a = signed_current_a
                self._prev_effective_current_a = None
                self._prev_ts = time.monotonic()
                self._at_sync_ticks = 0
                self._attr_native_value = bms_soc
                self.async_write_ha_state()
                return
            # BMS read miss — fall through to integrator so the sensor
            # doesn't go dark on a transient handshake glitch.

        # ---------- Voltage-based branch (Lithium voltage / Lead-acid) --
        sync_voltage = self.get_full_charge_sync_voltage()
        floating_voltage = self.get_floating_charging_voltage()
        if sync_voltage is None:
            self._attr_native_value = None
            self.async_write_ha_state()
            return

        now = time.monotonic()
        elapsed_seconds = now - self._prev_ts

        # Coulombic efficiency. LFP is nearly lossless at the Coulomb
        # level (≈0.99 charge / 1.0 discharge); lead loses real charge
        # to gassing on the charge side and to self-discharge on the
        # discharge side. Keeping the factors split lets the integrator
        # stay signed and symmetric.
        if signed_current_a >= 0:
            effective_current_a = signed_current_a * params["charge_eff"]
        else:
            effective_current_a = signed_current_a / params["discharge_eff"]

        if self._prev_effective_current_a is not None:
            # Trapezoidal Coulomb count: ΔAh = avg(I) × Δt (hours).
            charge_increment = (elapsed_seconds / 3600) * (
                self._prev_effective_current_a + effective_current_a
            ) / 2
            self._accumulated_charge_ah += charge_increment

        self._prev_current_a = signed_current_a
        self._prev_effective_current_a = effective_current_a
        self._prev_ts = now

        # ---------- Snap-to-100% --------------------------------------
        # Battery is genuinely full only when BOTH conditions hold:
        #   (a) terminal voltage has reached the absorption / float setpoint
        #       (CV phase entered), AND
        #   (b) charge current has tapered to the absorption-tail threshold
        #       (battery can no longer accept full current at this voltage).
        #
        # Earlier revisions snapped on (a) alone, but for LFP that transition
        # happens at ~80-90% real SoC — the remaining 10-20% comes from the
        # CV tail. Voltage-only snap caused jumps like 70 → 100% the moment
        # bulk_voltage was hit.
        #
        # The voltage threshold uses min(sync, float) because the charger
        # can reach "full" via either path: stayed at bulk until tail
        # (absorption complete), or already dropped to float and tail.
        # Either way, voltage at-or-above whichever is *lower* + tail
        # current is the correct condition.
        tail_current_a = max_capacity_ah * params["tail_c_rate"]
        v_full_threshold = (
            min(sync_voltage, floating_voltage)
            if floating_voltage is not None and floating_voltage > 0
            else sync_voltage
        )
        at_voltage = current_voltage >= v_full_threshold
        at_tail = 0 < signed_current_a <= tail_current_a

        if at_voltage and at_tail:
            self._at_sync_ticks += 1
        else:
            # Decay (not hard reset) — one bad tick costs one count, two
            # good ticks recover. Tolerates brief load transients during
            # absorption without falsely re-arming from scratch.
            self._at_sync_ticks = max(0, self._at_sync_ticks - 1)

        if self._at_sync_ticks >= _SYNC_DEBOUNCE_TICKS:
            soc_percent = 100.0
            self._accumulated_charge_ah = max_capacity_ah
        else:
            if self._accumulated_charge_ah < 0:
                self._accumulated_charge_ah = 0.0
            elif self._accumulated_charge_ah > max_capacity_ah:
                self._accumulated_charge_ah = max_capacity_ah

            soc_percent = (self._accumulated_charge_ah / max_capacity_ah) * 100

        soc_percent = max(0.0, min(100.0, soc_percent))

        self._attr_native_value = soc_percent
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
