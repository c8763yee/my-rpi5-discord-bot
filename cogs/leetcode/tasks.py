import datetime
import os

import discord
from discord.ext import tasks

from cogs import CogsExtension
from loggers import TZ

from .schema import UpcomingContest
from .utils import LeetCodeUtils, ResponseFormatter

daily_challenge_time = datetime.time(hour=8, minute=0, second=0, tzinfo=TZ)
biweekly_contest_start_time = datetime.time(hour=22, minute=30, second=0, tzinfo=TZ)
biweekly_contest_end_time = datetime.time(hour=23, minute=45, second=0, tzinfo=TZ)
weekly_contest_start_time = datetime.time(hour=10, minute=30, second=0, tzinfo=TZ)
weekly_contest_end_time = datetime.time(hour=11, minute=45, second=0, tzinfo=TZ)
contest_remind_time = datetime.time(hour=10, minute=0, second=0, tzinfo=TZ)


class LeetCodeTasks(CogsExtension):
    def __init__(self, bot, *args, **kwargs):
        super().__init__(bot, *args, **kwargs)
        self.utils = LeetCodeUtils()

    async def cog_load(self):
        self.fetch_daily_challenge.start()
        self.fetch_contest.start()
        self.biweekly_contest_start_reminder.start()
        self.biweekly_contest_end_reminder.start()
        self.weekly_contest_start_reminder.start()
        self.weekly_contest_end_reminder.start()

    async def cog_unload(self):
        self.fetch_daily_challenge.stop()
        self.fetch_contest.stop()
        self.biweekly_contest_start_reminder.stop()
        self.biweekly_contest_end_reminder.stop()
        self.weekly_contest_start_reminder.stop()
        self.weekly_contest_end_reminder.stop()

    async def _notify_test_channel(self, content: str, **kwargs):
        channel_id = os.getenv("TEST_CHANNEL_ID")
        if not channel_id:
            self.logger.error("TEST_CHANNEL_ID is not configured.")
            return

        channel = self.bot.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            self.logger.error("Test channel is unavailable or is not a text channel")
            return

        try:
            await channel.send(content, **kwargs)
        except discord.errors.Forbidden as forbidden:
            self.logger.error(
                "PiBot has no permission to send message in the test channel: %s", forbidden
            )

    async def _send_contest_reminder(self, contest_prefix: str, notification_message: str):
        contests: list[UpcomingContest] = await self.utils.fetch_contest()
        target_contest = next(
            (contest for contest in contests if contest.title.startswith(contest_prefix)),
            None,
        )

        is_success, embed = await ResponseFormatter.parse_contest(target_contest, only_today=True)
        if not is_success or embed is None:
            self.logger.info("No %s today", contest_prefix.lower())
            return

        await self._notify_test_channel(notification_message, embed=embed)

    @tasks.loop(time=daily_challenge_time)
    async def fetch_daily_challenge(self):
        response = await self.utils.fetch_daily_challenge()
        embed, title = await ResponseFormatter.daily_challenge(response)
        await self._notify_test_channel(
            f"@here\n :tada: **Daily LeetCode Challenge** :tada:  \n{title}", embed=embed
        )

    @tasks.loop(time=contest_remind_time)
    async def fetch_contest(self):
        contests: list[UpcomingContest] = await self.utils.fetch_contest()
        is_success, embeds = await ResponseFormatter.parse_contests(contests, only_today=True)
        if not is_success or not embeds:
            return

        await self._notify_test_channel(
            "@here\n :tada: **Upcoming LeetCode Contest** :tada:  \n", embeds=embeds
        )

    @tasks.loop(time=biweekly_contest_start_time)
    async def biweekly_contest_start_reminder(self):
        await self._send_contest_reminder(
            "Biweekly Contest",
            "@here\n:tada:**This week of the Biweekly LeetCode Contest is started!**:tada:\n",
        )

    @tasks.loop(time=biweekly_contest_end_time)
    async def biweekly_contest_end_reminder(self):
        await self._send_contest_reminder(
            "Biweekly Contest",
            "@here\n"
            ":tada:**This week of the Biweekly LeetCode Contest will end in 15 minutes!**:tada:\n",
        )

    @tasks.loop(time=weekly_contest_start_time)
    async def weekly_contest_start_reminder(self):
        await self._send_contest_reminder(
            "Weekly Contest",
            "@here\n:tada:**This week of the Weekly LeetCode Contest is started!**:tada:\n",
        )

    @tasks.loop(time=weekly_contest_end_time)
    async def weekly_contest_end_reminder(self):
        await self._send_contest_reminder(
            "Weekly Contest",
            "@here\n"
            ":tada:**This week of the Weekly LeetCode Contest will end in 15 minutes!**:tada:\n",
        )
