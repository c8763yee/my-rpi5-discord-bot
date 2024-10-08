import json
from base64 import b64encode as be
from pathlib import Path

from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from cogs.mygo.schema import SubtitleItem
from cogs.mygo.utils import SubtitleUtils
from database import EpisodeItem, SentenceItem, engine
from loggers import setup_package_logger

logger = setup_package_logger("core.func")


def encode_image_to_b64(image_path: str | Path | bytes) -> str:
    if isinstance(image_path, bytes):
        return be(image_path).decode("utf-8")

    if isinstance(image_path, str):
        image_path = Path(image_path)

    with image_path.open("rb") as image:
        return be(image.read()).decode("utf-8")


async def init_models():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def db_insert_subtitle_data(data: SubtitleItem, session: AsyncSession, update: bool = False):
    for i, item in enumerate(data.result):
        # Update if sentence already exists (via segment_id)
        # NOTE: ID is auto-incremented, so we can't use it to check for duplicates
        old_row = (
            await session.exec(
                select(SentenceItem).where(SentenceItem.segment_id == item.segment_id)
            )
        ).first()

        if old_row is not None and update is True:
            old_row.sqlmodel_update(
                item.model_dump(
                    include=["text", "episode", "frame_start", "frame_end", "segment_id"]
                )
            )
            session.add(old_row)
            data.result[i] = old_row  # prevent duplicate insert

        elif old_row is None:
            session.add(item)

        else:
            data.result[i] = None

    await session.commit()
    for item in data.result:
        # ignore non-inserted or updated items
        if item is None:
            continue


async def db_insert_episode(episode: str, session: AsyncSession, update: bool = False):
    data = await SubtitleUtils.get_total_frame_number(episode)
    insert_item = EpisodeItem(
        episode=episode, total_frame=data.total_frame, frame_rate=data.frame_rate
    )
    if (old_row := await session.get(EpisodeItem, episode)) is not None and update is True:
        old_row.sqlmodel_update(
            insert_item.model_dump(include=["episode", "total_frame", "frame_rate"])
        )
        session.add(old_row)

    elif old_row is None:
        session.add(insert_item)

    await session.commit()


async def init():
    await init_models()

    with (Path.cwd() / "json_data" / "mygo_detail.json").open("r", encoding="utf-8") as file:
        data = SubtitleItem.model_validate(json.load(file), strict=True)

    async with AsyncSession(engine) as session:
        for episode in ["1-3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13"]:
            await db_insert_episode(episode, session, update=True)

        await db_insert_subtitle_data(data, session, update=True)
