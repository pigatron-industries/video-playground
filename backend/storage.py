import json
import shutil
import time
from pathlib import Path

from fastapi import HTTPException

from backend.models import RenderPlan

# The single active project folder for this (single-user) app session.
# Everything — timeline.json, images/, clips/, the final video — lives
# inside it. Set via set_active_folder (the "Open Project" flow).
_active_folder: Path | None = None

# In-memory copy of the open project's timeline state. The file is read
# exactly once — the first time the plan is accessed for a given project
# (open project flow or app startup) — and from then on every API reads
# from this variable, so edits made on top of the loaded state are never
# clobbered by a stale re-read. save_plan() keeps it in sync and persists
# to disk. Switching project folders resets it to force a fresh load.
_plan: RenderPlan | None = None

# App-level config, persisted across server restarts. Lives in the app root
# (one level up from this backend/ package) so it's easy to find; it's
# git-ignored because it stores this machine's absolute paths.
CONFIG_PATH = Path(__file__).parent.parent / "config.json"


def load_config() -> dict:
    """Read the app config, returning {} if it doesn't exist yet or is
    malformed — a fresh install or a hand-edited file must not crash boot."""
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_last_folder(path: Path | str) -> None:
    """Record the given project folder as the last-opened one, so the next
    server boot can reopen it and the folder picker can start there."""
    config = load_config()
    config["last_project_path"] = str(path)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")


def load_last_folder() -> str | None:
    return load_config().get("last_project_path")


def set_active_folder(path: str, *, persist: bool = True) -> Path:
    """Point the app at a project folder, creating it (and its image/clip
    subfolders) if it doesn't exist yet. With ``persist`` (the default) the
    folder is also saved as the last-opened project so a restart resumes
    here. Returns the resolved folder."""
    global _active_folder, _plan
    folder = Path(path).expanduser().resolve()
    (folder / "images").mkdir(parents=True, exist_ok=True)
    (folder / "clips").mkdir(parents=True, exist_ok=True)
    _active_folder = folder
    _plan = None  # new project — the file is (re)loaded once on first access
    if persist:
        save_last_folder(folder)
    return folder


def restore_last_project() -> Path | None:
    """Reopen the last project recorded in config (if it still exists) so a
    server restart resumes where the user left off rather than starting
    empty. Returns the restored folder, or None if there's nothing valid."""
    last = load_last_folder()
    if not last:
        return None
    candidate = Path(last).expanduser()
    if not candidate.is_dir():
        return None
    set_active_folder(str(candidate), persist=False)
    return _active_folder


def active_folder() -> Path | None:
    return _active_folder


def project_dir() -> Path:
    if _active_folder is None:
        raise HTTPException(400, "No project folder is open")
    return _active_folder


def load_plan() -> RenderPlan:
    """Return the in-memory copy of the open project's timeline.

    timeline.json is read only to populate the cache the first time a
    project's plan is accessed (a fresh project gets an empty plan); every
    later call returns the cached object, so in-memory edits made after
    the initial load are the single source of truth for the session.
    """
    global _plan
    if _plan is None:
        plan_path = project_dir() / "timeline.json"
        if plan_path.exists():
            _plan = RenderPlan.model_validate_json(plan_path.read_text())
        else:
            _plan = RenderPlan()
    return _plan


# How many timeline.json backups to retain in the project's undo/ folder
# before the oldest ones are removed.
_MAX_TIMELINE_BACKUPS = 50


def _backup_timeline(plan_path: Path) -> None:
    """Copy the current timeline.json into <project>/undo/ so the previous
    state can be restored after a save overwrites it. Keeps only the newest
    _MAX_TIMELINE_BACKUPS copies (oldest deleted first). No-op on a fresh
    project's first save, when there is no previous timeline to back up."""
    if not plan_path.exists():
        return
    undo_dir = plan_path.parent / "undo"
    undo_dir.mkdir(exist_ok=True)
    # time_ns() names sort chronologically, so pruning by name order prunes
    # the oldest backups first.
    shutil.copy2(plan_path, undo_dir / f"timeline-{time.time_ns()}.json")
    backups = sorted(undo_dir.glob("timeline-*.json"))
    for stale in backups[:-_MAX_TIMELINE_BACKUPS]:
        stale.unlink()


def save_plan(plan: RenderPlan) -> None:
    global _plan
    _plan = plan
    plan_path = project_dir() / "timeline.json"
    _backup_timeline(plan_path)
    plan_path.write_text(plan.model_dump_json(indent=2))


def save_image(filename: str, data: bytes) -> Path:
    dest = project_dir() / "images" / filename
    dest.write_bytes(data)
    return dest
