"""
NiceGUI page for the keyframe app.

Layout is real NiceGUI (header, editor form, preview window, buttons) — the
only thing that's raw HTML/JS is the canvas timeline, embedded as a static
div plus <script src="/static/timeline.js">. The canvas talks to the backend
over the /api/ui/select polling bridge (see backend/api.py's docstring)
rather than NiceGUI's internal event system, so it stays framework-agnostic.

Page layout (three sections):
  - top-left   : the selected-keyframe editor form
  - top-right  : a video/image preview window
  - bottom     : the timeline, full page width, independently
                 scrollable horizontally
"""

import json

import httpx
from nicegui import ui

# Single source of truth for the local server port — launch.py imports this
# for ui.run, so api_url and the running server can never drift apart.
PORT = 8099


def build_page() -> None:
    @ui.page('/')
    def index():
        ui.add_head_html('<link rel="stylesheet" href="/static/timeline.css">')
        # Reset the default body margins so 100vh truly fills the viewport
        # and the three sections line up edge to edge.
        ui.add_head_html('<style>html,body{margin:0;padding:0}</style>')
        ui.add_css(
            """
            .q-page-container { height: 100vh; }
            .q-page { height: 100%; }
            .nicegui-content { height: 100%; padding: 0; gap: 0; }
            /* Keep the "Keyframe image" uploader compact — the file list is
               redundant since the thumbnail below already shows the image. */
            .q-uploader__list { display: none; }
            """
        )

        # The currently-open project folder (set by "Open Project"). Gates
        # save/upload/concat client-side so we can show a friendly message.
        project_folder = {'path': None}

        # One full-viewport flex column: header (fixed) / top (form + preview,
        # flex-grow) / timeline (fixed at the bottom).
        with ui.column().classes('w-full').style(
            'height: 100vh; overflow: hidden; background:#1a1a1e'
        ) as _root:

            # ---- header --------------------------------------------------
            with ui.row().classes('items-center w-full').style(
                'height: 56px; padding: 0 16px; background:#232328; flex-shrink: 0; gap: 8px'
            ) as _header:
                ui.label('Keyframe Timeline').classes('text-subtitle2')
                folder_label = ui.label('No project open').classes('text-caption text-grey')
                ui.space()
                ui.button('Open Project', icon='folder_open', on_click=lambda: open_project()).props('outline')
                ui.button('Save', icon='save', on_click=lambda: do_save())
                ui.button('Concat Final Video', icon='movie', on_click=lambda: concat_final())

            # ---- top: editor form (left) + preview window (right) --------
            with ui.row().classes('w-full no-wrap').style(
                'flex: 1 1 auto; min-height: 0; overflow: hidden'
            ) as _top_row:
                # top-left: selected-keyframe editor form
                with ui.column().classes('q-pa-md gap-2').style(
                    'flex: 1 1 0; min-width: 0; overflow-y: auto; border-right: 1px solid #34343c; background:#232328'
                ) as _editor:
                    ui.label('Selected Keyframe').classes('text-subtitle2 text-grey')
                    no_selection_label = ui.label('Click a keyframe in the timeline to edit it.').classes('text-caption text-grey')

                    with ui.column().classes('w-full gap-2') as edit_panel:
                        edit_panel.visible = False
                        kf_time = ui.number('Time (s)', value=0).props('dense outlined')
                        kf_upload = ui.upload(
                            label='Keyframe image',
                            auto_upload=True,
                            on_upload=lambda e: handle_upload(e),
                        ).props('dense').classes('w-full')
                        # Thumbnail of the selected keyframe's image, shown
                        # under the uploader (about half the form's width) so
                        # the current image is visible in the editor context.
                        # Follows selection/upload.
                        kf_thumb = ui.image().style(
                            'width: 50%; min-width: 0; height: 96px; '
                            'object-fit: contain; background:#1a1a1e; '
                            'border: 1px solid #34343c'
                        )
                        kf_thumb.visible = False
                        kf_prompt = ui.textarea('Transition prompt (segment ending here)').props('dense outlined').classes('w-full')
                        render_status = ui.label('').classes('text-caption')
                        with ui.row().classes('w-full gap-2'):
                            ui.button('Render segment', on_click=lambda: render_current_segment())
                            ui.button('Delete', on_click=lambda: delete_current())

                # top-right: video/image preview window
                with ui.column().classes('items-stretch gap-2 q-pa-md').style(
                    'flex: 1 1 0; min-width: 0; overflow-y: auto; background:#1a1a1e'
                ) as _preview:
                    ui.label('Preview').classes('text-subtitle2 text-grey')
                    # Video preview surface — plays a rendered segment clip once
                    # one is available (driven via window.setSegmentPreview).
                    ui.html('<video id="segPreview" controls playsinline style="width:100%; aspect-ratio:16/9; background:#111"></video>')
                    video_status = ui.label('No rendered segment yet — render one to preview video here.').classes('text-caption text-grey')
                    # Image preview of the selected keyframe (follows the selection).
                    preview_image = ui.image().style('width:100%; aspect-ratio:16/9; background:#232328')
                    preview_image.visible = False

            # ---- timeline toolbar: control row sitting directly above the canvas ----
            with ui.row().classes('items-center w-full no-wrap').style(
                'height: 44px; padding: 0 16px; background:#232328; flex-shrink: 0; gap: 8px; '
                'border-top: 1px solid #34343c'
            ) as _timeline_toolbar:
                total_duration = ui.number('Total length (s)', value=60, min=1).props('dense outlined dark').classes('w-32')
                ui.space()

            # ---- bottom: timeline, full page width, independent h-scroll --
            # The ui.html element renders a wrapper <div> that is the real flex
            # item of the .nicegui-column below. That column uses
            # align-items:flex-start and flex items default to min-width:auto,
            # so without an explicit width the wrapper grows to the canvas's
            # full width and #canvasWrap (100% of the wrapper) never overflows,
            # i.e. there is no scrollbar. Pin the wrapper to the bar's width and
            # drop its min-width floor so #canvasWrap becomes the scroll region.
            with ui.column().classes('w-full').style(
                'height: 360px; flex-shrink: 0; overflow: hidden; '
                'border-top: 1px solid #34343c; background:#1a1a1e'
            ) as _timeline:
                ui.html('<div id="canvasWrap"><canvas id="timeline"></canvas></div>') \
                    .style('width: 100%; height: 100%; min-width: 0;')

        # --- state mirrored from the canvas via polling --------------------
        current_selection = {'keyframe_id': None}

        def api_url(path: str) -> str:
            # Same-origin — NiceGUI serves the API router on this app too.
            return f'http://127.0.0.1:{PORT}{path}'

        async def poll_selection():
            # On first poll after page load, restore project state if the server
            # already has a project open (e.g. browser refresh).
            if not project_folder['path']:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(api_url('/api/projects/state'))
                ps = resp.json()
                if ps['path']:
                    project_folder['path'] = ps['path']
                    folder_label.text = ps['path']
                    if ps['plan']:
                        ui.run_javascript(f"window.loadPlan({json.dumps(ps['plan'])})")
                return

            async with httpx.AsyncClient() as client:
                resp = await client.get(api_url('/api/ui/select'))
                state = resp.json()

            kf_id = state.get('keyframe_id')
            kf_time_val = state.get('time', 0)

            if kf_id != current_selection['keyframe_id']:
                # Selection changed (or was cleared) — refresh the whole form.
                current_selection['keyframe_id'] = kf_id
                if kf_id is None:
                    current_selection['time'] = None
                    edit_panel.visible = False
                    no_selection_label.visible = True
                    preview_image.visible = False
                    kf_thumb.visible = False
                else:
                    current_selection['time'] = kf_time_val
                    edit_panel.visible = True
                    no_selection_label.visible = False
                    kf_time.value = kf_time_val
                    kf_prompt.value = state.get('prompt', '')
                    img_path = state.get('image_path')
                    if img_path:
                        # image_path may be just a filename or a legacy full URL.
                        img_url = (
                            img_path if img_path.startswith('/api/')
                            else f'/api/projects/images/{img_path}'
                        )
                        preview_image.source = img_url
                        preview_image.visible = True
                        kf_thumb.source = img_url
                        kf_thumb.visible = True
                    else:
                        preview_image.visible = False
                        kf_thumb.visible = False
            elif kf_id is not None and kf_time_val != current_selection.get('time'):
                # Same keyframe still selected, but its time moved on the canvas
                # (user dragged it) — sync just the time field. Comparing against
                # the last backend value (not the field's) means this won't
                # clobber a time the user is currently typing in the form.
                current_selection['time'] = kf_time_val
                kf_time.value = kf_time_val

        ui.timer(0.4, poll_selection)

        # --- form -> canvas -------------------------------------------------
        def on_time_change(e):
            if current_selection['keyframe_id']:
                ui.run_javascript(f"window.setKeyframeTime('{current_selection['keyframe_id']}', {e.value})")

        def on_prompt_change(e):
            if current_selection['keyframe_id']:
                ui.run_javascript(
                    f"window.setKeyframePrompt('{current_selection['keyframe_id']}', {json.dumps(e.value)})"
                )

        kf_time.on_value_change(on_time_change)
        kf_prompt.on_value_change(on_prompt_change)

        async def handle_upload(e):
            if not project_folder['path']:
                ui.notify('Open a project folder before adding images.', type='warning')
                return
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    api_url('/api/projects/images'),
                    files={'file': (e.name, e.content.read())},
                )
            result = resp.json()
            preview_image.source = result['url']
            preview_image.visible = True
            kf_thumb.source = result['url']
            kf_thumb.visible = True
            if current_selection['keyframe_id']:
                filename = result['url'].split('/')[-1]
                ui.run_javascript(
                    f"window.setKeyframeImage('{current_selection['keyframe_id']}', '{filename}')"
                )

        def delete_current():
            if current_selection['keyframe_id']:
                ui.run_javascript(f"window.deleteKeyframe('{current_selection['keyframe_id']}')")
                edit_panel.visible = False
                no_selection_label.visible = True
                preview_image.visible = False
                kf_thumb.visible = False

        async def render_current_segment():
            # NOTE: rendering is defined per-segment (between two keyframes),
            # not per-keyframe. This assumes the backend can resolve "the
            # segment ending at this keyframe" — wire that lookup in once
            # segment IDs are settled; left as a TODO to keep this scaffold
            # honest rather than papering over it. Once a clip URL is produced,
            # drive the preview with:
            #   ui.run_javascript(f"window.setSegmentPreview('<url>')")
            render_status.text = 'TODO: resolve segment id for this keyframe, then POST /render'

        async def concat_final():
            if not project_folder['path']:
                ui.notify('Open a project folder first.', type='warning')
                return
            async with httpx.AsyncClient() as client:
                resp = await client.post(api_url('/api/projects/concat'))
            if resp.status_code == 200:
                ui.notify(f"Final video: {resp.json()['output_path']}")
            else:
                ui.notify(f'Concat failed: {resp.text}', type='negative')

        def do_save():
            if not project_folder['path']:
                ui.notify('Open a project folder before saving.', type='warning')
                return
            ui.run_javascript('window.exportPlan()')

        async def open_project():
            # Server-side folder dialog (see path_picker.py) — the browser can't
            # expose real filesystem paths, so we walk the server's own tree.
            # Start at the currently-open folder (the last one the user opened,
            # persisted in config.json) so re-picking is one click instead of
            # navigating from home.
            from frontend.path_picker import pick_folder
            path = await pick_folder(project_folder['path'] or '')
            if not path:
                return
            async with httpx.AsyncClient() as client:
                resp = await client.post(api_url('/api/projects/open'), json={'path': path})
            result = resp.json()
            project_folder['path'] = result['path']
            folder_label.text = result['path']
            # Restore any existing timeline.json into the canvas.
            ui.run_javascript(f"window.loadPlan({json.dumps(result['plan'])})")
            ui.notify(f'Opened project: {result["path"]}', type='positive')

        total_duration.on_value_change(
            lambda e: ui.run_javascript(f'window.setTotalDuration({e.value})')
        )

        ui.add_body_html('<script src="/static/timeline.js"></script>')
