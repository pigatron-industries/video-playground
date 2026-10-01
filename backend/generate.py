import os
import shutil
import time
from pathlib import Path

import requests

from backend.models import Segment
from backend.storage import load_plan, project_dir, save_plan

# Generic workflow API (diffusers-playground) — see api.md in that repo for
# the full contract. Overridable via env var if the server moves off localhost.
API_BASE_URL = os.environ.get("VIDEO_API_BASE_URL", "http://localhost:8070").rstrip("/")
H3_WORKFLOW = "MinimaxH3VideoWorkflow"
H3_RESOLUTION = [768, 448]  # [width, height], per the docs example
POLL_INTERVAL_SECONDS = 2.0
# Give up only after this many *consecutive* poll failures — transient network
# hiccups are fine, a dead server is not (~1 minute at the interval above).
MAX_CONSECUTIVE_POLL_ERRORS = 30


def run_generate(segment_id: str) -> None:
    """Entry point for a background render job. Uses the shared in-memory
    plan (the single source of truth for the session), since the NiceGUI
    sidebar may have edited the prompt/trim after this was queued."""
    plan = load_plan()
    segment = next(s for s in plan.segments if s.id == segment_id)
    out_path = project_dir() / "clips" / f"{segment_id}.mp4"

    segment.status = "rendering"
    save_plan(plan)

    try:
        generate_clip(segment, out_path)
        segment.status = "done"
        segment.output_path = str(out_path)
        segment.error = None
    except Exception as exc:  # noqa: BLE001 — surface failure to the UI
        segment.status = "error"
        segment.error = str(exc)

    save_plan(plan)


def generate_clip(segment: Segment, out_path: Path) -> None:
    """Generate a clip via MinimaxH3VideoWorkflow on the generic workflow
    API: queue it asynchronously, poll until finished, then copy the
    resulting file into the project's clips/ folder."""
    params = {
        "prompt": segment.prompt,
        "first_image": _image_param(segment.start_image_path),
        "last_image": _image_param(segment.end_image_path),
        "resolution": H3_RESOLUTION,
        "duration": segment.duration,
    }

    resp = requests.post(
        f"{API_BASE_URL}/api/generic/async/run",
        json={"workflow": H3_WORKFLOW, "batch_size": 1, "params": params},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to queue generation: HTTP {resp.status_code}: {resp.text[:500]}"
        )
    job = resp.json()
    job_id = job.get("job_id")
    if not job_id:
        raise RuntimeError(f"No job_id in queue response: {job}")

    result = _poll_job(job_id)
    shutil.copy2(_extract_video_file(result), out_path)


def _image_param(stored: str | None) -> str | None:
    """Turn a segment's stored image reference into a local file path the
    API can read (it accepts base64 or a server-side path), or None if unset."""
    if not stored:
        return None
    name = Path(stored).name  # handles both bare names and full/URL paths
    candidate = project_dir() / "images" / name
    if candidate.exists():
        return str(candidate)
    raw = Path(stored)
    if raw.is_absolute() and raw.exists():
        return str(raw)
    return None


def _poll_job(job_id: str) -> dict:
    """Poll the job until it finishes or errors. Returns the final response."""
    url = f"{API_BASE_URL}/api/generic/async/{job_id}"
    consecutive_errors = 0
    while True:
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 404:
                raise RuntimeError(
                    f"Unknown job id {job_id} (did the API server restart?)"
                )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            # Transient network issue — retry a bounded number of times.
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                raise RuntimeError(
                    f"Lost contact with the generation API: {exc}"
                ) from exc
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        status = data.get("status")
        if status == "finished":
            return data
        if status == "error":
            raise RuntimeError(
                data.get("error") or f"Generation job failed: {data}"
            )
        # queued / running — keep polling.
        time.sleep(POLL_INTERVAL_SECONDS)


def _extract_video_file(result: dict) -> str:
    """Pull the server-side video path out of a finished job's response."""
    outputs = result.get("outputs") or []
    for out in outputs:
        if out.get("type") == "Video" and out.get("file"):
            return out["file"]
    # Fall back to any output that carries a file path.
    for out in outputs:
        if out.get("file"):
            return out["file"]
    raise RuntimeError(f"No video file in generation result: {result}")
