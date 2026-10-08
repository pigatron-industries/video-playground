"""Tests for the segment-deletion logic in frontend/app.py.

No test framework is installed in this project, so run directly with the
project venv:

    .venv/bin/python tests/test_delete.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.models import Keyframe, RenderPlan, Segment  # noqa: E402
from frontend.app import (  # noqa: E402
    apply_segment_delete,
    delete_segment_keyframes,
)


def kf(id_: str, time: float) -> dict:
    return {'id': id_, 'time': time}


def test_middle_segment_deleted_and_tail_shifted_back() -> None:
    kfs = [kf('a', 0), kf('b', 5), kf('c', 10), kf('d', 15)]
    new_kfs = delete_segment_keyframes(kfs, 'b-c')
    # The deleted segment's end keyframe (c) is gone.
    assert [k['id'] for k in new_kfs] == ['a', 'b', 'd']
    # The follower starts where the deleted one started and keeps its duration.
    assert (new_kfs[0]['time'], new_kfs[1]['time']) == (0.0, 5.0)
    assert new_kfs[2]['time'] == 10.0


def test_last_segment_deleted_drops_its_end_keyframe() -> None:
    kfs = [kf('a', 0), kf('b', 5)]
    new_kfs = delete_segment_keyframes(kfs, 'a-b')
    assert [k['id'] for k in new_kfs] == ['a']
    assert new_kfs[0]['time'] == 0.0


def test_first_segment_deleted_moves_follower_to_front() -> None:
    # Leading gap (first keyframe not at t=0) must stay put.
    kfs = [kf('a', 2), kf('b', 7), kf('c', 13)]
    new_kfs = delete_segment_keyframes(kfs, 'a-b')
    assert [k['id'] for k in new_kfs] == ['a', 'c']
    # Follower keeps its 6s duration from the preserved start point.
    assert (new_kfs[0]['time'], new_kfs[1]['time']) == (2.0, 8.0)


def test_non_adjacent_pair_rejected() -> None:
    kfs = [kf('a', 0), kf('b', 5), kf('c', 10)]
    assert delete_segment_keyframes(kfs, 'a-c') is None
    assert delete_segment_keyframes(kfs, 'zz-b') is None


def test_follower_keeps_full_state_under_new_id() -> None:
    plan = RenderPlan(total_duration=60)
    plan.keyframes = [
        Keyframe(id='a', time=0), Keyframe(id='b', time=5),
        Keyframe(id='c', time=10), Keyframe(id='d', time=15),
    ]
    keep = Segment(id='a-b', start_time=0, end_time=5, duration=5, prompt='hold')
    gone = Segment(
        id='b-c', start_time=5, end_time=10, duration=5,
        prompt='pan left', status='done', output_path='/clips/gone.mp4',
    )
    follower = Segment(
        id='c-d', start_time=10, end_time=15, duration=5,
        prompt='zoom in', start_image_path='s.png', end_image_path='e.png',
        status='done', output_path='/clips/zoom.mp4', history=['/clips/old.mp4'],
    )
    plan.segments = [keep, gone, follower]

    result = apply_segment_delete(plan, 'b-c')
    assert result is not None
    new_kfs, shifted = result
    assert [k['id'] for k in new_kfs] == ['a', 'b', 'd']

    # The deleted segment is gone; its predecessor keeps everything.
    assert [s.id for s in plan.segments] == ['a-b', 'b-d']
    assert (plan.segments[0].prompt, plan.segments[0].status) == ('hold', 'empty')

    # The follower moved back to the deleted segment's start and kept its state.
    assert shifted is not None and shifted.id == 'b-d'
    assert (shifted.start_time, shifted.end_time, shifted.duration) == (5.0, 10.0, 5.0)
    assert shifted.prompt == 'zoom in'
    assert (shifted.start_image_path, shifted.end_image_path) == ('s.png', 'e.png')
    assert shifted.status == 'done' and shifted.output_path == '/clips/zoom.mp4'
    assert shifted.history == ['/clips/old.mp4']


def test_deleting_last_segment_leaves_no_follower() -> None:
    plan = RenderPlan(total_duration=60)
    plan.keyframes = [Keyframe(id='a', time=0), Keyframe(id='b', time=5)]
    only = Segment(id='a-b', start_time=0, end_time=5, duration=5, prompt='only')
    plan.segments = [only]

    result = apply_segment_delete(plan, 'a-b')
    assert result is not None
    new_kfs, shifted = result
    assert [k['id'] for k in new_kfs] == ['a']
    assert shifted is None
    assert plan.segments == []


def test_deleting_unknown_segment_rejected() -> None:
    plan = RenderPlan(total_duration=60)
    plan.keyframes = [Keyframe(id='a', time=0), Keyframe(id='b', time=5)]
    plan.segments = [Segment(id='a-b', start_time=0, end_time=5, duration=5)]

    assert apply_segment_delete(plan, 'zz-qq') is None


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for t in tests:
        t()
        print(f'ok  {t.__name__}')
    print(f'{len(tests)} tests passed')


if __name__ == '__main__':
    main()
