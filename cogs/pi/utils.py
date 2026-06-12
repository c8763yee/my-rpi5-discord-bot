from datetime import datetime
from pathlib import Path

import psutil
from discord import Color, Embed

from core.classes import BaseClassMixin
from core.models import Field
from loggers import TZ

from .const import REBOOT_TEMPERATURE, WARNING_TEMPERATURE


class TemperatureTooHighError(Exception): ...


class RaspberryPiUtils(BaseClassMixin):
    thermal_zone_path = Path("/sys/class/thermal/thermal_zone0/temp")

    @staticmethod
    def convert_to_gb(value: int) -> float:
        return value / 1024 / 1024 / 1024

    async def get_temperature(self) -> str:
        """Get the Raspberry Pi CPU temperature from Linux thermal sysfs."""
        temperature = float(self.thermal_zone_path.read_text(encoding="ascii").strip()) / 1000

        message = datetime.now(tz=TZ).strftime("[%Y-%m-%d %H:%M:%S]: ")

        if temperature > REBOOT_TEMPERATURE:
            self.logger.warning("Temperature Too High: %s °C, Rebooting", temperature)
            raise TemperatureTooHighError(f"Temperature Too High: {temperature} °C, Rebooting")

        if temperature > WARNING_TEMPERATURE:
            message += f"Temperature High: {temperature} °C, Consider Rebooting or Cooling"
            self.logger.warning(message)
        else:
            message += f"Temperature: {temperature} °C"
            self.logger.info(message)

        return message

    async def get_stats(self):
        """Get current stats of the Raspberry Pi using psutil and vcgencmd."""
        memory_used = self.convert_to_gb(psutil.virtual_memory().used)
        memory_total = self.convert_to_gb(psutil.virtual_memory().total)

        memory_text = f"""
        Percentage: {psutil.virtual_memory().percent}%
        Used: ({memory_used:.2f}GB / {memory_total:.2f}GB)
        """

        disk_used = self.convert_to_gb(psutil.disk_usage("/").used)
        disk_total = self.convert_to_gb(psutil.disk_usage("/").total)

        disk_text = f"""
        Percentage: {psutil.disk_usage("/").percent}%
        Used: ({disk_used:.2f}GB / {disk_total:.2f}GB)
        """

        message = {
            "now": datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "cpu_usage": f"{psutil.cpu_percent()}%",
            "memory_usage": memory_text,
            "disk_usage": disk_text,
            "temperature": await self.get_temperature(),
        }

        return message


class StatsFormatter:
    @staticmethod
    async def format_stats(stats: dict) -> Embed:
        embed = await BaseClassMixin.create_embed(
            "Raspberry Pi Statistics",
            f'Current Time: {stats["now"]}',
            Field(name="CPU Usage", value=stats["cpu_usage"], inline=False),
            Field(name="Memory Usage", value=stats["memory_usage"], inline=False),
            Field(name="Disk Usage", value=stats["disk_usage"], inline=False),
            Field(name="Temperature", value=stats["temperature"], inline=False),
            color=Color.blue(),
        )
        return embed
