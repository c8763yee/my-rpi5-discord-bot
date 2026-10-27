from discord.ext import commands

from cogs import CogsExtension

from .utils import APIUtils, ArcaeaResponseFormatter


class ArcaeaCMD(CogsExtension):
    def __init__(self, bot, *args, **kwargs):
        super().__init__(bot, *args, **kwargs)
        self.utils = APIUtils()

    async def cog_load(self):
        await self.utils.init_db()

    async def cog_unload(self):
        await self.utils.unload()

    @commands.hybrid_group(name="arc", ephemeral=True)
    async def arcaea(self, ctx: commands.Context):
        """Commands for Arcaea score calculation and recent play queries."""

    @arcaea.command("register")
    async def register_account(self, ctx: commands.Context, email: str, password: str):
        await ctx.defer(ephemeral=True)
        account, friend_count = await self.utils.register_account(email, password)
        display = account.display_name or account.name or account.email
        await ctx.send(
            f"Registered Arcaea query account **{display}** "
            f"(synced **{friend_count}** friend(s)).",
            ephemeral=True,
        )

    @arcaea.command("score2step")
    async def score_to_step(
        self, ctx: commands.Context, song_rating: float, char_step: int, score: int
    ):
        rating = await self.utils.score_to_rating(song_rating, score)
        step = await self.utils.rating_to_step(char_step, rating)
        await ctx.send(f"Score: {score} -> Step: {step:.2f}")

    @arcaea.command("step2score")
    async def step_to_score(
        self, ctx: commands.Context, song_rating: float, char_step: int, step: int
    ):
        rating = await self.utils.step_to_rating(char_step, step)
        score = await self.utils.rating_to_score(rating, song_rating)
        await ctx.send(f"Step: {step} -> Score: {score}")

    @arcaea.command("recent")
    async def recent_score(self, ctx: commands.Context, user_code: str):
        await ctx.defer()

        result = await self.utils.fetch_recent(user_code)
        embed, username, *files = await ArcaeaResponseFormatter.recent_score(result)

        await ctx.send(
            f"Recent play info for user **{username}**", embed=embed, files=files
        )
