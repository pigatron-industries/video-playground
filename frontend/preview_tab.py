"""The "Preview" panel as a standalone widget.

Shows the rendered clip for the selected segment (or an earlier take). It owns
only the video element + loop toggle + status line; deciding *which* clip to
show — and the cache-busting timestamp so re-renders actually reload — is done
here, while the decision of *when* to swap clips stays in ``app.py``.
"""

import time

from nicegui import ui


class PreviewTab:
    """Video preview box + loop toggle + a one-line status caption."""

    _VIDEO_CLASS = 'preview-clip'
    _BTN_CLASS = 'loop-toggle-btn'

    def __init__(self, *, initial_message='Select a video segment to preview its rendered clip here.') -> None:
        self._video_el: ui.video | None = None
        self._loop = False
        with ui.column().classes('items-stretch gap-2 w-full').style(
            'height: 100%; overflow-y: auto; padding: 10px'
        ):
            # Toolbar (rebuilt on toggle so Quasar renders correct state from scratch)
            self._toolbar = ui.row().classes('items-center w-full no-wrap').style('gap: 4px')
            self._build_loop_btn()
            self.video_box = ui.column().classes('w-full')
            self.status_label = ui.label(initial_message).classes('text-caption text-grey')

    def _build_loop_btn(self) -> None:
        """(Re)create the loop button with props matching the current state."""
        self._toolbar.clear()
        with self._toolbar:
            if self._loop:
                ui.button(icon='repeat', on_click=self._toggle_loop).props(
                    'round dense color=positive text-color=white'
                ).tooltip('Loop playback (on)')
            else:
                ui.button(icon='repeat', on_click=self._toggle_loop).props(
                    'round dense flat'
                ).tooltip('Loop playback (off)')

    def _toggle_loop(self) -> None:
        """Flip the loop state, rebuild the button, and sync the video element."""
        self._loop = not self._loop
        self._build_loop_btn()
        self._sync_loop()

    def _sync_loop(self) -> None:
        """Apply the current loop state to the loaded <video> node in place."""
        if self._video_el is None:
            return
        val = 'true' if self._loop else 'false'
        ui.run_javascript(
            f'(function(){{var c=document.querySelector(".{self._VIDEO_CLASS}");'
            f'if(!c)return;var v=c.tagName==="VIDEO"?c:c.querySelector("video");'
            f'if(v)v.loop={val};}})();'
        )

    def set_end(self, end: float | None) -> None:
        """Stop (or loop) playback at ``end`` seconds without touching the file."""
        if self._video_el is None:
            return
        val = 'null' if end is None else f'{end:.3f}'
        ui.run_javascript(f'''(function(){{
      var c=document.querySelector(".{self._VIDEO_CLASS}");
      var v=c&&(c.tagName==="VIDEO"?c:c.querySelector("video"));
      if(!v) return;
      v._end={val};
      if(v._limitBound) return;
      v._limitBound=true;
      (function tick(){{
        if(v._end!=null && v.currentTime>=v._end){{
          if(v.loop){{ v.currentTime=0; v.play(); }}
          else {{ v.pause(); v.currentTime=v._end; }}
        }}
        requestAnimationFrame(tick);
      }})();
    }})();''')

    def show_clip(self, src: str, message: str) -> None:
        """Replace the preview with a freshly-loaded video at ``src``.

        A cache-busting query string is appended so the browser fetches the new
        file instead of replaying a previously rendered clip from cache.
        """
        self.video_box.clear()
        with self.video_box:
            v = ui.video(f'{src}?t={int(time.time())}').classes(self._VIDEO_CLASS).style(
                'width:100%; aspect-ratio:16/9; background:#111'
            )
            if self._loop:
                v.props('loop')
            self._video_el = v
        self.status_label.text = message

    def clear(self, message: str) -> None:
        """Empty the preview and update the status caption."""
        self.video_box.clear()
        self._video_el = None
        self.status_label.text = message
