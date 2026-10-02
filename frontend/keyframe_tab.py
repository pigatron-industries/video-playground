"""The "Keyframe" editor tab as a standalone widget.

A keyframe is a bare time marker on the timeline, so this panel only edits its
position and lets you delete it — the transition prompt and start/end frame
images belong to the *segment*, not here (see ``segment_tab.py``).

Like every other tab widget in this package, it owns only its UI. All domain
logic (plan load/save, keyframe lookup) lives in ``app.py``, which drives this
widget through the small public API below and receives user actions via the
callbacks passed to the constructor.
"""

from nicegui import ui


class KeyframeTab:
    """Time input + delete button for the currently selected keyframe."""

    def __init__(self, *, on_time_change=None, on_delete=None) -> None:
        self._on_time_change_cb = on_time_change
        self._on_delete_cb = on_delete

        with ui.column().classes('gap-2 w-full').style(
            'height: 100%; overflow-y: auto; padding: 10px'
        ):
            # ``panel`` is the show/hide container — hidden until a keyframe is
            # selected (the "nothing selected" hint takes its place).
            with ui.column().classes('w-full gap-2') as self.panel:
                self.panel.visible = False
                self.time_input = ui.number('Time (s)', value=0).props('dense outlined')
                with ui.row().classes('w-full gap-2'):
                    ui.button('Delete', on_click=self._on_delete)

        # Setting ``time_input.value`` fires this; app.py no-ops when the value
        # already matches the keyframe, so loading a selection is safe.
        self.time_input.on_value_change(self._emit_time_change)

    # -- internal ---------------------------------------------------------
    def _emit_time_change(self, e) -> None:
        if self._on_time_change_cb is not None:
            self._on_time_change_cb(e.value)

    def _on_delete(self) -> None:
        if self._on_delete_cb is not None:
            self._on_delete_cb()

    # -- public API -------------------------------------------------------
    @property
    def time_value(self):
        return self.time_input.value

    def set_keyframe(self, time: float) -> None:
        """Show the panel and load a keyframe's time into the input."""
        self.panel.visible = True
        self.time_input.value = time

    def hide(self) -> None:
        self.panel.visible = False
