"""The "Segment" editor tab as a standalone widget.

A segment is the unit of work: it owns the transition prompt, the start/end
frame images (shown side by side), and its render status / history. This panel
edits all of that for the currently selected segment.

It owns only its UI. Domain logic — resolving which segment is selected, saving
the plan, seeding neighbouring segments' frames, enqueuing renders — lives in
``app.py``, which drives this widget through ``set_segment`` / the small update
methods below and receives user actions via the constructor callbacks.
"""

from nicegui import ui


class SegmentTab:
    """Prompt, start/end frame uploaders, render status, history + generate."""

    def __init__(
        self,
        *,
        on_prompt_change=None,
        on_frame_upload=None,
        on_remove_frame=None,
        on_copy_prev_end=None,
        on_copy_start_to_end=None,
        on_generate=None,
        on_duplicate=None,
        on_history_select=None,
        on_duration_change=None,
    ) -> None:
        self._on_prompt_change_cb = on_prompt_change
        self._on_frame_upload_cb = on_frame_upload
        self._on_remove_frame_cb = on_remove_frame
        self._on_copy_prev_end_cb = on_copy_prev_end
        self._on_copy_start_to_end_cb = on_copy_start_to_end
        self._on_generate_cb = on_generate
        self._on_duplicate_cb = on_duplicate
        self._on_history_select_cb = on_history_select
        self._on_duration_change_cb = on_duration_change

        with ui.column().classes('gap-2 w-full').style(
            'height: 100%; overflow-y: auto; padding: 10px'
        ):
            # ``panel`` is the show/hide container — hidden until a segment is
            # selected (the "nothing selected" hint takes its place).
            with ui.column().classes('w-full gap-2') as self.panel:
                self.panel.visible = False
                self.range_label = ui.label('').classes('text-subtitle2')
                self.duration_input = ui.number(
                    'Duration (s)', value=5, min=0.5, step=0.5, format='%.1f'
                ).props('dense outlined debounce=500').classes('w-32')

                with ui.row().classes('w-full gap-3 items-start'):
                    # ---- start frame column ------------------------------
                    with ui.column().classes('flex-1 items-center gap-1 min-w-0'):
                        # Relative wrapper so the corner buttons can sit on the
                        # thumbnail; min-height keeps them anchored even before a
                        # start frame exists (the image is hidden).
                        with ui.element('div').style(
                            'position: relative; width: 100%; min-height: 140px'
                        ):
                            self.start_thumb = ui.image().style(
                                'width: 100%; height: 140px; '
                                'background:#1a1a1e; border: 1px solid #34343c'
                            ).props('fit=contain')
                            self.start_thumb.visible = False
                            self.start_delete = ui.button(
                                icon='close',
                                on_click=lambda: self._emit_remove_frame('start'),
                            ).props('flat dense round color=white size=sm').style(
                                'position: absolute; top: 4px; right: 4px;'
                            )
                            self.start_delete.visible = False
                            # Top-left corner: pull the previous segment's end
                            # frame in as this segment's start frame.
                            self.start_copy = ui.button(
                                icon='arrow_right',
                                on_click=self._on_copy_prev_end,
                            ).props('flat dense round color=white size=sm').style(
                                'position: absolute; top: 4px; left: 4px;'
                            )
                            self.start_copy.visible = False
                        self.start_upload = ui.upload(
                            label='Start frame',
                            auto_upload=True,
                            on_upload=self._on_start_upload,
                        ).props('dense').classes('w-full')

                    # ---- end frame column --------------------------------
                    with ui.column().classes('flex-1 items-center gap-1 min-w-0'):
                        # Same relative wrapper as the start column so the corner
                        # buttons have a stable anchor even before an end frame
                        # exists (the image is hidden).
                        with ui.element('div').style(
                            'position: relative; width: 100%; min-height: 140px'
                        ):
                            self.end_thumb = ui.image().style(
                                'width: 100%; height: 140px; '
                                'background:#1a1a1e; border: 1px solid #34343c'
                            ).props('fit=contain')
                            self.end_thumb.visible = False
                            self.end_delete = ui.button(
                                icon='close',
                                on_click=lambda: self._emit_remove_frame('end'),
                            ).props('flat dense round color=white size=sm').style(
                                'position: absolute; top: 4px; right: 4px;'
                            )
                            self.end_delete.visible = False
                            # Top-left corner: duplicate this segment's start
                            # frame into its end slot.
                            self.end_copy = ui.button(
                                icon='arrow_right',
                                on_click=self._on_copy_start_to_end,
                            ).props('flat dense round color=white size=sm').style(
                                'position: absolute; top: 4px; left: 4px;'
                            )
                            self.end_copy.visible = False
                        self.end_upload = ui.upload(
                            label='End frame',
                            auto_upload=True,
                            on_upload=self._on_end_upload,
                        ).props('dense').classes('w-full')

                self.prompt_input = ui.textarea('Transition prompt').props(
                    'dense outlined debounce=400'
                ).classes('w-full')
                self.status_label = ui.label('Status: empty').classes('text-caption')
                # Earlier takes of this segment (content-addressed, newest first) —
                # pick one to preview it.
                self.history_select = ui.select(
                    options={},
                    label='Earlier renders',
                ).props('dense outlined dark').classes('w-full')
                self.history_select.visible = False
                with ui.row().classes('w-full gap-2'):
                    self.generate_button = ui.button(
                        'Generate', icon='movie', on_click=self._on_generate
                    )
                    # Insert a copy of this segment (prompt, frames and clip)
                    # right after it, pushing later keyframes back to make room.
                    self.duplicate_button = ui.button(
                        'Duplicate', icon='content_copy', on_click=self._on_duplicate
                    ).props('outline')

        # Setting ``prompt_input.value`` fires this; app.py no-ops when the value
        # already matches the segment, so loading a selection is safe.
        self.prompt_input.on_value_change(self._emit_prompt_change)
        # Setting ``history_select.value`` fires this (app.py guards on selection).
        self.history_select.on_value_change(self._emit_history_select)
        self.duration_input.on_value_change(self._emit_duration_change)

    # -- internal emitters ------------------------------------------------
    def _emit_prompt_change(self, e) -> None:
        if self._on_prompt_change_cb is not None:
            self._on_prompt_change_cb(e.value)

    async def _on_start_upload(self, e) -> None:
        await self._emit_frame_upload('start', e)

    async def _on_end_upload(self, e) -> None:
        await self._emit_frame_upload('end', e)

    async def _emit_frame_upload(self, which: str, e) -> None:
        if self._on_frame_upload_cb is None:
            return
        result = self._on_frame_upload_cb(which, e)
        # app.py's handler may be a coroutine (it awaits file I/O); run it.
        if hasattr(result, '__await__'):
            await result

    def _emit_remove_frame(self, which: str) -> None:
        if self._on_remove_frame_cb is not None:
            self._on_remove_frame_cb(which)

    def _on_copy_prev_end(self) -> None:
        if self._on_copy_prev_end_cb is not None:
            self._on_copy_prev_end_cb()

    def _on_copy_start_to_end(self) -> None:
        if self._on_copy_start_to_end_cb is not None:
            self._on_copy_start_to_end_cb()

    def _on_generate(self) -> None:
        if self._on_generate_cb is not None:
            result = self._on_generate_cb()
            # generate_selected_segment is async; schedule it without blocking.
            if hasattr(result, '__await__'):
                import asyncio
                asyncio.ensure_future(result)

    def _on_duplicate(self) -> None:
        if self._on_duplicate_cb is not None:
            result = self._on_duplicate_cb()
            # duplicate_selected_segment may be a coroutine; schedule it.
            if hasattr(result, '__await__'):
                import asyncio
                asyncio.ensure_future(result)

    def _emit_history_select(self, e) -> None:
        if self._on_history_select_cb is not None and e.value:
            self._on_history_select_cb(e.value)

    def _emit_duration_change(self, e) -> None:
        if self._on_duration_change_cb is not None and e.value is not None:
            self._on_duration_change_cb(e.value)

    # -- public API -------------------------------------------------------
    @staticmethod
    def _set_thumb(img, url: str | None) -> None:
        img.source = url
        img.visible = bool(url)

    def set_segment(self, view: dict) -> None:
        """Show the panel and populate it from a plain ``view`` dict.

        Expected keys (all optional): ``range_text``, ``prompt``,
        ``start_image_url``, ``end_image_url``, ``status_text``,
        ``generate_busy``, ``history_options`` ({path: label}),
        ``show_start_copy``, ``show_end_copy``, ``duration``.
        """
        self.panel.visible = True
        if view.get('range_text'):
            self.range_label.text = view['range_text']

        if view.get('duration') is not None:
            self.duration_input.value = view['duration']

        # Setting this fires on_value_change; app.py no-ops when unchanged.
        self.prompt_input.value = view.get('prompt') or ''

        start_url = view.get('start_image_url')
        end_url = view.get('end_image_url')
        self._set_thumb(self.start_thumb, start_url)
        self._set_thumb(self.end_thumb, end_url)
        # Corner delete buttons only make sense when a frame is set; the copy
        # buttons only when there is something to copy.
        self.start_delete.visible = bool(start_url)
        self.end_delete.visible = bool(end_url)
        self.start_copy.visible = bool(view.get('show_start_copy'))
        self.end_copy.visible = bool(view.get('show_end_copy'))

        history_opts = view.get('history_options') or {}
        self.history_select.options = history_opts
        self.history_select.value = None
        self.history_select.visible = bool(history_opts)

        self.set_status_text(view.get('status_text', 'Status: empty'))
        self.update_generate_button(bool(view.get('generate_busy')))

    def set_status_text(self, text: str) -> None:
        self.status_label.text = text

    def set_range_text(self, text: str) -> None:
        self.range_label.text = text

    def update_generate_button(self, busy: bool) -> None:
        """Disable Generate while the segment is queued or rendering."""
        if busy:
            self.generate_button.disable()
        else:
            self.generate_button.enable()

    def reset_upload(self, which: str) -> None:
        """Clear a frame uploader's file list after its upload was handled."""
        (self.start_upload if which == 'start' else self.end_upload).reset()

    def hide(self) -> None:
        self.panel.visible = False
