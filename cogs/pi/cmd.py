from discord.ext import commands

from .tasks import RaspberryPiTasks
from .utils import StatsFormatter, TemperatureTooHighError


class RaspberryPiCMD(RaspberryPiTasks):
    @commands.hybrid_group(ephemeral=True)
    async def pi(self, ctx: commands.Context):
        """Commands for monitoring and managing the Raspberry Pi."""

    @commands.is_owner()
    @pi.command("reboot")
    async def reboot(self, ctx: commands.Context):
        await ctx.send("Rebooting")
        self.utils.reboot()

    @pi.command("temp")
    async def temperature(self, ctx: commands.Context):
        try:
            message = await self.utils.get_temperature()
        except TemperatureTooHighError:
            await ctx.send("Temperature too high, rebooting")
            self.utils.reboot()
            return

        await ctx.send(message)

    @pi.command("stats")
    async def stats(self, ctx: commands.Context):
        try:
            message = await self.utils.get_stats()
        except TemperatureTooHighError:
            await ctx.send("Temperature too high, rebooting")
            self.utils.reboot()
            return

        embed = await StatsFormatter.format_stats(message)
        await ctx.send(f'[Raspberry Pi Stats] {message["now"]}', embed=embed)
