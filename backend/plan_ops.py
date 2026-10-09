"""Pure edit operations on a RenderPlan.

The plan is an ordered list of segments; position in the list *is* position on
the timeline. Every function mutates the plan in place and returns something
the caller needs for UI follow-up (or None/False when the request was invalid,
e.g. a stale selection). Persisting and refreshing the UI is the caller's job.
"""

from backend.models import RenderPlan, Segment, new_segment_id

MIN_DURATION = 0.5
DEFAULT_DURATION = 5.0


def add_segment(
    plan: RenderPlan, before_id: str | None = None, duration: float = DEFAULT_DURATION
) -> Segment | None:
    """Insert a new empty segment before ``before_id``, or append it when
    ``before_id`` is None. Its start frame is seeded from the previous
    segment's end frame so consecutive segments line up."""
    if before_id is None:
        idx = len(plan.segments)
    else:
        idx = plan.index_of(before_id)
        if idx is None:
            return None
    seg = Segment(duration=round(max(duration, MIN_DURATION), 1))
    if idx > 0:
        seg.start_image_path = plan.segments[idx - 1].end_image_path
    plan.segments.insert(idx, seg)
    return seg


def duplicate_segment(plan: RenderPlan, seg_id: str) -> Segment | None:
    """Insert a copy (prompt, frames, trim/speed, clip + earlier takes)
    immediately after ``seg_id``. The copy references the same
    content-addressed clip, so it is playable straight away."""
    idx = plan.index_of(seg_id)
    if idx is None:
        return None
    copy = plan.segments[idx].model_copy(deep=True, update={"id": new_segment_id()})
    copy.status = "done" if copy.output_path else "empty"
    copy.error = None
    plan.segments.insert(idx + 1, copy)
    return copy


def remove_segment(plan: RenderPlan, seg_id: str) -> int | None:
    """Remove a segment; later ones slide back to fill its time. Returns the
    index it occupied (so the caller can select whatever now sits there)."""
    idx = plan.index_of(seg_id)
    if idx is None:
        return None
    del plan.segments[idx]
    return idx


def move_segment(plan: RenderPlan, from_idx: int, to_idx: int) -> bool:
    """Move the segment in slot ``from_idx`` to slot ``to_idx``."""
    n = len(plan.segments)
    if not (0 <= from_idx < n and 0 <= to_idx < n) or from_idx == to_idx:
        return False
    plan.segments.insert(to_idx, plan.segments.pop(from_idx))
    return True


def resize_segment(plan: RenderPlan, seg_id: str, duration: float) -> bool:
    """Set a segment's duration (clamped, rounded to 0.1s). Later segments
    shift automatically. Returns False when nothing changed."""
    seg = plan.get(seg_id)
    if seg is None:
        return False
    new = round(max(MIN_DURATION, float(duration)), 1)
    if new == seg.duration:
        return False
    seg.duration = new
    return True