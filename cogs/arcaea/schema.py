import datetime
import warnings
from pathlib import Path
from random import randint
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

from loggers import TZ, setup_package_logger

from .const import MAX_INT, MAX_RATING, MIN_INT, DifficultyEnum

SONGS_ROOT: Path = Path("/opt/arcaea/assets/songs")

logger = setup_package_logger("cogs.arcaea.schema")


def ascii_validate(model: BaseModel, *fields: str) -> BaseModel:
    """Check if all specified string fields are ASCII only."""
    for field in fields:
        attr = getattr(model, field, None)
        if attr is None:
            continue

        if not isinstance(attr, str) or not attr.isascii():
            raise ValueError(f"`{model.__class__.__name__}.{field}` must be ascii only")

    return model


class DifficultyValidator(BaseModel):
    @model_validator(mode="after")
    def ascii_field_check(self) -> "Difficulty":
        return ascii_validate(self, "jacket_night", "bg", "bg_inverse")

    @model_validator(mode="after")
    def hidden_until_unlocked_check(self) -> "Difficulty":
        if self.hidden_until is not None and self.hidden_until_unlocked is False:
            warnings.warn("hidden_until_unlocked is False but hidden_until is present")
            logger.debug(self.model_dump_json(indent=2, exclude_none=True))
            self.hidden_until_unlocked = True
        return self

    @model_validator(mode="after")
    def deprecated_values(self) -> "Difficulty":
        deprecated_fields = ["plusFingers"]
        for field in deprecated_fields:
            if getattr(self, field, None) is not None:
                warnings.warn(
                    f"deprecated field {field} is present in:\n"
                    f"{self.model_dump_json(indent=2, exclude_none=True)}"
                )
                setattr(self, field, None)
        return self


class PacksValidator(BaseModel):
    @model_validator(mode="after")
    def ascii_field_check(self) -> "Packs":
        return ascii_validate(self, "id", "pack_parent")


class SongsValidator(BaseModel):
    # ------------------- class variables -------------------
    _id_counter: ClassVar[int] = -1  # start from 0

    @classmethod
    def _generate_id(cls) -> int:
        cls._id_counter += 1
        return cls._id_counter

    # ------------------- pre-validation -------------------

    @model_validator(mode="before")
    @classmethod
    def force_override_idx(cls, values: dict) -> dict:
        if isinstance(values, dict):
            values["idx"] = cls._generate_id()
        return values

    # ------------------- post-validation -------------------

    @model_validator(mode="after")
    def base_difficulty_file_check(self) -> "Song":
        for diff in self.difficulties:
            rating_class = diff.ratingClass
            if (
                not (SONGS_ROOT / self.id / f"{rating_class}.aff").exists()
                and diff.rating != -1
                and not getattr(self, "remote_dl", False)
            ):
                warnings.warn(f"base difficulty file {rating_class}.aff not found in {self.id}")

        return self

    @model_validator(mode="after")
    def byd_local_unlock_check(self) -> "Song":
        for diff in self.difficulties:
            if diff.ratingClass == DifficultyEnum.BEYOND and self.byd_local_unlock is None:
                self.byd_local_unlock = True
                break
        if self.world_unlock is not None:
            self.byd_local_unlock = not self.world_unlock
        return self

    @model_validator(mode="after")
    def timestamp_overflow_check(self) -> "Song":
        if self.date > MAX_INT:
            warnings.warn(f"timestamp overflow detected in {self.id}, setting to current time")
            self.date = int(datetime.datetime.now(TZ).timestamp())

        return self

    @model_validator(mode="after")
    def ascii_field_check(self) -> "Song":
        return ascii_validate(self, "id", "bg", "bg_inverse")

    @model_validator(mode="after")
    def title_check(self) -> "Song":
        if getattr(self.title_localized, "en", None) is None:
            raise ValueError("`title_localized.en` must be present")

        if self.title_localized.en and not self.title_localized.en.isascii():
            self.title_localized.en = self.id

        return self

    @model_validator(mode="after")
    def difficulties_check(self) -> "Song":
        current_diff = set()
        for diff in self.difficulties:
            if diff.ratingClass in current_diff:
                raise ValueError(f"duplicate ratingClass {diff.ratingClass} in {self.id}")

            current_diff.add(diff.ratingClass)

        if DifficultyEnum.BEYOND not in current_diff:
            self.byd_local_unlock = None

        if DifficultyEnum.BEYOND in current_diff and DifficultyEnum.ETERNAL in current_diff:
            raise ValueError(
                f"ratingClass 3 and 4 cannot be present at the same time in {self.id}"
            )

        base_difference = {
            DifficultyEnum.PAST,
            DifficultyEnum.PRESENT,
            DifficultyEnum.FUTURE,
        }.difference(current_diff)
        if base_difference:
            for diff in base_difference:
                self.difficulties.append(
                    Difficulty(
                        ratingClass=diff,
                        chartDesigner="chart",
                        jacketDesigner="",
                    )
                )

        self.difficulties.sort(key=lambda x: x.ratingClass)
        return self

    @model_validator(mode="after")
    def rating_check(self) -> "Song":
        song_path: Path = SONGS_ROOT / self.id
        for diff in self.difficulties:
            aff_path = song_path / f"{diff.ratingClass}.aff"
            ogg_path = song_path / f"{diff.ratingClass}.ogg"
            jpg_path = song_path / f"{diff.ratingClass}.jpg"
            jpg_1080_path = song_path / f"1080_{diff.ratingClass}.jpg"

            if aff_path.exists() and diff.rating == -1:
                diff.rating = randint(1, MAX_RATING[diff.ratingClass])
            if ogg_path.exists():
                diff.audioOverride = True
            if jpg_path.exists() or jpg_1080_path.exists():
                diff.jacketOverride = True

        return self


