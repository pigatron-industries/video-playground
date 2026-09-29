import subprocess
from pathlib import Path

from backend.models import RenderPlan, Segment
from backend.storage import load_plan, project_dir, save_plan


def run_render(segment_id: str) -> None:
    """Entry point for a background render job. Uses the shared in-memory
    plan (the single source of truth for the session), since the NiceGUI
    sidebar may have edited the prompt/trim after this was queued."""
    plan = load_plan()
    segment = next(s for s in plan.segments if s.id == segment_id)
    segment.status = "rendering"
    save_plan(plan)

    out_path = project_dir() / "clips" / f"{segment_id}.mp4"

    try:
        if segment.source_clip_path:
            _process_source_clip(segment, out_path)
        else:
            _generate_with_h3(segment, out_path)
        segment.status = "done"
        segment.output_path = str(out_path)
        segment.error = None
    except Exception as exc:  # noqa: BLE001 — surface failure to the UI
        segment.status = "error"
        segment.error = str(exc)

    save_plan(plan)


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


def _generate_with_h3(segment: Segment, out_path: Path) -> None:
    # TODO: real call — needs API credentials and the current request/
    # response shape confirmed against MiniMax's docs.
    #
    # resp = requests.post(
    #     "https://api.minimax.io/v1/video_generation",
    #     json={
    #         "model": "MiniMax-H3",
    #         "prompt": segment.prompt,
    #         "first_frame_image": segment.start_image_path,
    #         "last_frame_image": segment.end_image_path,
    #         "duration": segment.duration,
    #     },
    #     headers={"Authorization": f"Bearer {API_KEY}"},
    # )
    # download resulting video to out_path
    raise NotImplementedError("Wire up the H3 API call — see commented example above.")


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
