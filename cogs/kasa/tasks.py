import datetime
import os

import discord
from discord.ext import tasks

from cogs import CogsExtension
from loggers import TZ

from .const import PlugID
from .utils import KasaUtils

daily_report_time = datetime.time(
    hour=12,
    minute=0,
    second=0,
    tzinfo=TZ,
)


class KasaTasks(CogsExtension):
    def __init__(self, bot, *args, **kwargs):
        super().__init__(bot, *args, **kwargs)
        self.utils = KasaUtils()

    async def cog_load(self):
        self.power_report.start()

    async def cog_unload(self):
        self.power_report.stop()

    @tasks.loop(time=daily_report_time)
    async def power_report(self):
        channel_id = os.getenv("TEST_CHANNEL_ID")
        if not channel_id:
            self.logger.error("TEST_CHANNEL_ID is not configured.")
            return

        channel = self.bot.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            self.logger.error("Test channel is unavailable or is not a text channel.")
            return

        lines = []
        for plug_id in PlugID:
            daily_kwh = await self.utils.get_daily_power_usage(plug_id)
            usage_str = f"{daily_kwh:.3f} kWh" if daily_kwh >= 0 else "No data"
            lines.append(f"Plug {plug_id.name} ({int(plug_id)}): {usage_str}")

        await channel.send(
            "Daily power usage report for all plugs\n```\n" + "\n".join(lines) + "\n```",
            silent=True,
        )
