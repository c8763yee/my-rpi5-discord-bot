import datetime
import os
from sys import version_info
from pathlib import Path
from textwrap import dedent

from pydantic import ConfigDict, computed_field
from sqlalchemy.dialects.mysql import TEXT
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import Column, Field, Relationship, SQLModel

from core import load_env
from loggers import TZ

if version_info < (3, 11):
    from strenum import StrEnum
else:
    from enum import StrEnum
load_env(Path.cwd() / "env" / "db.env")
DATABASE_URL: str = (
    # pylint: disable=consider-using-f-string
    "mysql+aiomysql://"
    "{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:3306/{MYSQL_DATABASE}?charset=UTF8mb4"
).format(
    MYSQL_USER=os.getenv("MYSQL_USER", "root"),
    MYSQL_PASSWORD=os.getenv("MYSQL_PASSWORD", "root"),
    MYSQL_HOST=os.getenv("MYSQL_HOST", "localhost"),
    MYSQL_DATABASE=os.getenv("MYSQL_DATABASE", "default"),
)
engine = create_async_engine(DATABASE_URL, echo=False)


# --------------- SQL Model --------------- #
class BaseSQLModel(SQLModel):
    __abstract__ = True
    __table_args__ = {
        "extend_existing": True,
        "mysql_charset": "utf8mb4",
        "mysql_default_charset": "utf8mb4",
    }

    ID: int = Field(primary_key=True)
    create_time: datetime.datetime = Field(default_factory=lambda: datetime.datetime.now(TZ))


# --------------- MyGO --------------- #
class EpisodeItem(BaseSQLModel, table=True):
    __tablename__ = "episode"
    model_config = ConfigDict(title=__tablename__)

    episode: str
    total_frame: int
    frame_rate: float


class SentenceItem(BaseSQLModel, table=True):
    def __str__(self):
        return dedent(
            f"""
            Episode: {self.episode}
            Frame Start: {self.frame_start}
            Frame End: {self.frame_end}
            Text: {self.text}

            -----------------
            command:
                <prefix>mygo gif {self.episode} {self.frame_start} {self.frame_end}
                <prefix>mygo frame {self.episode} <number in {self.frame_start} ~ {self.frame_end}>

            """
        )

    __tablename__ = "sentence"
    model_config = ConfigDict(title=__tablename__)

    text: str
    episode: str
    frame_start: int
    frame_end: int
    segment_id: int = Field(index=True)

    @computed_field
    @property
    def gif_command(self) -> str:
        return f"mygo gif {self.episode} {self.frame_start} {self.frame_end}"

    @computed_field
    @property
    def frame_command(self) -> str:
        return f"mygo frame {self.episode} <number in {self.frame_start} ~ {self.frame_end}>"


# --------------- Kasa --------------- #
class Emeter(BaseSQLModel):
    __abstract__ = True
    name: str = Field(nullable=False)
    status: bool = Field(nullable=False)
    voltage: float = Field(nullable=False, alias="V")
    current: float = Field(nullable=False, alias="A")
    power: float = Field(nullable=False, alias="W")
    total_wh: float = Field(nullable=False)


class HS300(Emeter, table=True):
    __tablename__ = "hs300"


class PC(Emeter, table=True):
    __tablename__ = "pc"


class ScreenFHD(Emeter, table=True):
    __tablename__ = "screen_fhd"


class Screen2K(Emeter, table=True):
    __tablename__ = "screen_2k"


class NintendoSwitch(Emeter, table=True):
    __tablename__ = "switch"


class PhoneCharge(Emeter, table=True):
    __tablename__ = "phone"


class RaspberryPi(Emeter, table=True):
    __tablename__ = "pi"

    # drop all database then create


# --------------- OpenAI Chat History --------------- #
class Role(StrEnum):
    user = "user"
    assistant = "assistant"
    system = "system"


class ChatHistory(BaseSQLModel, table=True):
    chat_id: str = Field(foreign_key="chat.history_id", index=True, ondelete="CASCADE")
    chat: "Chat" = Relationship(back_populates="history")
    role: Role
    content: str = Field(sa_column=Column(TEXT))


class Chat(BaseSQLModel, table=True):
    __tablename__ = "chat"
    history_id: str = Field(index=True)
    index: int = Field(default=0)
    history: list[ChatHistory] = Relationship(back_populates="chat", cascade_delete=True)


async def recreate_model():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)


if __name__ == "__main__":
    from asyncio import get_event_loop, set_event_loop

    loop = get_event_loop()
    set_event_loop(loop)
    loop.run_until_complete(recreate_model())
