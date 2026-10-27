import datetime
import os

import discord
from discord.ext import tasks

from cogs import CogsExtension
from loggers import TZ

from .utils import RaspberryPiUtils, StatsFormatter, TemperatureTooHighError

per_clock = [
    datetime.time(
        hour=hour,
        minute=0,
        second=0,
        tzinfo=TZ,
    )
    for hour in range(6, 24)
]


class RaspberryPiTasks(CogsExtension):
    def __init__(self, bot, *args, **kwargs):
        super().__init__(bot, *args, **kwargs)
        self.utils = RaspberryPiUtils()

    async def cog_load(self):
        self.get_stats.start()

    async def cog_unload(self):
        self.get_stats.stop()

    def _get_test_channel(self) -> discord.TextChannel | None:
        channel_id = os.getenv("TEST_CHANNEL_ID")
        if not channel_id:
            self.logger.error("TEST_CHANNEL_ID is not configured.")
            return None

        channel = self.bot.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            self.logger.error("Test channel is unavailable or is not a text channel.")
            return None

        return channel

    @tasks.loop(time=per_clock)
    async def get_stats(self):
        channel = self._get_test_channel()
        if channel is None:
            return

        try:
            message = await self.utils.get_stats()
        except TemperatureTooHighError:
            await channel.send("Temperature too high, rebooting")
            self.utils.reboot()
            return

        embed = await StatsFormatter.format_stats(message)
        await channel.send(f'[Raspberry Pi Stats] {message["now"]}', embed=embed, silent=True)
