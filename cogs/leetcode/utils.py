import asyncio
import datetime
import functools
import json
import os
import re
from pathlib import Path
from textwrap import dedent

import discord
from aiohttp import ClientSession, ContentTypeError
from pydantic import BaseModel

from core import load_env
from core.classes import BaseClassMixin
from core.models import Field
from loggers import TZ

from .const import API_URL, THUMBNAIL_URL
from .schema import UpcomingContest, UpcomingContestsResponse

load_env(path=Path.cwd() / "env" / "bot.env")

with (Path.cwd() / "json_data" / "secret.json").open("r", encoding="utf-8") as f:
    script = json.load(f)
    headers: dict[str, str] = script["headers"]
    cookies: dict[str, str] = script["cookies"]
    csrf_token = os.getenv("LEETCODE_CSRFTOKEN") or ""
    cookies["csrftoken"] = csrf_token
    headers["x-csrftoken"] = csrf_token

difficulty_color = {
    "Easy": discord.Color.green(),
    "Medium": discord.Color.gold(),
    "Hard": discord.Color.red(),
}

OPERATION_PATTERN = re.compile(r"^\s*query\s+([a-zA-Z]+)\s*\((.*)\)\s*\{")


@functools.lru_cache(maxsize=8)
def _load_graphql_query(relative_path: str) -> str:
    return Path(relative_path).read_text(encoding="utf-8")


class LeetCodeUtils(BaseClassMixin):
    async def _send_request_to_api(
        self,
        operation: str,
        query: str = "",
        pydantic_model: type[BaseModel] | None = None,
        session: ClientSession | None = None,
        **variables,
    ) -> dict | BaseModel:
        request_body = {"operationName": operation, "variables": variables, "query": query}

        async def _post(active_session: ClientSession) -> dict:
            async with active_session.post(
                API_URL, json=request_body, headers=headers, cookies=cookies
            ) as resp:
                try:
                    return await resp.json()
                except ContentTypeError as exc:
                    self.logger.error("Error occurred while decoding LeetCode response: %s", exc)
                    raise ValueError(
                        "Error occurred while fetching data from LeetCode API"
                    ) from exc

        if session is not None:
            response = await _post(session)
        else:
            async with ClientSession() as new_session:
                response = await _post(new_session)

        if pydantic_model is not None:
            return pydantic_model.model_validate(response)

        return response

    async def fetch_user_info(self, username: str) -> dict:
        user_query = _load_graphql_query("queries/profile_page.graphql")
        operation_names = [
            match.group(1)
            for line in user_query.splitlines()
            if (match := OPERATION_PATTERN.match(line)) is not None
        ]

        now = datetime.datetime.now(tz=TZ)
        async with ClientSession() as session:
            results = await asyncio.gather(
                *(
                    self._send_request_to_api(
                        operation,
                        user_query,
                        session=session,
                        username=username,
                        year=now.year,
                        month=now.month,
                        limit=1,
                    )
                    for operation in operation_names
                )
            )

        return {
            operation: op_response.get("data", {})
            for operation, op_response in zip(operation_names, results)
        }

    async def fetch_daily_challenge(self) -> dict:
        """Fetch LeetCode daily challenge data including title, difficulty, tags, and link."""
        daily_challenge_query = _load_graphql_query("queries/profile_page.graphql")
        response = await self._send_request_to_api("questionOfToday", query=daily_challenge_query)
        return response["data"]

    async def fetch_contest(self) -> list[UpcomingContest]:
        contest_query = _load_graphql_query("queries/feed.graphql")
        response = await self._send_request_to_api(
            "upcomingContests", query=contest_query, pydantic_model=UpcomingContestsResponse
        )
        return response.data.upcomingContests


