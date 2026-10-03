"""Tests for the segment-duplication logic in frontend/app.py.

No test framework is installed in this project, so run directly with the
project venv:

    .venv/bin/python tests/test_duplicate.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.models import Keyframe, RenderPlan, Segment  # noqa: E402
from frontend.app import (  # noqa: E402
    apply_segment_duplicate,
    duplicate_segment_keyframes,
)


def kf(id_: str, time: float) -> dict:
    return {'id': id_, 'time': time}


def test_middle_segment_duplicated_and_tail_shifted() -> None:
    kfs = [kf('a', 0), kf('b', 10), kf('d', 30)]
    new_kfs, new_id = duplicate_segment_keyframes(kfs, 'a-b')
    assert [k['id'] for k in new_kfs] == ['a', 'b', new_id, 'd']
    # Original untouched; copy runs b -> b + length.
    assert (new_kfs[0]['time'], new_kfs[1]['time']) == (0.0, 10.0)
    assert new_kfs[2]['time'] == 20.0
    # Everything after the copy moved back by one segment length.
    assert new_kfs[3]['time'] == 40.0


def test_last_segment_duplicated() -> None:
    kfs = [kf('a', 5), kf('b', 15)]
    new_kfs, new_id = duplicate_segment_keyframes(kfs, 'a-b')
    assert [k['id'] for k in new_kfs] == ['a', 'b', new_id]
    assert (new_kfs[0]['time'], new_kfs[1]['time']) == (5.0, 15.0)
    assert new_kfs[2]['time'] == 25.0


def test_non_adjacent_pair_rejected() -> None:
    kfs = [kf('a', 0), kf('b', 10), kf('c', 20)]
    assert duplicate_segment_keyframes(kfs, 'a-c') is None
    assert duplicate_segment_keyframes(kfs, 'zz-b') is None


def test_copy_mirrors_source_and_follower_keeps_state() -> None:
    plan = RenderPlan(total_duration=60)
    plan.keyframes = [Keyframe(id='a', time=0), Keyframe(id='b', time=10), Keyframe(id='d', time=30)]
    src = Segment(
        id='a-b', start_time=0, end_time=10, duration=10,
        prompt='pan left', start_image_path='s.png', end_image_path='e.png',
        status='done', output_path='/clips/abc.mp4', history=['/clips/old.mp4'],
    )
    nxt = Segment(
        id='b-d', start_time=10, end_time=30, duration=20,
        prompt='zoom in', start_image_path='e.png', status='queued',
    )
    plan.segments = [src, nxt]

    result = apply_segment_duplicate(plan, 'a-b')
    assert result is not None
    new_kfs, copy_seg, shifted = result
    assert [k['id'] for k in new_kfs] == ['a', 'b', new_kfs[2]['id'], 'd']

    # The original segment is untouched.
    orig = next(s for s in plan.segments if s.id == 'a-b')
    assert (orig.prompt, orig.status) == ('pan left', 'done')

    # The copy mirrors the source: prompt, frames and generated clip.
    assert copy_seg.id == f'b-{new_kfs[2]["id"]}'
    assert (copy_seg.start_time, copy_seg.end_time, copy_seg.duration) == (10.0, 20.0, 10.0)
    assert copy_seg.prompt == 'pan left'
    assert copy_seg.start_image_path == 's.png'
    assert copy_seg.end_image_path == 'e.png'
    assert copy_seg.status == 'done'
    assert copy_seg.output_path == '/clips/abc.mp4'
    assert copy_seg.history == ['/clips/old.mp4']

    # The follower kept its full state under its new id.
    assert shifted is not None and shifted.id == f'{new_kfs[2]["id"]}-d'
    assert (shifted.prompt, shifted.status) == ('zoom in', 'queued')
    assert shifted.start_image_path == 'e.png'


def test_copy_of_unrendered_segment_starts_empty() -> None:
    plan = RenderPlan(total_duration=60)
    plan.keyframes = [Keyframe(id='a', time=0), Keyframe(id='b', time=10)]
    src = Segment(
        id='a-b', start_time=0, end_time=10, duration=10,
        prompt='hold still', start_image_path='s.png', status='empty',
    )
    plan.segments = [src]

    result = apply_segment_duplicate(plan, 'a-b')
    assert result is not None
    _new_kfs, copy_seg, shifted = result
    assert shifted is None  # nothing followed the source
    assert copy_seg.prompt == 'hold still'
    assert copy_seg.start_image_path == 's.png'
    assert copy_seg.status == 'empty'
    assert copy_seg.output_path is None


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for t in tests:
        t()
        print(f'ok  {t.__name__}')
    print(f'{len(tests)} tests passed')


if __name__ == '__main__':
    main()
