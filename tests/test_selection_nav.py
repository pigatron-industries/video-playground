"""Tests for arrow-key selection navigation in frontend/app.py.

The left/right arrows move the timeline's segment selection to the previous /
next segment, from any starting state (a selected segment or none). The pure
decision lives in ``selection_target``; this exercises its edge cases.

No test framework is installed in this project, so run directly with the
project venv:

    .venv/bin/python tests/test_selection_nav.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from frontend.app import selection_target  # noqa: E402


IDS = ['a', 'b', 'c']


def test_right_from_none_selects_first() -> None:
    assert selection_target(IDS, None, +1) == 'a'


def test_left_from_none_selects_last() -> None:
    assert selection_target(IDS, None, -1) == 'c'


def test_right_moves_to_next() -> None:
    assert selection_target(IDS, 'a', +1) == 'b'
    assert selection_target(IDS, 'b', +1) == 'c'


def test_left_moves_to_previous() -> None:
    assert selection_target(IDS, 'c', -1) == 'b'
    assert selection_target(IDS, 'b', -1) == 'a'


def test_right_at_last_stays_put() -> None:
    # No segment beyond the last — the selection should not move.
    assert selection_target(IDS, 'c', +1) is None


def test_left_at_first_stays_put() -> None:
    assert selection_target(IDS, 'a', -1) is None


def test_empty_timeline_is_noop() -> None:
    assert selection_target([], None, +1) is None
    assert selection_target([], None, -1) is None


def test_stale_selection_is_noop() -> None:
    # The selected id is no longer on the timeline (e.g. it was deleted).
    assert selection_target(IDS, 'zzz', +1) is None
    assert selection_target(IDS, 'zzz', -1) is None


def test_single_segment_boundaries() -> None:
    # From nothing selected, either arrow lands on the only segment...
    assert selection_target(['only'], None, +1) == 'only'
    assert selection_target(['only'], None, -1) == 'only'
    # ...but once it is selected there is nowhere further to go.
    assert selection_target(['only'], 'only', +1) is None
    assert selection_target(['only'], 'only', -1) is None


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for t in tests:
        t()
        print(f'ok  {t.__name__}')
    print(f'{len(tests)} tests passed')


if __name__ == '__main__':
    main()
