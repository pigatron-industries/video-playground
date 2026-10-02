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
    # Earlier renders of this segment, newest first (content-addressed clip
    # paths). A re-render pushes the previous take here instead of overwriting
    # its file, so old takes stay playable and reusable.
    history: list[str] = []
    error: str | None = None


class Keyframe(BaseModel):
    id: str
    time: float


class RenderPlan(BaseModel):
    total_duration: float = 60
    # Project-wide generation resolution ([width, height]) applied to every
    # rendered clip. Defaults match the workflow's documented example; edit via
    # the "Project" tab in the UI. Old timeline.json files without these fields
    # fall back to the defaults on load.
    width: int = 768
    height: int = 448
    keyframes: list[Keyframe] = []
    segments: list[Segment] = []


class OpenProjectRequest(BaseModel):
    """The absolute server-side path of the folder to open as the active
    project. Created (with its image/clip subfolders) if it doesn't exist."""
    path: str


class SelectionState(BaseModel):
    """What's currently selected in the canvas, mirrored to the Python
    side so NiceGUI's sidebar form can display/edit it. Single-user app,
    so this lives as one in-memory object rather than per-session state —
    revisit if this ever needs to support multiple concurrent projects
    open at once. ``kind`` says whether the selection is a keyframe dot
    (top track) or a video segment block (bottom row); exactly one of the
    two IDs is set, or both are null when nothing is selected."""
    kind: Literal["keyframe", "segment"] = "keyframe"
    keyframe_id: str | None = None
    segment_id: str | None = None
    time: float = 0
    prompt: str = ""
    image_path: str | None = None
    # Segment context (populated when kind == "segment") — posted by the
    # canvas so the sidebar can display it without re-deriving the plan.
    start_time: float = 0
    end_time: float = 0
    duration: float = 0
    status: str = ""
