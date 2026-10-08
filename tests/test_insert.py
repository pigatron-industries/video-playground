"""Tests for insert_segment_keyframes (the timeline Insert button's pure logic).

No test framework is installed in this project, so run directly with the
project venv:

    .venv/bin/python tests/test_insert.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from frontend.app import insert_segment_keyframes  # noqa: E402


def kf(id_: str, time: float) -> dict:
    return {'id': id_, 'time': time}


def new_id_from(new_kfs: list[dict], known: tuple[str, ...]) -> str:
    """The one inserted keyframe id (the only one not in the original set)."""
    return [k['id'] for k in new_kfs if k['id'] not in known][0]


def test_insert_before_middle_segment() -> None:
    kfs = [kf('a', 0), kf('b', 5), kf('c', 10)]
    new_kfs, new_seg_id = insert_segment_keyframes(kfs, 'b-c')

    # The fresh keyframe lands at the selected segment's start point (t=5);
    # b and c move back by 5s. Order: a, <new>, b, c.
    nid = new_id_from(new_kfs, ('a', 'b', 'c'))
    assert [k['id'] for k in new_kfs] == ['a', nid, 'b', 'c']
    assert [k['time'] for k in new_kfs] == [0.0, 5.0, 10.0, 15.0]

    # The new segment is the one between <new> and b — it sits before the
    # selected (b-c), which keeps its own duration further along.
    assert new_seg_id == f'{nid}-b'


def test_insert_before_first_segment() -> None:
    kfs = [kf('a', 0), kf('b', 5)]
    new_kfs, new_seg_id = insert_segment_keyframes(kfs, 'a-b')

    # No preceding segment to split — the fresh keyframe becomes the new head.
    nid = new_id_from(new_kfs, ('a', 'b'))
    assert [k['id'] for k in new_kfs] == [nid, 'a', 'b']
    assert [k['time'] for k in new_kfs] == [0.0, 5.0, 10.0]
    # The selected (a-b) keeps its 5s duration, just shifted later.
    a = next(k for k in new_kfs if k['id'] == 'a')
    b = next(k for k in new_kfs if k['id'] == 'b')
    assert round(b['time'] - a['time'], 1) == 5.0
    assert new_seg_id == f'{nid}-a'


def test_insert_at_end_when_nothing_selected() -> None:
    kfs = [kf('a', 0), kf('b', 5)]
    new_kfs, new_seg_id = insert_segment_keyframes(kfs)

    # Appended after the last keyframe; existing keyframes are untouched.
    nid = new_id_from(new_kfs, ('a', 'b'))
    assert [k['id'] for k in new_kfs] == ['a', 'b', nid]
    assert [k['time'] for k in new_kfs] == [0.0, 5.0, 10.0]
    assert new_seg_id == f'b-{nid}'


def test_insert_bootstraps_empty_timeline() -> None:
    new_kfs, new_seg_id = insert_segment_keyframes([])

    # Two keyframes at 0 and the default length form the first segment.
    assert [k['time'] for k in new_kfs] == [0.0, 5.0]
    first_id, second_id = (k['id'] for k in new_kfs)
    assert new_seg_id == f'{first_id}-{second_id}'


def test_insert_respects_custom_length() -> None:
    kfs = [kf('a', 0), kf('b', 5)]
    new_kfs, _ = insert_segment_keyframes(kfs, 'a-b', length=3.0)

    # The inserted block is exactly 3s wide (fresh keyframe at t=0, a at t=3).
    nid = new_id_from(new_kfs, ('a', 'b'))
    n = next(k for k in new_kfs if k['id'] == nid)
    a = next(k for k in new_kfs if k['id'] == 'a')
    assert round(a['time'] - n['time'], 1) == 3.0


def test_insert_rejects_unknown_segment() -> None:
    kfs = [kf('a', 0), kf('b', 5)]
    assert insert_segment_keyframes(kfs, 'zz-qq') is None


def test_insert_rejects_non_adjacent_pair() -> None:
    # a and c are not consecutive (b sits between them) — not a real segment.
    kfs = [kf('a', 0), kf('b', 5), kf('c', 10)]
    assert insert_segment_keyframes(kfs, 'a-c') is None


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for t in tests:
        t()
        print(f'ok  {t.__name__}')
    print(f'{len(tests)} tests passed')


if __name__ == '__main__':
    main()
