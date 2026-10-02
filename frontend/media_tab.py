"""The "Media" panel as a standalone widget.

Lists every image and rendered clip stored in the open project, with a
thumbnail, its media type (Image / Video), and how many video segments
reference it in the plan. It owns only the rendering of that table; *which*
files exist (and their usage counts) is resolved in ``app.py`` from the
project folder + plan and passed in via ``set_items`` — same split as
RenderQueueTab.

The "only rebuild when the list actually changed" check lives here, so the
short timer that keeps this panel live stays cheap while idle without app.py
tracking any signature state.
"""

from nicegui import ui


class MediaTab:
    """A thumbnail / type / usage-count table of the project's media files."""

    def __init__(self) -> None:
        self._last_signature = None

        with ui.column().classes('gap-2 w-full').style(
            'height: 100%; overflow-y: hidden; padding: 10px'
        ):
            self.count_label = ui.label('').classes('text-caption text-grey')
            # Header row — the fixed column widths below are shared with the
            # data rows so the columns line up.
            with ui.row().classes('items-center w-full gap-2').style(
                'padding: 6px 10px; border-bottom: 1px solid #34343c'
            ):
                ui.label('Thumbnail').classes('text-caption text-grey').style('width: 72px')
                ui.label('Type').classes('text-caption text-grey').style('flex: 1')
                ui.label('Segments using it').classes(
                    'text-caption text-grey'
                ).style('width: 130px; text-align: right')
            self.media_box = ui.column().classes('w-full gap-1').style('height: 100%; overflow-y: auto;');

    def set_items(self, items) -> None:
        """Render ``items`` (a list of dicts with kind / name / url / uses).

        Only touches the DOM when the list actually changed, so calling this on
        every timer tick is cheap while nothing new has been saved.
        """
        signature = tuple((i['kind'], i['name'], i['uses']) for i in items)
        if signature == self._last_signature:
            return
        self._last_signature = signature

        self.media_box.clear()
        with self.media_box:
            for item in items:
                kind, url, uses = item['kind'], item['url'], item['uses']
                with ui.row().classes('items-center w-full gap-2').style(
                    'padding: 6px 10px; background:#232328; border-radius: 6px'
                ):
                    if kind == 'image':
                        ui.image(url).style(
                            'width:72px; height:40px; object-fit:cover; '
                            'border-radius:4px; background:#111'
                        )
                    else:
                        # preload="metadata" makes the browser fetch just enough to
                        # paint the first frame as a thumbnail — no full clip download.
                        ui.html(
                            f'<video src="{url}" preload="metadata" muted playsinline '
                            f'style="width:72px;height:40px;object-fit:cover;'
                            f'border-radius:4px;background:#111"></video>'
                        )
                    ui.label('Image' if kind == 'image' else 'Video').classes(
                        'text-body2'
                    ).style(f'flex: 1')
                    uses_label = (
                        f'{uses} segment{"s" if uses != 1 else ""}'
                        if uses else 'unused'
                    )
                    # Unused files are likely orphans — dim them so they stand out.
                    color = '' if uses else '; color:#9e9ea7'
                    ui.label(uses_label).classes('text-caption').style(
                        f'width: 130px; text-align: right{color}'
                    )

        n_img = sum(1 for i in items if i['kind'] == 'image')
        n_vid = len(items) - n_img
        self.count_label.text = (
            f'{n_img} image{"s" if n_img != 1 else ""}, '
            f'{n_vid} video{"s" if n_vid != 1 else ""}'
        )
