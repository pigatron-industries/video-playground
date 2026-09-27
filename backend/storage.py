from pathlib import Path

from fastapi import HTTPException

from backend.models import RenderPlan

DATA_DIR = Path.home() / ".keyframe-app"
PROJECTS_DIR = DATA_DIR / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def project_dir(project_id: str) -> Path:
    d = PROJECTS_DIR / project_id
    (d / "clips").mkdir(parents=True, exist_ok=True)
    (d / "images").mkdir(parents=True, exist_ok=True)
    return d


def load_plan(project_id: str) -> RenderPlan:
    plan_path = project_dir(project_id) / "plan.json"
    if not plan_path.exists():
        raise HTTPException(404, f"No project found with id {project_id}")
    return RenderPlan.model_validate_json(plan_path.read_text())


def save_plan(plan: RenderPlan) -> None:
    if not plan.project_id:
        raise ValueError("plan.project_id must be set before saving")
    plan_path = project_dir(plan.project_id) / "plan.json"
    plan_path.write_text(plan.model_dump_json(indent=2))


def save_image(project_id: str, filename: str, data: bytes) -> Path:
    dest = project_dir(project_id) / "images" / filename
    dest.write_bytes(data)
    return dest
