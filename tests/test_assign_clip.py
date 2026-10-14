"""Tests for assign_clip (backend/plan_ops.py) — making a clip (rendered or
uploaded) the segment's current take, marking it done, and keeping the
previous take reachable in its history.

No test framework is installed in this project, so run directly with the
project venv:

    .venv/bin/python tests/test_assign_clip.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.models import Segment  # noqa: E402
from backend.plan_ops import assign_clip  # noqa: E402


def test_assign_sets_output_and_marks_done() -> None:
    seg = Segment(duration_frames=120)
    assign_clip(seg, '/clips/abc.mp4')
    assert seg.output_path == '/clips/abc.mp4'
    assert seg.status == 'done'
    assert seg.error is None


def test_assign_clears_stale_error() -> None:
    seg = Segment(duration_frames=120, status='error', error='boom')
    assign_clip(seg, '/clips/abc.mp4')
    assert seg.status == 'done'
    assert seg.error is None


def test_first_assignment_keeps_history_empty() -> None:
    seg = Segment(duration_frames=120)
    assign_clip(seg, '/clips/first.mp4')
    assert seg.history == []


def test_previous_take_moved_to_history_front() -> None:
    seg = Segment(duration_frames=120, output_path='/clips/old.mp4', status='done')
    assign_clip(seg, '/clips/new.mp4')
    assert seg.output_path == '/clips/new.mp4'
    assert seg.history[0] == '/clips/old.mp4'


def test_assign_same_path_does_not_touch_history() -> None:
    seg = Segment(duration_frames=120, output_path='/clips/same.mp4', status='done')
    assign_clip(seg, '/clips/same.mp4')
    assert seg.output_path == '/clips/same.mp4'
    assert seg.history == []


def test_reassign_dedupes_existing_history_entry() -> None:
    # /clips/b.mp4 is already an earlier take; reusing it as current must not
    # leave a duplicate of it in the history.
    seg = Segment(
        duration_frames=120, output_path='/clips/a.mp4',
        history=['/clips/b.mp4'], status='done',
    )
    assign_clip(seg, '/clips/b.mp4')
    assert seg.output_path == '/clips/b.mp4'
    assert '/clips/b.mp4' not in seg.history
    # The take that was current moves to the front.
    assert seg.history[0] == '/clips/a.mp4'


def test_history_order_is_newest_first_across_takes() -> None:
    seg = Segment(duration_frames=120)
    assign_clip(seg, '/clips/one.mp4')
    assign_clip(seg, '/clips/two.mp4')
    assign_clip(seg, '/clips/three.mp4')
    assert seg.output_path == '/clips/three.mp4'
    assert seg.history == ['/clips/two.mp4', '/clips/one.mp4']


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for t in tests:
        t()
        print(f'ok  {t.__name__}')
    print(f'{len(tests)} tests passed')


if __name__ == '__main__':
    main()
