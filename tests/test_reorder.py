"""Tests for the segment reorder logic in frontend/app.py.

No test framework is installed in this project, so run directly with the
project venv:

    .venv/bin/python tests/test_reorder.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.models import Keyframe, RenderPlan, Segment  # noqa: E402
from frontend.app import reorder_segments  # noqa: E402


def make_plan(times, prompts) -> RenderPlan:
    """Build a plan whose keyframes sit at ``times`` and whose segments (in
    positional order) carry the given prompts / durations. Durations are the
    gaps between consecutive times."""
    kfs = [Keyframe(id=f'k{i}', time=t) for i, t in enumerate(times)]
    segs = []
    for i in range(len(times) - 1):
        dur = round(times[i + 1] - times[i], 1)
        segs.append(Segment(
            id=f'k{i}-k{i + 1}', start_time=times[i], end_time=times[i + 1],
            duration=dur, prompt=prompts[i],
        ))
    return RenderPlan(total_duration=max(times), keyframes=kfs, segments=segs)


def test_swap_two_segments_moves_content_and_durations() -> None:
    plan = make_plan([0, 5, 8], ['zoom', 'pan'])
    # Give the two segments distinct render state so we can assert it follows.
    plan.segments[1].status = 'done'
    plan.segments[1].output_path = '/clips/pan.mp4'

    new_kfs = reorder_segments(plan, 1, 0)  # move slot 1 to the front

    assert [k['id'] for k in new_kfs] == ['k0', 'k1', 'k2']
    # Durations permuted with content: "pan" (3s) now first, "zoom" (5s) second.
    assert (new_kfs[0]['time'], new_kfs[1]['time'], new_kfs[2]['time']) == (0.0, 3.0, 8.0)

    # Positional slot ids are unchanged; their content now differs.
    assert [s.id for s in plan.segments] == ['k0-k1', 'k1-k2']
    first, second = plan.segments
    assert (first.prompt, first.duration) == ('pan', 3.0)
    assert (first.start_time, first.end_time) == (0.0, 3.0)
    assert first.status == 'done' and first.output_path == '/clips/pan.mp4'
    assert (second.prompt, second.duration) == ('zoom', 5.0)
    assert (second.start_time, second.end_time) == (3.0, 8.0)


def test_move_middle_to_front_among_three() -> None:
    plan = make_plan([0, 4, 9, 15], ['S1', 'S2', 'S3'])

    new_kfs = reorder_segments(plan, 1, 0)  # S2 to the front

    assert [k['id'] for k in new_kfs] == ['k0', 'k1', 'k2', 'k3']
    # New durations [5, 4, 6]: b=5, c=9 (coincidentally unchanged), d=15.
    assert [k['time'] for k in new_kfs] == [0.0, 5.0, 9.0, 15.0]
    # Content order is now S2, S1, S3 across the positional slots.
    assert [s.prompt for s in plan.segments] == ['S2', 'S1', 'S3']
    assert [s.duration for s in plan.segments] == [5.0, 4.0, 6.0]


def test_noop_when_same_slot() -> None:
    plan = make_plan([0, 5, 8], ['zoom', 'pan'])
    before = [(s.id, s.prompt, s.duration) for s in plan.segments]

    assert reorder_segments(plan, 0, 0) is None
    assert [(s.id, s.prompt, s.duration) for s in plan.segments] == before


def test_out_of_range_rejected() -> None:
    plan = make_plan([0, 5, 8], ['zoom', 'pan'])
    assert reorder_segments(plan, 2, 0) is None   # from beyond last slot
    assert reorder_segments(plan, 0, 9) is None   # to beyond last slot
    assert reorder_segments(plan, -1, 0) is None  # negative


def test_single_segment_cannot_reorder() -> None:
    plan = make_plan([0, 5], ['only'])
    assert reorder_segments(plan, 0, 0) is None


def test_leading_gap_preserved() -> None:
    # First keyframe not at t=0 — the leading gap must stay put.
    plan = make_plan([2, 7, 9], ['X', 'Y'])

    new_kfs = reorder_segments(plan, 0, 1)  # move X after Y

    assert [k['time'] for k in new_kfs] == [2.0, 4.0, 9.0]
    assert plan.segments[0].prompt == 'Y' and (plan.segments[0].start_time, plan.segments[0].end_time) == (2.0, 4.0)
    assert plan.segments[1].prompt == 'X' and (plan.segments[1].start_time, plan.segments[1].end_time) == (4.0, 9.0)


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for t in tests:
        t()
        print(f'ok  {t.__name__}')
    print(f'{len(tests)} tests passed')


if __name__ == '__main__':
    main()
