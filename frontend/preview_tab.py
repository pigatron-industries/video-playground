"""The "Preview" panel as a standalone widget.

Shows the rendered clip for the selected segment (or an earlier take). It owns
only the video element + status line; deciding *which* clip to show — and the
cache-busting timestamp so re-renders actually reload — is done here, while the
decision of *when* to swap clips stays in ``app.py``.
"""

import time

from nicegui import ui


class PreviewTab:
    """Video preview box + a one-line status caption."""

    def __init__(self, *, initial_message='Select a video segment to preview its rendered clip here.') -> None:
        with ui.column().classes('items-stretch gap-2 w-full').style(
            'height: 100%; overflow-y: auto; padding: 10px'
        ):
            self.video_box = ui.column().classes('w-full')
            self.status_label = ui.label(initial_message).classes('text-caption text-grey')

    def show_clip(self, src: str, message: str) -> None:
        """Replace the preview with a freshly-loaded video at ``src``.

        A cache-busting query string is appended so the browser fetches the new
        file instead of replaying a previously rendered clip from cache.
        """
        self.video_box.clear()
        with self.video_box:
            ui.video(f'{src}?t={int(time.time())}').style(
                'width:100%; aspect-ratio:16/9; background:#111'
            )
        self.status_label.text = message

    def clear(self, message: str) -> None:
        """Empty the preview and update the status caption."""
        self.video_box.clear()
        self.status_label.text = message
