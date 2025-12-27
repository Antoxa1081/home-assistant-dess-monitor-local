from abc import ABC, abstractmethod

from custom_components.dess_monitor_local.types import InverterSnapshot


class BaseInverterParser(ABC):
    @abstractmethod
    def sensors_from(self, sensors): ...

    @abstractmethod
    def settings_from(self, config): ...

    @abstractmethod
    def rated_from(self, config): ...

    def snapshot_from(self, sensors, config) -> InverterSnapshot:
        return InverterSnapshot(
            sensors=self.sensors_from(sensors),
            settings=self.settings_from(config),
            rated=self.rated_from(config),
        )
