import subprocess
from pathlib import Path

from backend.models import RenderPlan, Segment
from backend.storage import project_dir


def _process_source_clip(segment: Segment, out_path: Path) -> None:
    trim_in = segment.trim_in or 0
    trim_out = segment.trim_out or segment.duration
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
