import secrets
from typing import Literal

from pydantic import BaseModel, Field


def new_segment_id() -> str:
    return f"seg{secrets.token_hex(4)}"


class Segment(BaseModel):
    id: str = Field(default_factory=new_segment_id)
    duration: float
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
        return round(sum(s.duration for s in self.segments), 1)

    def index_of(self, seg_id: str | None) -> int | None:
        return next((i for i, s in enumerate(self.segments) if s.id == seg_id), None)

    def get(self, seg_id: str | None) -> Segment | None:
        i = self.index_of(seg_id)
        return self.segments[i] if i is not None else None

    def span_of(self, seg_id: str) -> tuple[float, float] | None:
        """(start, end) in seconds of a segment, or None if it isn't in the plan."""
        t = 0.0
        for s in self.segments:
            end = round(t + s.duration, 1)
            if s.id == seg_id:
                return t, end
            t = end
        return None


class OpenProjectRequest(BaseModel):
    """The absolute server-side path of the folder to open as the active
    project. Created (with its image/clip subfolders) if it doesn't exist."""
    path: str