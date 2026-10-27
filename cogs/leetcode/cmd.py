from discord.ext import commands

from .tasks import LeetCodeTasks
from .utils import ResponseFormatter


class LeetCodeCMD(LeetCodeTasks):
    @commands.hybrid_group(ephemeral=True)
    async def leetcode(self, ctx: commands.Context):
        """Commands for querying LeetCode profiles, daily challenges, and contests."""

    @leetcode.command("user")
    async def user(self, ctx: commands.Context, username: str):
        await ctx.defer()
        user_info = await self.utils.fetch_user_info(username)
        embed = await ResponseFormatter.user_info(user_info, username)
        await ctx.send(embed=embed)

    @leetcode.command("daily_challenge")
    async def daily(self, ctx: commands.Context):
        await ctx.defer()
        response = await self.utils.fetch_daily_challenge()
        embed, title = await ResponseFormatter.daily_challenge(response)
        await ctx.send(f"Daily LeetCode Challenge: {title}", embed=embed)

    @leetcode.command("contest")
    async def contest(self, ctx: commands.Context, only_today: bool = False):
        await ctx.defer()
        response = await self.utils.fetch_contest()
        is_success, embeds = await ResponseFormatter.parse_contests(response, only_today)

        if not is_success:
            await ctx.send("No upcoming contest")
            return

        await ctx.send("Upcoming LeetCode Contest", embeds=embeds)
