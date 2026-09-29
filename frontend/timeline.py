from nicegui.element import Element


class Timeline(Element, component='timeline.js'):
    def __init__(self, *, total_duration=60, keyframes=None, segment_status=None,
                 on_select=None, on_change=None):
        super().__init__()
        self._props['totalDuration'] = total_duration
        self._props['keyframes'] = keyframes or []      # [{id,time,prompt,imagePath}]
        self._props['segmentStatus'] = segment_status or {}
        self._props['selected'] = {'kind': None, 'id': None}
        if on_select:
            self.on('select', on_select)   # e.args = {'kind': 'keyframe'|'segment'|None, 'id': str|None}
        if on_change:
            self.on('change', on_change)   # e.args = {'keyframes': [...]}

    def set_total_duration(self, v): self._props['totalDuration'] = v; self.update()

    def set_keyframes(self, kfs):    self._props['keyframes'] = kfs;   self.update()

    def sync_keyframes(self, kfs):
        # Keep the prop in step with what the widget already has, without pushing it back.
        self._props['keyframes'] = kfs

    def set_segment_statuses(self, mapping):
        self._props['segmentStatus'] = dict(mapping)
        self.update()

    def set_segment_status(self, seg_id, status):
        self._props['segmentStatus'] = {**self._props['segmentStatus'], seg_id: status}
        self.update()
        
    def select(self, kind, id_):
        self._props['selected'] = {'kind': kind, 'id': id_}; self.update()