import os
import shutil
import tempfile
import time
from pathlib import Path

import requests
from PIL import Image, ImageOps

from backend.models import Segment
from backend.render import detect_fps
from backend.storage import load_plan, project_dir, save_clip, save_plan

# Generic workflow API (diffusers-playground) — see api.md in that repo for
# the full contract. Overridable via env var if the server moves off localhost.
API_BASE_URL = os.environ.get("VIDEO_API_BASE_URL", "http://localhost:8070").rstrip("/")
H3_WORKFLOW = "MinimaxH3VideoWorkflow"
# Default generation resolution ([width, height]) — the workflow's documented
# example. A project overrides it via RenderPlan.width/height (the Project tab).
DEFAULT_RESOLUTION = [768, 448]
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

    segment.status = "rendering"
    save_plan(plan)

    try:
        # Render into a temp file first, then store it under its content hash
        # (like images) so re-rendering never clobbers an earlier take.
        with tempfile.TemporaryDirectory(prefix="clip-render-") as tmp_dir:
            staged = Path(tmp_dir) / f"{segment_id}.mp4"
            generate_clip(segment, staged, width=plan.width, height=plan.height)
            out_path = save_clip(staged)

        # Detect the rendered clip's real frame rate and store it on the segment.
        # Re-express the stored duration (frames) at that rate so the timeline
        # length in seconds is unchanged — only the underlying fps/frames update.
        detected_fps = detect_fps(out_path)
        if detected_fps:
            intended_seconds = segment.duration_frames / segment.fps  # preserve real time
            segment.fps = detected_fps
            segment.duration_frames = int(round(intended_seconds * detected_fps))

        # Keep the previous take reachable instead of overwriting it.
        prev = segment.output_path
        if prev and prev != str(out_path):
            segment.history = [p for p in segment.history if p != str(out_path)]
            segment.history.insert(0, prev)
        segment.status = "done"
        segment.output_path = str(out_path)
        segment.error = None
    except Exception as exc:  # noqa: BLE001 — surface failure to the UI
        segment.status = "error"
        segment.error = str(exc)

    save_plan(plan)


def generate_clip(
    segment: Segment,
    out_path: Path,
    *,
    width: int = DEFAULT_RESOLUTION[0],
    height: int = DEFAULT_RESOLUTION[1],
) -> None:
    """Generate a clip via MinimaxH3VideoWorkflow on the generic workflow
    API: queue it asynchronously, poll until finished, then copy the
    resulting file into the project's clips/ folder. ``width``/``height`` are
    the project's generation resolution (RenderPlan.width/height); they default
    to the workflow's documented example so direct callers still work. Frame
    images are resized to that same resolution before being sent."""
    # The resized copies live in a temp dir that must stay on disk while the
    # API reads them, so it wraps everything up to (and including) polling.
    with tempfile.TemporaryDirectory(prefix="frame-resize-") as tmp_dir:
        params = {
            "prompt": segment.prompt,
            "first_image": _image_param(
                segment.start_image_path, width, height, Path(tmp_dir), "first"
            ),
            "last_image": _image_param(
                segment.end_image_path, width, height, Path(tmp_dir), "last"
            ),
            "resolution": [width, height],
            # The workflow API takes a length in seconds; the stored duration is
            # an integer frame count, so convert at this boundary.
            "duration": round(segment.duration_seconds, 2),
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


def _image_param(
    stored: str | None, width: int, height: int, tmp_dir: Path, name: str
) -> str | None:
    """Turn a segment's stored image reference into a local file path the
    API can read (it accepts base64 or a server-side path), resized to the
    project's generation resolution. Returns None if unset."""
    src = _resolve_image(stored)
    if src is None:
        return None
    with Image.open(src) as img:
        # exif_transpose bakes in any camera rotation before we resize, so
        # the frame arrives oriented the way it was previewed.
        resized = ImageOps.exif_transpose(img).resize((width, height))
        dst = tmp_dir / f"{name}{src.suffix}"
        if src.suffix.lower() in {".jpg", ".jpeg"}:
            resized.convert("RGB").save(dst)  # JPEG has no alpha channel
        else:
            resized.save(dst)
    return str(dst)


def _resolve_image(stored: str | None) -> Path | None:
    """Resolve a segment's stored image reference to an existing local file,
    or None if unset/unresolvable."""
    if not stored:
        return None
    name = Path(stored).name  # handles both bare names and full/URL paths
    candidate = project_dir() / "images" / name
    if candidate.exists():
        return candidate
    raw = Path(stored)
    if raw.is_absolute() and raw.exists():
        return raw
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
