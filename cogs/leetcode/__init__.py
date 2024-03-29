from discord.ext import commands

from .cmd import LeetCodeCMD


async def setup(bot: commands.Bot):
    await bot.add_cog(LeetCodeCMD(bot))


async def teardown(bot: commands.Bot):
    await bot.remove_cog("LeetCodeCMD")
