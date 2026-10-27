import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from cogs.arcaea import utils as arcaea_utils
from cogs.arcaea.const import GradeEnum
from cogs.arcaea.schema import Song
from cogs.arcaea.utils import APIUtils, ScoreUtils
from database import ArcaeaAccount, ArcaeaAccountFriendLink, ArcaeaFriend


class ScoreUtilsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.utils = ScoreUtils()

    async def test_rating_to_score_caps_perfect_score(self):
        score = await self.utils.rating_to_score(user_rating=15.0, song_rating=10.0)

        self.assertEqual(score, 10_000_000)

    async def test_step_conversions_reject_non_positive_character_stat(self):
        with self.assertRaisesRegex(ValueError, "greater than 0"):
            await self.utils.step_to_rating(char_step=0, world_step=1)
        with self.assertRaisesRegex(ValueError, "greater than 0"):
            await self.utils.rating_to_step(char_step=0, rating=1)

    async def test_score_below_c_is_grade_d(self):
        self.assertEqual(await self.utils.get_grade(0), GradeEnum.D)


class APIUtilsTest(unittest.IsolatedAsyncioTestCase):
    async def test_login_requires_credentials_before_opening_session(self):
        utils = APIUtils(email="", password="")
        utils.open_session = AsyncMock()

        with self.assertRaisesRegex(ValueError, "credentials are not configured"):
            await utils.login()

        utils.open_session.assert_not_awaited()

    async def test_existing_friend_is_reused_without_deletion(self):
        utils = APIUtils(email="test@example.com", password="password")
        utils.is_logged_in = True
        utils._session = _Session(
            {
                "success": True,
                "value": {
                    "user_id": 1,
                    "friends": [{"user_id": 2, "user_code": "123456789", "name": "friend"}],
                },
            }
        )
        utils.add_friend = AsyncMock()
        utils.del_friend = AsyncMock()

        user_id, username = await utils.get_user_id("123456789")

        self.assertEqual((user_id, username), (2, "friend"))
        utils.add_friend.assert_not_awaited()
        utils.del_friend.assert_not_awaited()
        self.assertIn(f"{utils.base_url}/webapi/friend/me", utils._session.urls)

    async def test_friend_recent_uses_separate_friend_endpoint(self):
        utils = APIUtils(email="test@example.com", password="password")
        utils._session = _Session({"success": True, "value": {"user_id": 1}})
        recent = {"song_id": "test", "score": 9900000}
        utils.get_friends = AsyncMock(return_value=[{"user_id": 2, "recent_score": [recent]}])

        self.assertEqual(await utils.fetch_recent_play_info(2), recent)
        utils.get_friends.assert_awaited_once()

    async def test_add_failure_keeps_http_and_application_error_separate(self):
        utils = APIUtils(email="test@example.com", password="password")
        utils.is_logged_in = True
        utils._session = _Session({"success": True, "value": {"user_id": 1}})
        utils.get_friends = AsyncMock(return_value=[])
        utils.add_friend = AsyncMock(
            return_value={"success": False, "_http_status": 404, "error_code": 401}
        )

        with self.assertRaisesRegex(ValueError, "HTTP 404, error_code=401"):
            await utils.get_user_id("123456789")


class DatabaseAccountPoolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.test_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        self.original_engine = arcaea_utils.engine
        arcaea_utils.engine = self.test_engine
        APIUtils._songlist = [{"sid": "test_song", "difficulty": 2, "rating": 100}]

    async def asyncTearDown(self):
        arcaea_utils.engine = self.original_engine
        APIUtils._songlist = []
        await self.test_engine.dispose()

    async def test_register_account_syncs_friends_and_many_to_many_links(self):
        utils = APIUtils()

        def make_client(email: str, password: str) -> APIUtils:
            client = APIUtils(email=email, password=password)
            client.is_logged_in = True
            client.login = AsyncMock()
            client.close = AsyncMock()
            if email == "acc1@example.com":
                client.get_me = AsyncMock(
                    return_value={
                        "user_id": 10,
                        "user_code": "111111111",
                        "name": "acc1",
                        "display_name": "Account 1",
                        "max_friend": 30,
                    }
                )
                client.get_friends = AsyncMock(
                    return_value=[
                        {"user_id": 100, "name": "shared_friend", "display_name": "Shared"},
                        {"user_id": 101, "name": "only_acc1", "display_name": "Only 1"},
                    ]
                )
            else:
                client.get_me = AsyncMock(
                    return_value={
                        "user_id": 20,
                        "user_code": "222222222",
                        "name": "acc2",
                        "display_name": "Account 2",
                        "max_friend": 30,
                    }
                )
                client.get_friends = AsyncMock(
                    return_value=[
                        {"user_id": 100, "name": "shared_friend", "display_name": "Shared"},
                    ]
                )
            return client

        utils._create_client = make_client

        acc1, count1 = await utils.register_account("acc1@example.com", "pw1")
        acc2, count2 = await utils.register_account("acc2@example.com", "pw2")

        self.assertEqual(count1, 2)
        self.assertEqual(count2, 1)
        self.assertEqual(acc1.user_id, 10)
        self.assertEqual(acc2.user_id, 20)

        async with AsyncSession(self.test_engine) as session:
            friends = (await session.exec(select(ArcaeaFriend))).all()
            links = (await session.exec(select(ArcaeaAccountFriendLink))).all()

        self.assertEqual(len(friends), 2)
        self.assertEqual(len(links), 3)

    async def test_fetch_recent_picks_account_with_fewest_friends_and_links_friend(self):
        async with AsyncSession(self.test_engine) as session:
            acc1 = ArcaeaAccount(
                email="acc1@example.com",
                password="pw1",
                user_id=10,
                name="acc1",
                max_friend=30,
            )
            acc2 = ArcaeaAccount(
                email="acc2@example.com",
                password="pw2",
                user_id=20,
                name="acc2",
                max_friend=30,
            )
            existing_friend = ArcaeaFriend(user_id=100, name="existing", user_code="100000000")
            session.add_all([acc1, acc2, existing_friend])
            await session.flush()
            session.add(ArcaeaAccountFriendLink(account_id=acc1.ID, friend_id=existing_friend.ID))
            await session.commit()

        used_emails: list[str] = []

        def make_client(email: str, password: str) -> APIUtils:
            used_emails.append(email)
            client = APIUtils(email=email, password=password)
            client.is_logged_in = True
            client.login = AsyncMock()
            client.close = AsyncMock()
            client.get_user_id = AsyncMock(return_value=(200, "NewPlayer"))
            client.fetch_recent_play_info = AsyncMock(
                return_value={"song_id": "test_song", "difficulty": 2, "score": 10_000_000}
            )
            return client

        utils = APIUtils()
        utils._create_client = make_client

        result = await utils.fetch_recent("999888777")
        self.assertEqual(used_emails, ["acc2@example.com"])
        self.assertEqual(result["username"], "NewPlayer")
        self.assertEqual(result["play_rating"], 12.0)

        # Second query for the same user_code reuses the linked account (acc2) directly
        used_emails.clear()
        result2 = await utils.fetch_recent("999888777")
        self.assertEqual(used_emails, ["acc2@example.com"])
        self.assertEqual(result2["username"], "NewPlayer")


class SongSchemaTest(unittest.TestCase):
    def test_overflowing_timestamp_is_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            original_root = Path("/opt/arcaea/assets/songs")
            try:
                from cogs.arcaea import schema

                schema.SONGS_ROOT = Path(directory)
                song = Song.model_validate(
                    {
                        "id": "test_song",
                        "title_localized": {"en": "Test Song"},
                        "artist": "Artist",
                        "bpm": "120",
                        "bpm_base": 120,
                        "audioPreview": 0,
                        "audioPreviewEnd": 1000,
                        "side": 0,
                        "bg": "base",
                        "date": 2**31,
                        "version": "1.0",
                        "difficulties": [],
                    }
                )
            finally:
                schema.SONGS_ROOT = original_root

        self.assertLess(song.date, 2**31)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self):
        return self.payload


class _Session:
    closed = False

    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def get(self, *args, **kwargs):
        self.urls.append(args[0])
        return _Response(self.payload)
