"""The "Project" editor tab as a standalone widget.

Holds project-wide settings — currently just the generation resolution offered
in a dropdown. The list of presets and their (width, height) mapping stay in
``app.py`` (single source of truth); this widget is handed the option labels to
display and reports the user's choice back via ``on_resolution_change``.
"""

from nicegui import ui


class ProjectTab:
    """Project settings panel — resolution dropdown."""

    def __init__(self, *, options=None, default_value=None, on_resolution_change=None) -> None:
        self._on_resolution_change_cb = on_resolution_change

        with ui.column().classes('gap-2 w-full').style(
            'height: 100%; overflow-y: auto; padding: 10px'
        ):
            ui.label('Project Settings').classes('text-subtitle2 text-grey')
            ui.label(
                'Resolution used when generating video clips.'
            ).classes('text-caption text-grey')
            self.resolution_select = ui.select(
                options=options or [],
                value=default_value,
            ).props('dense outlined dark').classes('w-full')

        # Setting ``resolution_select.value`` fires this; app.py guards a
        # redundant save when the plan already matches.
        self.resolution_select.on_value_change(self._emit_resolution)

    def _emit_resolution(self, e) -> None:
        if self._on_resolution_change_cb is not None:
            self._on_resolution_change_cb(e.value)

    @property
    def resolution_value(self):
        return self.resolution_select.value

    def set_resolution(self, label: str) -> None:
        """Reflect a stored resolution in the dropdown (e.g. on project load)."""
        self.resolution_select.value = label
