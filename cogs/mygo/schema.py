from pydantic import BaseModel, computed_field

from database import SentenceItem

# --------------- Pydantic Model --------------- #


class Disposition(BaseModel):
    default: int
    dub: int
    original: int
    comment: int
    lyrics: int
    karaoke: int
    forced: int
    hearing_impaired: int
    visual_impaired: int
    clean_effects: int
    attached_pic: int
    timed_thumbnails: int
    non_diegetic: int | None = None
    captions: int
    descriptions: int
    metadata: int
    dependent: int
    still_image: int


class StreamTags(BaseModel):
    language: str
    handler_name: str
    vendor_id: str


class FFProbeStream(BaseModel):
    index: int
    codec_name: str
    codec_long_name: str
    profile: str | None = None
    codec_type: str
    codec_tag_string: str
    codec_tag: str
    width: int | None = None
    height: int | None = None
    coded_width: int | None = None
    coded_height: int | None = None
    closed_captions: int | None = None
    film_grain: int | None = None
    has_b_frames: int | None = None
    sample_aspect_ratio: str | None = None
    display_aspect_ratio: str | None = None
    pix_fmt: str | None = None
    level: int | None = None
    color_range: str | None = None
    color_space: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    refs: int | None = None
    id: str
    r_frame_rate: str
    avg_frame_rate: str
    time_base: str
    start_pts: int
    start_time: str
    duration_ts: int
    duration: str
    bit_rate: str
    nb_frames: str
    disposition: Disposition
    tags: StreamTags
    sample_fmt: str | None = None
    sample_rate: str | None = None
    channels: int | None = None
    channel_layout: str | None = None
    bits_per_sample: int | None = None
    initial_padding: int | None = None
    extradata_size: int | None = None

    @computed_field
    @property
    def frame_rate(self) -> float:
        numerator, denominator = map(int, self.r_frame_rate.split("/"))
        return numerator / denominator

    @computed_field
    @property
    def total_frame(self) -> int:
        return int(self.nb_frames)


class FormatTags(BaseModel):
    major_brand: str
    minor_version: str
    compatible_brands: str
    encoder: str


class Format(BaseModel):
    filename: str
    nb_streams: int
    nb_programs: int
    nb_stream_groups: int | None = None
    format_name: str
    format_long_name: str
    start_time: str
    duration: str
    size: str
    bit_rate: str
    probe_score: int
    tags: FormatTags


class FFProbeResponse(BaseModel):
    streams: list[FFProbeStream]
    format: Format


class SubtitleItem(BaseModel):
    result: list[SentenceItem]