class JacketLocalized(BaseModel):
    ja: bool | None = None
    en: bool | None = None
    zh_hans: bool | None = Field(None, alias="zh-Hans")
    zh_hant: bool | None = Field(None, alias="zh-Hant")
    ko: bool | None = None


class Localized(BaseModel):
    @model_validator(mode="after")
    def rename_title_localized(self) -> "Localized":
        if getattr(self, "en", None) is None:
            raise ValueError("`title_localized.en` must be present")

        if self.en and not self.en.isascii():
            self.zh_hant = self.ja = self.en

        return self

    en: str
    ja: str | None = None
    zh_hans: str | None = Field(None, alias="zh-Hans")
    zh_hant: str | None = Field(None, alias="zh-Hant")
    ko: str | None = None


class AdditionalFile(BaseModel):
    file_name: str
    requirement: Literal["required", "hi_res", "low_res"]


class DayNight(BaseModel):
    @model_validator(mode="after")
    def ascii_field_check(self) -> "DayNight":
        return ascii_validate(self, "day", "night")

    day: str
    night: str


class Search(BaseModel):
    ja: list[str] | None = None
    zh_hans: list[str] | None = Field(None, alias="zh-Hans")
    zh_hant: list[str] | None = Field(None, alias="zh-Hant")
    ko: list[str] | None = None


class Difficulty(DifficultyValidator):
    ratingClass: DifficultyEnum
    chartDesigner: str
    jacketDesigner: str
    rating: int = Field(-1, ge=MIN_INT, le=MAX_INT)
    ratingPlus: bool = False
    plusFingers: bool | None = None
    title_localized: Localized | None = None
    artist: str | None = None
    bpm: str | None = None
    bpm_base: float | None = None
    jacket_night: str | None = None
    jacketOverride: bool | None = None
    audioOverride: bool | None = None
    hidden_until: str | None = None
    hidden_until_unlocked: bool | None = False
    bg: str | None = None
    bg_inverse: str | None = None
    world_unlock: bool | None = False
    date: int | None = Field(None, ge=MIN_INT, le=MAX_INT)
    version: str | None = None


class Song(SongsValidator):
    idx: int = Field(default_factory=SongsValidator._generate_id)
    id: str
    title_localized: Localized
    artist: str
    search_title: Search | None = None
    search_artist: Search | None = None
    artist_localized: Localized | None = None
    bpm: str
    bpm_base: float
    set: str = "single"
    purchase: str = ""
    audioPreview: int = Field(ge=MIN_INT, le=MAX_INT)
    audioPreviewEnd: int = Field(ge=MIN_INT, le=MAX_INT)
    side: Literal[0, 1, 2, 3, 4]
    bg: str
    bg_inverse: str | None = None
    bg_daynight: DayNight | None = None
    # Values above MAX_INT are repaired by timestamp_overflow_check.
    date: int = Field(ge=MIN_INT)
    version: str
    world_unlock: bool | None = False
    remote_dl: bool | None = False
    byd_local_unlock: bool | None = True
    songlist_hidden: bool | None = False
    no_pp: bool | None = None
    source_localized: Localized | None = None
    source_copyright: str | None = None
    no_stream: bool | None = None
    jacket_localized: JacketLocalized | None = None
    difficulties: list[Difficulty]
    additional_files: list[AdditionalFile] | None = None


class DeletedSong(BaseModel):
    idx: int = Field(default_factory=SongsValidator._generate_id)
    id: str
    deleted: bool


class SongList(BaseModel):
    songs: list[Song | DeletedSong]


class Packs(PacksValidator):
    id: str
    custom_banner: bool | None = None
    plus_character: int = -1
    pack_parent: str | None = None
    is_extend_pack: bool | None = None
    is_active_extend_pack: bool | None = None
    small_pack_image: bool | None = None
    cutout_pack_image: bool | None = None
    name_localized: Localized
    description_localized: Localized | None = None
    section: Literal["archive", "free", "mainstory", "sidestory", "collab"] = "free"


class PackList(BaseModel):
    packs: list[Packs]
