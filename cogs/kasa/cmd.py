from typing import Literal

from discord.ext import commands

from .const import PlugID
from .tasks import KasaTasks
from .utils import KasaResponseFormatter


class KasaCMD(KasaTasks):
    @commands.hybrid_group(ephemeral=True)
    async def kasa(self, ctx: commands.Context):
        """Commands for Kasa smart plugs."""

    @kasa.command("daily_usage")
    async def kasa_daily_usage(self, ctx: commands.Context, plug_id: PlugID):
        await ctx.defer()
        daily_kwh = await self.utils.get_daily_power_usage(plug_id)
        if daily_kwh < 0:
            await ctx.send(f"No power usage record found for plug {plug_id.name} ({int(plug_id)}).")
            return
        await ctx.send(
            f"Daily power usage of plug {plug_id.name} ({int(plug_id)}): {daily_kwh:.3f} kWh"
        )

    @kasa.command("emeter")
    async def kasa_emeter(self, ctx: commands.Context, plug_id: PlugID):
        await ctx.defer()
        payload = await self.utils.get_power_usage(plug_id)
        embed = await KasaResponseFormatter.format_power_usage(payload)
        await ctx.send(f"Power usage of plug {plug_id.name} ({int(plug_id)})", embed=embed)

    @kasa.command("emeters")
    async def kasa_emeters(
        self,
        ctx: commands.Context,
        plug_ids: commands.Greedy[int] = None,  # greedy can't be used with enum
    ):
        await ctx.defer()
        target_plugs = [PlugID(pid) for pid in plug_ids] if plug_ids else list(PlugID)

        payloads = await self.utils.get_power_usage_multiple(target_plugs)
        embeds = await KasaResponseFormatter.format_power_usage_multiple(payloads)
        plug_labels = ", ".join(str(int(pid)) for pid in target_plugs)
        await ctx.send(f"Power usage of plugs ({plug_labels})", embeds=embeds)

    @commands.is_owner()
    @kasa.command("on")
    async def kasa_on(self, ctx: commands.Context, plug_id: PlugID):
        await ctx.defer()
        await ctx.send(await self.utils.turn_on(plug_id))

    @commands.is_owner()
    @kasa.command("off")
    async def kasa_off(self, ctx: commands.Context, plug_id: PlugID):
        await ctx.defer()
        await ctx.send(await self.utils.turn_off(plug_id))

    @commands.is_owner()
    @kasa.command("toggle")
    async def kasa_toggle(
        self,
        ctx: commands.Context,
        plug_id: PlugID,
        status: Literal["on", "off"] | None = None,
    ):
        await ctx.defer()
        await ctx.send(await self.utils.toggle(plug_id, status))
