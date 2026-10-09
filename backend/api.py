from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.models import OpenProjectRequest, RenderPlan, Segment
from backend.queue import render_queue
from backend.render import concat_segments
from backend.storage import active_folder, load_plan, project_dir, save_image, save_plan, set_active_folder

router = APIRouter()


# ---------------------------------------------------------------------------
# Projects / plans
# ---------------------------------------------------------------------------
@router.get("/projects/state")
def project_state() -> dict:
    """Return the currently active project (folder path + plan) or null if none is open."""
    folder = active_folder()
    if folder is None:
        return {"path": None, "plan": None}
    return {"path": str(folder), "plan": load_plan()}


@router.post("/projects/open")
def open_project(req: OpenProjectRequest) -> dict:
    """Point the app at a project folder (creating it if needed) and load
    its timeline into the in-memory session state so the UI can restore
    the canvas. Returns the resolved folder path plus the plan."""
    folder = set_active_folder(req.path)
    return {"path": str(folder), "plan": load_plan()}


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
    # dest.name is the content-addressed (deduplicated) filename.
    return {"path": str(dest), "url": f"/api/projects/images/{dest.name}"}


@router.get("/projects/images/{filename}")
def get_image(filename: str) -> FileResponse:
    path = project_dir() / "images" / filename
    if not path.exists():
        raise HTTPException(404, "Image not found")
    return FileResponse(path)


# ---------------------------------------------------------------------------
# Rendered clips — content-addressed (<sha256>.mp4) files written by
# backend/generate.py into clips/, played in the preview window once a
# segment's status is "done".
# ---------------------------------------------------------------------------
@router.get("/projects/clips/{filename}")
def get_clip(filename: str) -> FileResponse:
    path = project_dir() / "clips" / filename
    if not path.exists():
        raise HTTPException(404, "Clip not found")
    return FileResponse(path)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
@router.post("/projects/segments/{segment_id}/render")
def render_segment(segment_id: str) -> dict:
    plan = load_plan()
    segment = next((s for s in plan.segments if s.id == segment_id), None)
    if not segment:
        raise HTTPException(404, f"No segment {segment_id} in the open project")

    segment.status = "queued"
    save_plan(plan)
    render_queue.enqueue(segment_id)
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


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}
