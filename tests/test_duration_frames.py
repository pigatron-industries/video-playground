"""Tests for the frames/fps duration model (segments store integer *frames* in
``duration_frames`` plus a per-segment ``fps``; seconds are derived at the
boundaries). Also covers loading old timeline.json files that stored the length
as a float number of seconds under ``duration``.

No test framework is installed in this project, so run directly with the
project venv:

    .venv/bin/python tests/test_duration_frames.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.models import DEFAULT_FPS, RenderPlan, Segment  # noqa: E402
from backend.plan_ops import MIN_DURATION, add_segment, resize_segment  # noqa: E402


def test_new_segment_stores_integer_frames_at_default_fps() -> None:
    plan = RenderPlan()
    seg = add_segment(plan)
    assert seg is not None
    # Duration is stored as an integer frame count (not a float of seconds).
    assert isinstance(seg.duration_frames, int)
    # A fresh segment has no rendered clip yet, so it uses the default fps.
    assert seg.fps == DEFAULT_FPS
    # The default 5s round-trips back to ~5 seconds at that rate.
    assert abs(seg.duration_seconds - 5.0) < 1e-9


def test_resize_converts_user_seconds_to_frames_at_segment_fps() -> None:
    plan = RenderPlan()
    seg = add_segment(plan)
    assert resize_segment(plan, seg.id, 3.0) is True
    # 3s at the default 24fps == 72 frames, and it reads back as exactly 3s.
    assert seg.duration_frames == int(round(3.0 * DEFAULT_FPS))
    assert abs(seg.duration_seconds - 3.0) < 1e-9


def test_resize_clamps_to_minimum_duration() -> None:
    plan = RenderPlan()
    seg = add_segment(plan)
    resize_segment(plan, seg.id, 0.1)  # below MIN_DURATION seconds
    assert abs(seg.duration_seconds - MIN_DURATION) < 1e-9


def test_resize_is_noop_when_unchanged() -> None:
    plan = RenderPlan()
    seg = add_segment(plan)
    before = seg.duration_frames
    # Setting the same length again (5s default) must report "no change".
    assert resize_segment(plan, seg.id, 5.0) is False
    assert seg.duration_frames == before


def test_plan_total_and_span_are_in_seconds() -> None:
    plan = RenderPlan()
    a = add_segment(plan)          # 5s default
    b = add_segment(plan)          # another 5s default
    assert a is not None and b is not None
    # Aggregate length is reported in seconds even though storage is frames.
    assert plan.duration == round(sum(s.duration_seconds for s in plan.segments), 1)
    span = plan.span_of(a.id)
    assert span is not None
    assert abs(span[0]) < 1e-9                       # first segment starts at t=0
    assert abs((span[1] - span[0]) - a.duration_seconds) < 1e-9


def test_reexpressing_frames_preserves_seconds_when_fps_changes() -> None:
    # A 5s clip rendered at the default rate, then discovered to actually be 30fps.
    seg = Segment(duration_frames=int(round(5.0 * DEFAULT_FPS)), fps=DEFAULT_FPS)
    detected = 30.0
    intended = seg.duration_frames / seg.fps         # preserve real time (5.0s)
    seg.fps = detected
    seg.duration_frames = int(round(intended * detected))   # -> 150 frames @ 30fps
    assert abs(seg.duration_seconds - 5.0) < 1e-9


def test_duration_seconds_is_safe_when_fps_is_zero() -> None:
    seg = Segment(duration_frames=120, fps=0.0)
    assert seg.duration_seconds == 0.0


def test_legacy_float_duration_loads_as_frames_at_24fps() -> None:
    # Old timeline.json files stored the length as a float number of seconds.
    seg = Segment(**{'duration': 5.0})
    assert seg.duration_frames == int(round(5.0 * DEFAULT_FPS))   # 120 frames @ 24fps
    assert abs(seg.duration_seconds - 5.0) < 1e-9


def test_legacy_migration_is_skipped_when_new_field_present() -> None:
    # A file already using duration_frames must not be re-migrated from a stray "duration".
    seg = Segment(**{'duration_frames': 72, 'fps': 30.0, 'duration': 99.0})
    assert seg.duration_frames == 72


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for t in tests:
        t()
        print(f'ok  {t.__name__}')
    print(f'{len(tests)} tests passed')


if __name__ == '__main__':
    main()
