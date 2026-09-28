from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.models import OpenProjectRequest, RenderPlan, Segment, SelectionState
from backend.render import concat_segments, run_render
from backend.storage import active_folder, load_plan, project_dir, save_image, save_plan, set_active_folder

router = APIRouter()

# In-memory, single-user selection state — see SelectionState's docstring.
_selection = SelectionState()


# ---------------------------------------------------------------------------
# Projects / plans
# ---------------------------------------------------------------------------
@router.get("/projects/state")
def project_state() -> dict:
    """Return the currently active project (folder path + plan) or null if none is open."""
    folder = active_folder()
    if folder is None:
        return {"path": None, "plan": None}
    plan_path = folder / "timeline.json"
    if plan_path.exists():
        plan = RenderPlan.model_validate_json(plan_path.read_text())
    else:
        plan = RenderPlan()
    return {"path": str(folder), "plan": plan}


@router.post("/projects/open")
def open_project(req: OpenProjectRequest) -> dict:
    """Point the app at a project folder (creating it if needed) and, if a
    timeline.json already lives there, load it back so the UI can restore
    the canvas. Returns the resolved folder path plus the plan."""
    folder = set_active_folder(req.path)
    plan_path = folder / "timeline.json"
    if plan_path.exists():
        plan = RenderPlan.model_validate_json(plan_path.read_text())
    else:
        plan = RenderPlan()
    return {"path": str(folder), "plan": plan}


@router.post("/projects")
def save_project(plan: RenderPlan) -> RenderPlan:
    save_plan(plan)
    return plan


@router.get("/projects")
def get_project() -> RenderPlan:
    return load_plan()


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
@router.post("/projects/images")
async def upload_image(file: UploadFile) -> dict:
    data = await file.read()
    dest = save_image(file.filename, data)
    return {"path": str(dest), "url": f"/api/projects/images/{file.filename}"}


@router.get("/projects/images/{filename}")
def get_image(filename: str) -> FileResponse:
    path = project_dir() / "images" / filename
    if not path.exists():
        raise HTTPException(404, "Image not found")
    return FileResponse(path)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
@router.post("/projects/segments/{segment_id}/render")
def render_segment(segment_id: str, background_tasks: BackgroundTasks) -> dict:
    plan = load_plan()
    segment = next((s for s in plan.segments if s.id == segment_id), None)
    if not segment:
        raise HTTPException(404, f"No segment {segment_id} in the open project")

    segment.status = "queued"
    save_plan(plan)
    background_tasks.add_task(run_render, segment_id)
    return {"status": "queued", "segment_id": segment_id}


@router.get("/projects/segments/{segment_id}/status")
def segment_status(segment_id: str) -> Segment:
    plan = load_plan()
    segment = next((s for s in plan.segments if s.id == segment_id), None)
    if not segment:
        raise HTTPException(404, f"No segment {segment_id} in the open project")
    return segment


@router.post("/projects/concat")
def concat_project() -> dict:
    plan = load_plan()
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
