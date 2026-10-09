import json
import subprocess
from pathlib import Path

from backend.models import RenderPlan, Segment
from backend.storage import project_dir


def _process_source_clip(segment: Segment, out_path: Path) -> None:
    trim_in = segment.trim_in or 0
    # trim_out is a seconds value for ffmpeg; fall back to the segment's length
    # in seconds (its stored duration is an integer frame count).
    trim_out = segment.trim_out or segment.duration_seconds
    speed = segment.speed_factor or 1.0

    filters = []
    if speed != 1.0:
        filters.append(f"setpts={1 / speed:.6f}*PTS")

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(trim_in), "-to", str(trim_out),
        "-i", segment.source_clip_path,
    ]
    if filters:
        cmd += ["-vf", ",".join(filters)]
    cmd += [str(out_path)]

    # TODO: audio speed remap (atempo, chained for >2x/<0.5x) once dragged
    # clips with audio are wired up end to end.
    subprocess.run(cmd, check=True, capture_output=True)


def detect_fps(video_path: Path | str) -> float | None:
    """Probe a video file's frame rate with ffprobe and return it as a float.

    Best-effort: returns None (rather than raising) if ffprobe is missing, the
    file can't be read, or no usable frame-rate field is present — callers then
    keep their existing/default fps."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate,r_frame_rate",
             "-of", "json", str(video_path)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        data = json.loads(out.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, json.JSONDecodeError):
        return None

    streams = data.get("streams") or []
    if not streams:
        return None
    stream = streams[0]
    for key in ("avg_frame_rate", "r_frame_rate"):
        value = str(stream.get(key) or "")
        num, sep, den = value.partition("/")
        if not sep:
            continue
        try:
            numerator, denominator = float(num), float(den)
        except ValueError:
            continue
        if denominator:
            return numerator / denominator
    return None


def concat_segments(plan: RenderPlan) -> Path:
    clips_dir = project_dir() / "clips"
    concat_list = clips_dir / "concat.txt"
    concat_list.write_text("\n".join(f"file '{s.output_path}'" for s in plan.segments))

    final_path = project_dir() / "final.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(concat_list), "-c", "copy", str(final_path)],
        check=True, capture_output=True,
    )
    return final_path