class ResponseFormatter:
    @staticmethod
    async def user_info(response: dict, username: str) -> discord.Embed:
        matched_user = (response.get("userPublicProfile") or {}).get("matchedUser")
        if not matched_user:
            raise ValueError(f"LeetCode user `{username}` not found.")

        matched_userprofile = matched_user.get("profile") or {}
        thumbnail = matched_userprofile.get("userAvatar")
        description = matched_userprofile.get("aboutMe") or ""
        display_name = (
            matched_userprofile.get("realName") or matched_user.get("username") or username
        )

        rating_info = (response.get("userContestRankingInfo") or {}).get("userContestRanking") or {}
        solved_problems = (
            ((response.get("userProblemsSolved") or {}).get("matchedUser") or {})
            .get("submitStatsGlobal", {})
            .get("acSubmissionNum", [])
        )
        language_count = list(
            ((response.get("languageStats") or {}).get("matchedUser") or {}).get(
                "languageProblemCount", []
            )
        )
        language_count.sort(key=lambda x: x["problemsSolved"], reverse=True)

        recent_ac_list = (response.get("recentAcSubmissions") or {}).get(
            "recentAcSubmissionList"
        ) or []
        if recent_ac_list:
            first_ac = recent_ac_list[0]
            recent_ac = (
                f'[{first_ac["title"]}](https://leetcode.com/problems/{first_ac["titleSlug"]})'
            )
        else:
            recent_ac = "No recent AC submissions"

        rank_text = (
            f"{rating_info.get('globalRanking', 'N/A')}/"
            f"{rating_info.get('totalParticipants', 'N/A')}"
        )
        top_percentage = rating_info.get("topPercentage")
        top_pct_str = f"{top_percentage:.2f}%" if top_percentage is not None else "N/A"
        rating_val = rating_info.get("rating")
        rating_str = f"{rating_val:.0f}" if isinstance(rating_val, int | float) else "N/A"

        rating = dedent(
            f"""\
            attempts: {rating_info.get("attendedContestsCount", "N/A")}
            Rank: {rank_text}
            Rating: {rating_str}
            Top %: {top_pct_str}"""
        )

        solved_count = (
            "\n".join(f"{item['difficulty']}: {item['count']}" for item in solved_problems)
            or "N/A"
        )
        languages = (
            "\n".join(
                f"{item['languageName']}: {item['problemsSolved']}" for item in language_count
            )
            or "N/A"
        )

        return await BaseClassMixin.create_embed(
            display_name,
            description,
            Field(name="Recent AC", value=recent_ac, inline=False),
            Field(name="Rating", value=rating, inline=True),
            Field(name="Solved Count", value=solved_count, inline=True),
            Field(name="Languages", value=languages, inline=False),
            thumbnail_url=thumbnail,
            color=discord.Color.blurple(),
            url=f"https://leetcode.com/{username}",
        )

    @staticmethod
    async def daily_challenge(response: dict) -> tuple[discord.Embed, str]:
        daily_data = response["activeDailyCodingChallengeQuestion"]
        question = daily_data["question"]
        question_id = question["frontendQuestionId"]
        title = f'{question_id}. {question["title"]}'
        difficulty = question["difficulty"]
        color = difficulty_color.get(difficulty, discord.Color.blurple())

        link = f"https://leetcode.com{daily_data['link']}"
        topic = ", ".join(tag["name"] for tag in question.get("topicTags", [])) or "N/A"
        ac_rate = f'{question["acRate"]:.2f}%'

        embed = await BaseClassMixin.create_embed(
            title,
            "Today's Leetcode Daily Challenge",
            Field(name="Question Link", value=f"[link]({link})", inline=False),
            Field(name="Difficulty", value=difficulty, inline=True),
            Field(name="Topic", value=topic, inline=True),
            Field(name="Acceptance Rate", value=ac_rate, inline=True),
            thumbnail_url=THUMBNAIL_URL,
            color=color,
            url=link,
        )
        return embed, title

    @classmethod
    async def parse_contest(
        cls, contest: UpcomingContest | None = None, only_today: bool = False
    ) -> tuple[bool, discord.Embed | None]:
        if contest is None or (only_today and not await cls.today_is_contest(contest)):
            return False, None

        start_time = datetime.datetime.fromtimestamp(contest.startTime, tz=TZ)
        link = f"https://leetcode.com/contest/{contest.titleSlug}/"

        embed = await BaseClassMixin.create_embed(
            contest.title,
            "Remember to participate in the contest!",
            Field(name="Start Time", value=start_time.strftime("%Y-%m-%d %H:%M:%S"), inline=False),
            color=discord.Color.blurple(),
            url=link,
            thumbnail_url=THUMBNAIL_URL,
        )
        return True, embed

    @classmethod
    async def parse_contests(
        cls, contests: list[UpcomingContest], only_today: bool = False
    ) -> tuple[bool, list[discord.Embed]]:
        embeds: list[discord.Embed] = []
        for contest in contests:
            is_success, embed = await cls.parse_contest(contest, only_today=only_today)
            if is_success and embed is not None:
                embeds.append(embed)

        return bool(embeds), embeds

    @staticmethod
    async def today_is_contest(contest: UpcomingContest) -> bool:
        start_time = datetime.datetime.fromtimestamp(contest.startTime, tz=TZ)
        return start_time.date() == datetime.datetime.now(tz=TZ).date()
