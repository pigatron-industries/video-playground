from nicegui.element import Element


class Timeline(Element, component='timeline.js'):
    def __init__(self, *, segments=None, segment_status=None, segment_prompts=None,
                 on_select=None, on_resize=None, on_reorder=None):
        super().__init__()
        self._props['segments'] = segments or []               # [{id, duration}]
        self._props['segmentStatus'] = segment_status or {}    # segId -> status
        self._props['segmentPrompts'] = segment_prompts or {}  # segId -> has prompt
        self._props['selected'] = None                         # segId | None
        if on_select:
            self.on('select', on_select)    # e.args = {'id': str | None}
        if on_resize:
            self.on('resize', on_resize)    # e.args = {'id': str, 'duration': float}
        if on_reorder:
            self.on('reorder', on_reorder)  # e.args = {'from': int, 'to': int}

    def load(self, segments, statuses, prompts):
        """Replace everything in one round trip."""
        self._props['segments'] = segments
        self._props['segmentStatus'] = dict(statuses)
        self._props['segmentPrompts'] = dict(prompts)
        self.update()

    def set_segments(self, segs):
        self._props['segments'] = segs
        self.update()

    def sync_segments(self, segs):
        # Keep the prop in step with what the widget already has, without pushing it back.
        self._props['segments'] = segs

    def set_segment_statuses(self, mapping):
        self._props['segmentStatus'] = dict(mapping)
        self.update()

    def set_segment_prompts(self, mapping):
        self._props['segmentPrompts'] = dict(mapping)
        self.update()

    def set_segment_status(self, seg_id, status):
        self._props['segmentStatus'] = {**self._props['segmentStatus'], seg_id: status}
        self.update()

    def select(self, seg_id):
        self._props['selected'] = seg_id
        self.update()