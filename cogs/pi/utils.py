import subprocess
from datetime import datetime
from pathlib import Path
from textwrap import dedent

import psutil
from discord import Color, Embed

from core.classes import BaseClassMixin
from core.models import Field
from loggers import TZ

from .const import REBOOT_TEMPERATURE, WARNING_TEMPERATURE


class TemperatureTooHighError(Exception):
    """Raised when the Raspberry Pi CPU temperature exceeds the safe reboot threshold."""


class RaspberryPiUtils(BaseClassMixin):
    thermal_zone_path = Path("/sys/class/thermal/thermal_zone0/temp")

    @staticmethod
    def convert_to_gb(value: int) -> float:
        return value / (1024**3)

    def reboot(self) -> None:
        """Trigger a system reboot."""
        self.logger.warning("Executing system reboot command.")
        subprocess.run(["sudo", "reboot"], check=False)

    async def get_temperature(self) -> str:
        """Get the Raspberry Pi CPU temperature from Linux thermal sysfs."""
        raw_temp = self.thermal_zone_path.read_text(encoding="ascii").strip()
        temperature = float(raw_temp) / 1000.0

        timestamp = datetime.now(tz=TZ).strftime("[%Y-%m-%d %H:%M:%S]: ")

        if temperature > REBOOT_TEMPERATURE:
            self.logger.warning("Temperature Too High: %.1f °C, Rebooting", temperature)
            raise TemperatureTooHighError(f"Temperature Too High: {temperature:.1f} °C, Rebooting")

        if temperature > WARNING_TEMPERATURE:
            message = (
                f"{timestamp}Temperature High: {temperature:.1f} °C, "
                "Consider Rebooting or Cooling"
            )
            self.logger.warning(message)
        else:
            message = f"{timestamp}Temperature: {temperature:.1f} °C"
            self.logger.info(message)

        return message

    async def get_stats(self) -> dict[str, str]:
        """Get current stats of the Raspberry Pi using psutil and thermal sysfs."""
        virtual_mem = psutil.virtual_memory()
        memory_used = self.convert_to_gb(virtual_mem.used)
        memory_total = self.convert_to_gb(virtual_mem.total)
        memory_text = dedent(
            f"""\
            Percentage: {virtual_mem.percent}%
            Used: ({memory_used:.2f}GB / {memory_total:.2f}GB)"""
        )

        disk = psutil.disk_usage("/")
        disk_used = self.convert_to_gb(disk.used)
        disk_total = self.convert_to_gb(disk.total)
        disk_text = dedent(
            f"""\
            Percentage: {disk.percent}%
            Used: ({disk_used:.2f}GB / {disk_total:.2f}GB)"""
        )

        return {
            "now": datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "cpu_usage": f"{psutil.cpu_percent()}%",
            "memory_usage": memory_text,
            "disk_usage": disk_text,
            "temperature": await self.get_temperature(),
        }


class StatsFormatter:
    @staticmethod
    async def format_stats(stats: dict[str, str]) -> Embed:
        return await BaseClassMixin.create_embed(
            "Raspberry Pi Statistics",
            f'Current Time: {stats["now"]}',
            Field(name="CPU Usage", value=stats["cpu_usage"], inline=False),
            Field(name="Memory Usage", value=stats["memory_usage"], inline=False),
            Field(name="Disk Usage", value=stats["disk_usage"], inline=False),
            Field(name="Temperature", value=stats["temperature"], inline=False),
            color=Color.blue(),
        )
