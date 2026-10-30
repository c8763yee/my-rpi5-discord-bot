import asyncio
import json
from datetime import datetime
from pathlib import Path

import aiohttp
from discord import Color, Embed, File
from sqlmodel import SQLModel, func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from core.classes import BaseClassMixin
from core.models import Field
from database import ArcaeaAccount, ArcaeaAccountFriendLink, ArcaeaFriend, engine
from loggers import TZ, setup_package_logger

from .const import (
    BELOW_EX_SCORE_DELTA,
    DIFFICULTY_ABBR,
    DIFFICULTY_COLOR_LIST,
    DIFFICULTY_NAMES,
    EX_RATING_DELTA,
    EX_SCORE_DELTA,
    GRADE_NAMES,
    GRADE_SUFFIX,
    PM_RATING_DELTA,
    GradeEnum,
    GradeScoreEnum,
)
from .schema import DeletedSong, Song, SongList

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 "
        "Safari/537.36"
    ),
    "Referer": "https://arcaea.lowiro.com",
    "Set-Cookie": "domain=lowiro.com",
    "Access-Control-Allow-Origin": "https://arcaea.lowiro.com",
}

CONCURRENT_REQUEST_LIMIT = 10


def is_same_song(song: dict, recent_play: dict) -> bool:
    return song["sid"] == recent_play["song_id"] and song["difficulty"] == recent_play["difficulty"]


class ScoreUtils(BaseClassMixin):
    async def step_to_rating(self, char_step: int, world_step: float) -> float:
        if char_step <= 0:
            raise ValueError("Character step stat must be greater than 0")
        base = (50 / char_step * world_step - 2.5) / 2.45
        return max(base, 0.0) ** 2

    async def rating_to_step(self, char_step: int, rating: float) -> float:
        r"""Calculate World Mode step progression from partner STEP stat and play rating."""
        if char_step <= 0:
            raise ValueError("Character step stat must be greater than 0")
        return char_step / 50 * (2.5 + 2.45 * max(rating, 0.0) ** 0.5)

    async def rating_to_score(self, user_rating: float, song_rating: float) -> int:
        diff_rating = user_rating - song_rating

        if diff_rating >= PM_RATING_DELTA:
            return int(GradeScoreEnum.PM)

        if diff_rating < EX_RATING_DELTA:
            return max(int(GradeScoreEnum.BELOW_EX + diff_rating * BELOW_EX_SCORE_DELTA), 0)

        return int(GradeScoreEnum.EX + (diff_rating - EX_RATING_DELTA) * EX_SCORE_DELTA)

    async def score_to_rating(self, chart_rating: float, score: int) -> float:
        if score >= GradeScoreEnum.PM:
            return chart_rating + PM_RATING_DELTA

        if score < GradeScoreEnum.EX:
            return max(chart_rating + (score - GradeScoreEnum.BELOW_EX) / BELOW_EX_SCORE_DELTA, 0.0)

        return chart_rating + EX_RATING_DELTA + (score - GradeScoreEnum.EX) / EX_SCORE_DELTA

    @staticmethod
    async def get_grade(score: int) -> GradeEnum:
        """Get grade enum from play score."""
        grades = (
            (GradeScoreEnum.EX_PLUS, GradeEnum.EX_PLUS),
            (GradeScoreEnum.EX, GradeEnum.EX),
            (GradeScoreEnum.BELOW_EX, GradeEnum.AA),
            (GradeScoreEnum.A, GradeEnum.A),
            (GradeScoreEnum.B, GradeEnum.B),
            (GradeScoreEnum.C, GradeEnum.C),
        )
        for threshold, grade in grades:
            if score >= threshold:
                return grade

        return GradeEnum.D


