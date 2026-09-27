import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.models import RenderPlan, Segment, SelectionState
from backend.render import concat_segments, run_render
from backend.storage import load_plan, project_dir, save_image, save_plan

router = APIRouter()

# In-memory, single-user selection state — see SelectionState's docstring.
_selection = SelectionState()


# ---------------------------------------------------------------------------
# Projects / plans
# ---------------------------------------------------------------------------
@router.post("/projects")
def create_or_update_project(plan: RenderPlan) -> RenderPlan:
    if not plan.project_id:
        plan.project_id = str(uuid.uuid4())
    save_plan(plan)
    return plan


@router.get("/projects/{project_id}")
def get_project(project_id: str) -> RenderPlan:
    return load_plan(project_id)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
@router.post("/projects/{project_id}/images")
async def upload_image(project_id: str, file: UploadFile) -> dict:
    data = await file.read()
    dest = save_image(project_id, file.filename, data)
    return {"path": str(dest), "url": f"/api/projects/{project_id}/images/{file.filename}"}


@router.get("/projects/{project_id}/images/{filename}")
def get_image(project_id: str, filename: str) -> FileResponse:
    path = project_dir(project_id) / "images" / filename
    if not path.exists():
        raise HTTPException(404, "Image not found")
    return FileResponse(path)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
@router.post("/projects/{project_id}/segments/{segment_id}/render")
def render_segment(project_id: str, segment_id: str, background_tasks: BackgroundTasks) -> dict:
    plan = load_plan(project_id)
    segment = next((s for s in plan.segments if s.id == segment_id), None)
    if not segment:
        raise HTTPException(404, f"No segment {segment_id} in project {project_id}")

    segment.status = "queued"
    save_plan(plan)
    background_tasks.add_task(run_render, project_id, segment_id)
    return {"status": "queued", "segment_id": segment_id}


@router.get("/projects/{project_id}/segments/{segment_id}/status")
def segment_status(project_id: str, segment_id: str) -> Segment:
    plan = load_plan(project_id)
    segment = next((s for s in plan.segments if s.id == segment_id), None)
    if not segment:
        raise HTTPException(404, f"No segment {segment_id} in project {project_id}")
    return segment


@router.post("/projects/{project_id}/concat")
def concat_project(project_id: str) -> dict:
    plan = load_plan(project_id)
    missing = [s.id for s in plan.segments if s.status != "done"]
    if missing:
        raise HTTPException(400, f"Segments not yet rendered: {missing}")
    final_path = concat_segments(plan)
    return {"output_path": str(final_path)}


# ---------------------------------------------------------------------------
# Selection state — bridges the canvas (JS) and the NiceGUI sidebar (Python).
#
# The canvas POSTs here whenever the user clicks/drags a keyframe. The
# NiceGUI page polls GET on a short timer and refreshes its form fields
# from whatever comes back. This keeps the bridge simple and version-
# independent rather than relying on NiceGUI's internal event plumbing.
# ---------------------------------------------------------------------------
@router.post("/ui/select")
def set_selection(state: SelectionState) -> SelectionState:
    global _selection
    _selection = state
    return _selection


@router.get("/ui/select")
def get_selection() -> SelectionState:
    return _selection


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}
