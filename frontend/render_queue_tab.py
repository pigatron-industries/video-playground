"""The "Render Queue" panel as a standalone widget.

Lists the segments waiting to be rendered, with a live count. It owns only the
rendering of that list; *which* segments are pending (and their status / time
ranges) is resolved in ``app.py`` from the plan and passed in via ``set_items``.

The "only rebuild when the list actually changed" check lives here — it's a
presentation concern, so the short timer that keeps this panel live stays cheap
while idle without app.py tracking any signature state.
"""

from nicegui import ui


class RenderQueueTab:
    """A count line plus one row per queued segment."""

    def __init__(self, *, empty_message='Queue is empty — press "Render segment" on a video segment.') -> None:
        self._empty_message = empty_message
        self._last_signature = None

        with ui.column().classes('gap-2 w-full').style(
            'height: 100%; overflow-y: auto; padding: 10px'
        ):
            self.count_label = ui.label('').classes('text-caption text-grey')
            self.queue_box = ui.column().classes('w-full gap-1')

    def set_items(self, items) -> None:
        """Render ``items`` (a list of ``(label, status)`` tuples).

        Only touches the DOM when the list actually changed, so calling this on
        every timer tick is cheap while the queue is idle.
        """
        signature = tuple(items)
        if signature == self._last_signature:
            return
        self._last_signature = signature

        self.queue_box.clear()
        with self.queue_box:
            for label, status in items:
                with ui.row().classes('items-center w-full gap-2').style(
                    'padding: 6px 10px; background:#232328; border-radius: 6px'
                ):
                    ui.label(label).classes('text-body2')
                    ui.space()
                    color = {'queued': 'orange', 'rendering': 'blue'}.get(status, 'grey')
                    ui.badge(status, color=color)

        if items:
            n = len(items)
            self.count_label.text = f'{n} item{"s" if n != 1 else ""} waiting to render'
        else:
            self.count_label.text = self._empty_message