class APIUtils(ScoreUtils):
    base_url = "https://webapi.lowiro.com"
    _session: aiohttp.ClientSession | None = None
    _songlist: list[dict] = []

    def __init__(self, email: str = "", password: str = ""):
        super().__init__()
        self.email: str = email
        self.password: str = password
        self.is_logged_in: bool = False
        self.friend_ids: set[int] = set()
        self._friend_codes: dict[str, int] = {}
        self._session: aiohttp.ClientSession | None = None

    @staticmethod
    async def init_db() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    def _create_client(self, email: str, password: str) -> "APIUtils":
        return APIUtils(email=email, password=password)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self.is_logged_in = False
        self._session = None

    async def _unload(self) -> None:
        await self.close()
        if self.__class__._session and not self.__class__._session.closed:
            await self.__class__._session.close()
        self.__class__._songlist = []
        self.__class__._session = None

    async def unload(self) -> None:
        await self._unload()

    async def login(self) -> None:
        if self.is_logged_in:
            return

        if not self.email or not self.password:
            raise ValueError("Arcaea credentials are not configured")

        if self._session is None or self._session.closed:
            await self.open_session()

        async with self._session.post(
            f"{self.base_url}/auth/login",
            data={"email": self.email, "password": self.password},
            headers=headers,
        ) as request:
            response = await request.json()
            self.is_logged_in = bool(response.get("isLoggedIn", False))
            if self.is_logged_in and not self.__class__._songlist:
                self.__class__._songlist = await self.get_slst()

    async def get_me(self) -> dict:
        if self._session is None or self._session.closed:
            await self.open_session()

        if not self.is_logged_in:
            await self.login()
            if not self.is_logged_in:
                raise ValueError("Login failed!")

        async with self._session.get(
            f"{self.base_url}/webapi/user/me", headers=headers
        ) as response:
            result = await response.json()
            if not result.get("success", False):
                raise ValueError(f"Failed to fetch user profile: {result}")
            return result.get("value", {})

    @staticmethod
    async def _upsert_friend(
        session: AsyncSession,
        user_id: int,
        name: str,
        display_name: str | None = None,
        user_code: str | None = None,
    ) -> ArcaeaFriend:
        stmt = select(ArcaeaFriend).where(ArcaeaFriend.user_id == user_id)
        if user_code is not None:
            stmt = select(ArcaeaFriend).where(
                or_(ArcaeaFriend.user_id == user_id, ArcaeaFriend.user_code == user_code)
            )
        friend = (await session.exec(stmt)).first()
        if friend is None:
            friend = ArcaeaFriend(
                user_id=user_id,
                user_code=user_code,
                name=name,
                display_name=display_name,
            )
            session.add(friend)
            await session.flush()
        else:
            friend.user_id = user_id
            friend.name = name
            if display_name is not None:
                friend.display_name = display_name
            if user_code is not None:
                friend.user_code = user_code
            session.add(friend)
            await session.flush()
        return friend

    async def register_account(self, email: str, password: str) -> tuple[ArcaeaAccount, int]:
        if not email or not password:
            raise ValueError("Arcaea credentials are not configured")

        client = self._create_client(email=email, password=password)
        try:
            await client.login()
            if not client.is_logged_in:
                raise ValueError("Login failed! Please check your email and password.")

            me_value = await client.get_me()
            friends = await client.get_friends()
        finally:
            await client.close()

        user_id = me_value.get("user_id")
        user_code = (
            str(me_value["user_code"]) if me_value.get("user_code") is not None else None
        )
        name = me_value.get("name")
        display_name = me_value.get("display_name")
        max_friend = me_value.get("max_friend") or 30

        async with AsyncSession(engine, expire_on_commit=False) as session:
            stmt = select(ArcaeaAccount).where(ArcaeaAccount.email == email)
            if user_id is not None:
                stmt = select(ArcaeaAccount).where(
                    or_(ArcaeaAccount.email == email, ArcaeaAccount.user_id == user_id)
                )
            account = (await session.exec(stmt)).first()
            if account is None:
                account = ArcaeaAccount(
                    email=email,
                    password=password,
                    user_id=user_id,
                    user_code=user_code,
                    name=name,
                    display_name=display_name,
                    max_friend=max_friend,
                )
            else:
                account.email = email
                account.password = password
                account.user_id = user_id
                account.user_code = user_code
                account.name = name
                account.display_name = display_name
                account.max_friend = max_friend

            session.add(account)
            await session.flush()

            existing_links = (
                await session.exec(
                    select(ArcaeaAccountFriendLink).where(
                        ArcaeaAccountFriendLink.account_id == account.ID
                    )
                )
            ).all()
            linked_friend_ids = {link.friend_id for link in existing_links}
            synced_friend_ids: set[int] = set()

            for friend_data in friends:
                friend_user_id = friend_data.get("user_id")
                if friend_user_id is None:
                    continue

                friend_name = (
                    friend_data.get("name")
                    or friend_data.get("display_name")
                    or str(friend_user_id)
                )
                friend_display_name = friend_data.get("display_name")
                friend_user_code = (
                    str(friend_data["user_code"])
                    if friend_data.get("user_code") is not None
                    else None
                )

                friend_obj = await self._upsert_friend(
                    session,
                    user_id=friend_user_id,
                    name=friend_name,
                    display_name=friend_display_name,
                    user_code=friend_user_code,
                )
                synced_friend_ids.add(friend_obj.ID)
                if friend_obj.ID not in linked_friend_ids:
                    session.add(
                        ArcaeaAccountFriendLink(
                            account_id=account.ID,
                            friend_id=friend_obj.ID,
                        )
                    )
                    linked_friend_ids.add(friend_obj.ID)

            for link in existing_links:
                if link.friend_id not in synced_friend_ids:
                    await session.delete(link)

            await session.commit()

        return account, len(synced_friend_ids)

    async def fetch_play_info(self, song: dict, user_id: int) -> dict:
        sid = song["sid"]
        difficulty = song["difficulty"]

        songs_url = (
            f"{self.base_url}/webapi/score/song/friend"
            f"?song_id={sid}&difficulty={difficulty}&start=0&limit=30"
        )
        async with self._session.get(songs_url, headers=headers) as response:
            result = await response.json()
            if not result.get("success", False):
                return result

        for value in result.get("value", []):
            if value.get("user_id") == user_id:
                return {**value, **song}

        return result

    async def fetch_my_recent_play_info(self) -> tuple[dict, str]:
        user_value = await self.get_me()
        recent_scores = user_value.get("recent_score") or []
        if not recent_scores:
            raise ValueError("This user has no recent play record.")

        username = user_value.get("display_name") or user_value.get("name") or "Unknown"
        return recent_scores[0], username

    async def fetch_recent_play_info(self, user_id: int) -> dict:
        if self._session is None or self._session.closed:
            await self.open_session()
            if not self.is_logged_in:
                await self.login()

        async with self._session.get(
            f"{self.base_url}/webapi/user/me", headers=headers
        ) as response:
            result = await response.json()
            if not result.get("success", False):
                raise ValueError(f"Failed to fetch user profile: {result}")

        user_value = result.get("value", {})
        if user_value.get("user_id") == user_id:
            recent_scores = user_value.get("recent_score") or []
            if not recent_scores:
                raise ValueError("This user has no recent play record.")
            return recent_scores[0]

        for friend in await self.get_friends():
            if friend.get("user_id") == user_id:
                recent_scores = friend.get("recent_score") or []
                if not recent_scores:
                    raise ValueError("This user has no recent play record.")
                return recent_scores[0]

        raise ValueError(f"User ID {user_id} not found in friend list.")

    async def add_friend(self, user_code: str) -> dict:
        async with self._session.post(
            f"{self.base_url}/webapi/friend/me/add",
            headers={
                "Content-Type": "multipart/form-data; boundary=boundary",
                **headers,
            },
            data=(
                "--boundary\r\n"
                f'Content-Disposition: form-data;name="friend_code"\r\n\r\n{user_code}\r\n'
                "--boundary--\r\n"
            ),
        ) as response:
            data = await response.json()
            data["_http_status"] = response.status
            return data

    async def del_friend(self, user_id: int) -> dict:
        async with self._session.post(
            f"{self.base_url}/webapi/friend/me/delete",
            headers={
                "Content-Type": "multipart/form-data; boundary=boundary",
                **headers,
            },
            data=(
                "--boundary\r\n"
                f'Content-Disposition: form-data; name="friend_id"\r\n\r\n{user_id}\r\n'
                "--boundary--\r\n"
            ),
        ) as response:
            return await response.json()

    async def get_friends(self) -> list[dict]:
        async with self._session.get(
            f"{self.base_url}/webapi/friend/me", headers=headers
        ) as response:
            resp = await response.json()
            if not resp.get("success"):
                raise ValueError(
                    f"Failed to fetch friends: HTTP {response.status}, "
                    f"error_code={resp.get('error_code')}"
                )
            friends = resp["value"]["friends"]
            self.friend_ids = {friend["user_id"] for friend in friends}
            return friends

    async def update_friend_list(self) -> None:
        await self.get_friends()

    async def get_slst(self) -> list[dict]:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.chinosk6.cn/arcscore/get_slst") as response:
                return await response.json()

    async def open_session(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    @classmethod
    def close_session(cls) -> None:
        if cls._session and not cls._session.closed:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(cls._session.close())
            except RuntimeError:
                asyncio.run(cls._session.close())
            finally:
                cls._session = None

    @staticmethod
    async def _find_friend_in_db(
        session: AsyncSession, target_user_code: str
    ) -> ArcaeaFriend | None:
        friend = (
            await session.exec(
                select(ArcaeaFriend).where(ArcaeaFriend.user_code == target_user_code)
            )
        ).first()
        if friend is not None:
            return friend

        if target_user_code.isdigit():
            friend = (
                await session.exec(
                    select(ArcaeaFriend).where(
                        ArcaeaFriend.user_id == int(target_user_code)
                    )
                )
            ).first()
            if friend is not None:
                return friend

        return (
            await session.exec(
                select(ArcaeaFriend).where(
                    or_(
                        ArcaeaFriend.name == target_user_code,
                        ArcaeaFriend.display_name == target_user_code,
                    )
                )
            )
        ).first()

    @staticmethod
    async def _find_account_in_db(
        session: AsyncSession, target_user_code: str
    ) -> ArcaeaAccount | None:
        account = (
            await session.exec(
                select(ArcaeaAccount).where(ArcaeaAccount.user_code == target_user_code)
            )
        ).first()
        if account is not None:
            return account

        if target_user_code.isdigit():
            account = (
                await session.exec(
                    select(ArcaeaAccount).where(
                        ArcaeaAccount.user_id == int(target_user_code)
                    )
                )
            ).first()
            if account is not None:
                return account

        return (
            await session.exec(
                select(ArcaeaAccount).where(
                    or_(
                        ArcaeaAccount.name == target_user_code,
                        ArcaeaAccount.display_name == target_user_code,
                    )
                )
            )
        ).first()

    async def _resolve_identifiers_from_db(
        self, user_code: str
    ) -> tuple[int | None, str | None]:
        target = user_code.strip()
        if not target:
            return None, None

        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                friend = await self._find_friend_in_db(session, target)
                if friend is not None:
                    if friend.user_code:
                        self._friend_codes[friend.user_code] = friend.user_id
                    self._friend_codes[target] = friend.user_id
                    return friend.user_id, friend.user_code

                account = await self._find_account_in_db(session, target)
                if account is not None and account.user_id is not None:
                    if account.user_code:
                        self._friend_codes[account.user_code] = account.user_id
                    self._friend_codes[target] = account.user_id
                    return account.user_id, account.user_code
        except Exception:  # pylint: disable=broad-exception-caught
            return None, None

        return None, None

    async def get_user_id(self, user_code: str) -> tuple[int, str]:
        db_user_id, db_user_code = await self._resolve_identifiers_from_db(user_code)

        me_value = await self.get_me()
        own_identifiers = (
            me_value.get("user_code"),
            str(me_value.get("user_id")),
            me_value.get("name"),
        )
        if (
            (db_user_id is not None and me_value.get("user_id") == db_user_id)
            or user_code in own_identifiers
            or (db_user_code is not None and db_user_code in own_identifiers)
        ):
            return me_value["user_id"], me_value.get("display_name") or me_value.get("name")

        friends = await self.get_friends()
        self.friend_ids = {friend["user_id"] for friend in friends if "user_id" in friend}
        for friend in friends:
            identifiers = (
                str(friend.get("user_code")),
                str(friend.get("user_id")),
                friend.get("name"),
            )
            if (
                (db_user_id is not None and friend.get("user_id") == db_user_id)
                or user_code in identifiers
                or (db_user_code is not None and db_user_code in identifiers)
                or self._friend_codes.get(user_code) == friend["user_id"]
            ):
                return friend["user_id"], friend.get("display_name") or friend["name"]

        friend_code = db_user_code or user_code
        resp = await self.add_friend(friend_code)
        if resp.get("success"):
            added_friends = resp.get("value", {}).get("friends", [])
            if not added_friends:
                raise ValueError(f"Arcaea returned no friend data: {resp}")
            new_friends = [
                friend for friend in added_friends if friend["user_id"] not in self.friend_ids
            ]
            if len(new_friends) != 1:
                raise ValueError("Could not identify the added friend in Arcaea response")
            first_friend = new_friends[0]
            self._friend_codes[friend_code] = first_friend["user_id"]
            self._friend_codes[user_code] = first_friend["user_id"]
            return first_friend["user_id"], first_friend.get("display_name") or first_friend["name"]

        if resp.get("error_code") == 602:
            raise ValueError(
                "Arcaea returned error_code=602 for this existing friend. "
                "Use the friend's name or internal user_id; friend lists omit friend codes."
            )
        raise ValueError(
            f"Failed to add friend: HTTP {resp.get('_http_status')}, "
            f"error_code={resp.get('error_code')}, logged-in user_code={me_value.get('user_code')}"
        )

    async def fetch_all(self, user_code: str) -> tuple[dict, str]:
        user_id, user_name = await self.get_user_id(user_code)
        semaphore = asyncio.Semaphore(CONCURRENT_REQUEST_LIMIT)

        async def _bounded_fetch(song: dict) -> dict:
            async with semaphore:
                return await self.fetch_play_info(song, user_id)

        tasks = await asyncio.gather(*(_bounded_fetch(song) for song in self._songlist))
        result: dict[str, dict] = {}
        for play_data in tasks:
            if not play_data or not play_data.get("success", True) or "sid" not in play_data:
                continue

            sid = play_data["sid"]
            if sid not in result:
                result[sid] = {}

            play_rating = await self.score_to_rating(
                play_data.get("rating", 0) / 10, play_data.get("score", 0)
            )

            result[sid][play_data["difficulty"]] = {
                "score": play_data.get("score", "N/A"),
                "play_rating": play_rating,
                "best_clear_type": play_data.get("best_clear_type", "N/A"),
                "shiny_perfect_count": play_data.get("shiny_perfect_count", "N/A"),
                "perfect_count": play_data.get("perfect_count", "N/A"),
                "near_count": play_data.get("near_count", "N/A"),
                "miss_count": play_data.get("miss_count", "N/A"),
                "rating": play_data.get("rating", "N/A"),
                "time_played": play_data.get("time_played", "N/A"),
            }

        return result, user_name

    async def _populate_recent_play(self, recent_play: dict, username: str) -> dict:
        recent_play = dict(recent_play)
        this_song = next(
            (song for song in self._songlist if is_same_song(song, recent_play)),
            None,
        )

        if this_song:
            recent_play["rating"] = (
                this_song["rating"] / 10 if this_song["rating"] != -1 else 0
            )

        recent_play["play_rating"] = await self.score_to_rating(
            recent_play.get("rating", 0), recent_play.get("score", 0)
        )
        recent_play["username"] = username
        return recent_play

    async def _fetch_recent_from_db(self, target_user_code: str) -> tuple[dict, str]:
        target_user_code = target_user_code.strip()
        if not target_user_code:
            raise ValueError("Target user code is required.")

        async with AsyncSession(engine, expire_on_commit=False) as session:
            friend = await self._find_friend_in_db(session, target_user_code)

            if friend is not None:
                linked_accounts = (
                    await session.exec(
                        select(ArcaeaAccount)
                        .join(
                            ArcaeaAccountFriendLink,
                            ArcaeaAccount.ID == ArcaeaAccountFriendLink.account_id,
                        )
                        .where(ArcaeaAccountFriendLink.friend_id == friend.ID)
                        .order_by(ArcaeaAccount.ID.asc())
                    )
                ).all()

                last_linked_err: Exception | None = None
                for account in linked_accounts:
                    client = self._create_client(account.email, account.password)
                    try:
                        await client.login()
                        if not client.is_logged_in:
                            raise ValueError(f"Login failed for account {account.email}")
                        recent_play = await client.fetch_recent_play_info(friend.user_id)
                        return recent_play, friend.display_name or friend.name
                    except ValueError as exc:
                        if "has no recent play record" in str(exc):
                            raise
                        last_linked_err = exc
                    finally:
                        await client.close()

                if last_linked_err is not None and not friend.user_code:
                    raise last_linked_err

            self_account = await self._find_account_in_db(session, target_user_code)
            if self_account is not None:
                client = self._create_client(self_account.email, self_account.password)
                try:
                    return await client.fetch_my_recent_play_info()
                finally:
                    await client.close()

            # Select query accounts ordered by friend count ascending (least friends first)
            # pylint: disable=not-callable
            stmt = (
                select(ArcaeaAccount, func.count(ArcaeaAccountFriendLink.friend_id))
                .outerjoin(
                    ArcaeaAccountFriendLink,
                    ArcaeaAccount.ID == ArcaeaAccountFriendLink.account_id,
                )
                .group_by(ArcaeaAccount.ID)
                .order_by(
                    func.count(ArcaeaAccountFriendLink.friend_id).asc(),
                    ArcaeaAccount.ID.asc(),
                )
            )
            account_rows = (await session.exec(stmt)).all()
            if not account_rows:
                raise ValueError(
                    "No Arcaea query account registered. Please use `/arc register` first."
                )

            available_accounts = [
                account
                for account, friend_count in account_rows
                if friend_count < (account.max_friend or 30)
            ]
            if not available_accounts:
                raise ValueError(
                    "All registered Arcaea query accounts have reached their friend limit."
                )

            query_identifier = (
                str(friend.user_id) if friend is not None else target_user_code
            )
            last_error: Exception | None = None
            for account in available_accounts:
                client = self._create_client(account.email, account.password)
                if friend is not None:
                    client._friend_codes[str(friend.user_id)] = friend.user_id
                    if friend.user_code:
                        client._friend_codes[friend.user_code] = friend.user_id
                try:
                    target_user_id, username = await client.get_user_id(query_identifier)
                    recent_play = await client.fetch_recent_play_info(target_user_id)
                except ValueError as exc:
                    last_error = exc
                    if "error_code=602" in str(exc) and len(available_accounts) > 1:
                        continue
                    raise
                finally:
                    await client.close()

                inferred_user_code: str | None = friend.user_code if friend else None
                if inferred_user_code is None and target_user_code not in (
                    username,
                    str(target_user_id),
                ):
                    inferred_user_code = target_user_code

                friend_obj = await self._upsert_friend(
                    session,
                    user_id=target_user_id,
                    name=username,
                    display_name=username,
                    user_code=inferred_user_code,
                )
                existing_link = (
                    await session.exec(
                        select(ArcaeaAccountFriendLink).where(
                            ArcaeaAccountFriendLink.account_id == account.ID,
                            ArcaeaAccountFriendLink.friend_id == friend_obj.ID,
                        )
                    )
                ).first()
                if existing_link is None:
                    session.add(
                        ArcaeaAccountFriendLink(
                            account_id=account.ID,
                            friend_id=friend_obj.ID,
                        )
                    )
                await session.commit()
                return recent_play, username

            assert last_error is not None
            raise last_error

    async def fetch_recent(self, target_user_code: str | None = None) -> dict:
        if self.email and self.password:
            if target_user_code is None:
                recent_play, username = await self.fetch_my_recent_play_info()
            else:
                target_user_id, username = await self.get_user_id(target_user_code)
                recent_play = await self.fetch_recent_play_info(target_user_id)
        else:
            if target_user_code is None:
                raise ValueError("Target user code is required.")
            recent_play, username = await self._fetch_recent_from_db(target_user_code)

        return await self._populate_recent_play(recent_play, username)


class AssetFetcher:
    ROOT_PATH = Path("/opt/arcaea/assets")
    logger = setup_package_logger(f"{__name__}.AssetFetcher")
    _songlist_map: dict[str, Song | DeletedSong] | None = None

    @classmethod
    def get_songlist_map(cls) -> dict[str, Song | DeletedSong]:
        if cls._songlist_map is None:
            songlist_path = cls.ROOT_PATH / "songs" / "songlist"
            if not songlist_path.exists():
                cls.logger.error("Songlist file not found: %s", songlist_path)
                raise FileNotFoundError(f"Songlist file not found: {songlist_path}")
            with songlist_path.open(encoding="utf-8") as file:
                songlist = SongList.model_validate(json.load(file))
            cls._songlist_map = {song.id: song for song in songlist.songs}
        return cls._songlist_map

    @classmethod
    async def get_thumbnail(cls, grade: int) -> File:
        thumbnail_path = cls.ROOT_PATH / "img" / "grade" / f"{GRADE_SUFFIX[grade]}.png"
        if not thumbnail_path.exists():
            cls.logger.error("Thumbnail not found: %s", thumbnail_path)
            raise FileNotFoundError(f"Thumbnail not found: {thumbnail_path}")
        return File(thumbnail_path)

    @classmethod
    async def get_song_cover(cls, song_id: str, difficulty: int) -> File:
        song_data = cls.get_songlist_map().get(song_id)
        if not isinstance(song_data, Song):
            raise KeyError(f"Active song '{song_id}' not found in songlist")

        song_name = f"dl_{song_id}" if song_data.remote_dl else song_id
        diff_data = next(
            (diff for diff in song_data.difficulties if diff.ratingClass == difficulty),
            None,
        )
        difficulty_name = (
            str(difficulty) if (diff_data and diff_data.jacketOverride) else "base"
        )

        for prefix in ("1080_", ""):
            song_cover_path = cls.ROOT_PATH / "songs" / song_name / f"{prefix}{difficulty_name}.jpg"
            cls.logger.debug("Trying to fetch: %s", song_cover_path)
            if song_cover_path.exists():
                return File(song_cover_path)

        # Fallback to base cover if override cover was not found
        for prefix in ("1080_", ""):
            base_cover_path = cls.ROOT_PATH / "songs" / song_name / f"{prefix}base.jpg"
            if base_cover_path.exists():
                return File(base_cover_path)

        raise FileNotFoundError(f"Song cover not found for song '{song_id}' in {cls.ROOT_PATH}")


class ArcaeaResponseFormatter:
    @staticmethod
    def _resolve_song_title(result: dict, song_id: str) -> str:
        title_info = result.get("title")
        if isinstance(title_info, dict):
            title = title_info.get("ja") or title_info.get("en")
            if title:
                return title

        try:
            song_data = AssetFetcher.get_songlist_map().get(song_id)
            if isinstance(song_data, Song):
                return song_data.title_localized.ja or song_data.title_localized.en
        except FileNotFoundError:
            pass

        return song_id

    @classmethod
    async def recent_score(cls, result: dict) -> tuple[Embed, str, File, File]:
        song_id = result["song_id"]
        difficulty = result["difficulty"]

        song_cover = await AssetFetcher.get_song_cover(song_id, difficulty)
        grade = await ScoreUtils.get_grade(result["score"])
        thumbnail: File = await AssetFetcher.get_thumbnail(grade)

        rating_val = round(result["play_rating"], 2)
        rating = str(rating_val) if rating_val > 0 else "Too low to calculate"

        username = result["username"]
        song_title = cls._resolve_song_title(result, song_id)
        played_at = datetime.fromtimestamp(result["time_played"] // 1000, tz=TZ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        embed = await BaseClassMixin.create_embed(
            f"User: {username}\nRecent Play Info",
            f"{song_title} [{DIFFICULTY_ABBR[difficulty]}]「{GRADE_NAMES[grade]}」",
            Field(name="Played at", value=played_at, inline=False),
            Field(name="Rating", value=rating, inline=True),
            Field(name="Score", value=result["score"], inline=False),
            Field(name="Grade", value=GRADE_NAMES[grade], inline=True),
            Field(name="Difficulty", value=DIFFICULTY_NAMES[difficulty], inline=True),
            Field(name="Chart Constant", value=round(result.get("rating", 0), 1), inline=True),
            color=Color.from_str(DIFFICULTY_COLOR_LIST[difficulty]),
            image_url=f"attachment://{song_cover.filename}",
            thumbnail_url=f"attachment://{thumbnail.filename}",
        )
        return embed, username, thumbnail, song_cover
