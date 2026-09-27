from typing import Literal

from pydantic import BaseModel


class Segment(BaseModel):
    id: str
    start_time: float
    end_time: float
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
    error: str | None = None


class Keyframe(BaseModel):
    id: str
    time: float
    image_path: str | None = None


class RenderPlan(BaseModel):
    project_id: str | None = None
    total_duration: float = 60
    keyframes: list[Keyframe] = []
    segments: list[Segment] = []


class SelectionState(BaseModel):
    """What's currently selected in the canvas, mirrored to the Python
    side so NiceGUI's sidebar form can display/edit it. Single-user app,
    so this lives as one in-memory object rather than per-session state —
    revisit if this ever needs to support multiple concurrent projects
    open at once."""
    project_id: str | None = None
    keyframe_id: str | None = None
    time: float = 0
    prompt: str = ""
    image_path: str | None = None
