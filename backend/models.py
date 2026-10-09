import secrets
from typing import Literal

from pydantic import BaseModel, Field, model_validator


def new_segment_id() -> str:
    return f"seg{secrets.token_hex(4)}"


# Frame rate assumed for a segment until its rendered clip is probed (the real
# rate is then stored on ``Segment.fps``). Used to turn user-facing seconds into
# an integer frame count when creating or resizing segments.
DEFAULT_FPS = 24.0


class Segment(BaseModel):
    id: str = Field(default_factory=new_segment_id)
    # Length of this segment in *frames* (the storage unit). Convert to seconds
    # for display/layout via ``duration_seconds`` using the segment's own fps.
    duration_frames: int
    # Frame rate associated with this segment's clip. Defaults to DEFAULT_FPS and
    # is updated from a ffprobe of the rendered clip once one exists (generate.py).
    fps: float = DEFAULT_FPS
    prompt: str = ""
    start_image_path: str | None = None
    end_image_path: str | None = None
    source_clip_path: str | None = None
    trim_in: float | None = None
    trim_out: float | None = None
    speed_factor: float | None = None
    status: Literal["empty", "queued", "rendering", "done", "error"] = "empty"
    output_path: str | None = None
    # Earlier renders of this segment, newest first (content-addressed clip
    # paths). A re-render pushes the previous take here instead of overwriting
    # its file, so old takes stay playable and reusable.
    history: list[str] = []
    error: str | None = None

    @model_validator(mode='before')
    @classmethod
    def _migrate_legacy_duration(cls, data):
        """Load old timeline.json files that stored the length as a float number of
        seconds under ``duration``. Convert it to integer frames at the default rate
        (24fps) so both file formats load into ``duration_frames``."""
        if isinstance(data, dict) and 'duration_frames' not in data:
            legacy = data.get('duration')
            if legacy is not None:
                fps = float(data.get('fps') or DEFAULT_FPS)
                data['duration_frames'] = max(1, int(round(float(legacy) * fps)))
        return data

    @property
    def duration_seconds(self) -> float:
        """This segment's length in seconds, at its own frame rate."""
        return self.duration_frames / self.fps if self.fps else 0.0


class RenderPlan(BaseModel):
    """The whole timeline: an ordered list of segments laid end to end from
    t=0. Start/end times are derived from the durations (see ``span_of``).

    Older timeline.json files may still carry ``keyframes``, ``total_duration``
    and per-segment ``start_time``/``end_time``; pydantic ignores them."""
    # Project-wide generation resolution ([width, height]) applied to every
    # rendered clip; edit via the "Project" tab in the UI.
    width: int = 768
    height: int = 448
    segments: list[Segment] = []

    @property
    def duration(self) -> float:
        """Total timeline length, in seconds (each segment at its own fps)."""
        return round(sum(s.duration_seconds for s in self.segments), 1)

    def index_of(self, seg_id: str | None) -> int | None:
        return next((i for i, s in enumerate(self.segments) if s.id == seg_id), None)

    def get(self, seg_id: str | None) -> Segment | None:
        i = self.index_of(seg_id)
        return self.segments[i] if i is not None else None

    def span_of(self, seg_id: str) -> tuple[float, float] | None:
        """(start, end) in seconds of a segment, or None if it isn't in the plan."""
        t = 0.0
        for s in self.segments:
            end = round(t + s.duration_seconds, 1)
            if s.id == seg_id:
                return t, end
            t = end
        return None


class OpenProjectRequest(BaseModel):
    """The absolute server-side path of the folder to open as the active
    project. Created (with its image/clip subfolders) if it doesn't exist."""
    path: str