from pathlib import Path

from fastapi import HTTPException

from backend.models import RenderPlan

# The single active project folder for this (single-user) app session.
# Everything — timeline.json, images/, clips/, the final video — lives
# inside it. Set via set_active_folder (the "Open Project" flow).
_active_folder: Path | None = None


def set_active_folder(path: str) -> Path:
    """Point the app at a project folder, creating it (and its image/clip
    subfolders) if it doesn't exist yet. Returns the resolved folder."""
    global _active_folder
    folder = Path(path).expanduser().resolve()
    (folder / "images").mkdir(parents=True, exist_ok=True)
    (folder / "clips").mkdir(parents=True, exist_ok=True)
    _active_folder = folder
    return folder


def active_folder() -> Path | None:
    return _active_folder


def project_dir() -> Path:
    if _active_folder is None:
        raise HTTPException(400, "No project folder is open")
    return _active_folder


def load_plan() -> RenderPlan:
    plan_path = project_dir() / "timeline.json"
    if not plan_path.exists():
        raise HTTPException(404, "No timeline.json in the open project folder")
    return RenderPlan.model_validate_json(plan_path.read_text())


def save_plan(plan: RenderPlan) -> None:
    plan_path = project_dir() / "timeline.json"
    plan_path.write_text(plan.model_dump_json(indent=2))


def save_image(filename: str, data: bytes) -> Path:
    dest = project_dir() / "images" / filename
    dest.write_bytes(data)
    return dest
