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
            """
        )

        project_id_holder = {'id': None}

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
                total_duration = ui.number('Total length (s)', value=60, min=1).props('dense outlined dark').classes('w-32')
                ui.space()
                ui.button('Export / Save', on_click=lambda: ui.run_javascript('window.exportPlan()'))
                ui.button('Concat Final Video', on_click=lambda: concat_final(project_id_holder['id']))

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

            # ---- bottom: timeline, full page width, independent h-scroll --
            # The ui.html element renders a wrapper <div> that is the real flex
            # item of the .nicegui-column below. That column uses
            # align-items:flex-start and flex items default to min-width:auto,
            # so without an explicit width the wrapper grows to the canvas's
            # full width and #canvasWrap (100% of the wrapper) never overflows,
            # i.e. there is no scrollbar. Pin the wrapper to the bar's width and
            # drop its min-width floor so #canvasWrap becomes the scroll region.
            with ui.column().classes('w-full').style(
                'height: 180px; flex-shrink: 0; overflow: hidden; '
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
            async with httpx.AsyncClient() as client:
                resp = await client.get(api_url('/api/ui/select'))
                state = resp.json()

            project_id_holder['id'] = state.get('project_id')
            kf_id = state.get('keyframe_id')

            if kf_id != current_selection['keyframe_id']:
                current_selection['keyframe_id'] = kf_id
                if kf_id is None:
                    edit_panel.visible = False
                    no_selection_label.visible = True
                    preview_image.visible = False
                else:
                    edit_panel.visible = True
                    no_selection_label.visible = False
                    kf_time.value = state.get('time', 0)
                    kf_prompt.value = state.get('prompt', '')
                    img_path = state.get('image_path')
                    if img_path:
                        preview_image.source = img_path
                        preview_image.visible = True
                    else:
                        preview_image.visible = False

        ui.timer(0.4, poll_selection)

        # --- form -> canvas -------------------------------------------------
        def on_time_change(e):
            if current_selection['keyframe_id']:
                ui.run_javascript(f"window.setKeyframeTime('{current_selection['keyframe_id']}', {e.value})")

        def on_prompt_change(e):
            if current_selection['keyframe_id']:
                import json
                ui.run_javascript(
                    f"window.setKeyframePrompt('{current_selection['keyframe_id']}', {json.dumps(e.value)})"
                )

        kf_time.on_value_change(on_time_change)
        kf_prompt.on_value_change(on_prompt_change)

        async def handle_upload(e):
            pid = project_id_holder['id']
            if not pid:
                ui.notify('Save/export the project once before adding images.', type='warning')
                return
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    api_url(f'/api/projects/{pid}/images'),
                    files={'file': (e.name, e.content.read())},
                )
            result = resp.json()
            preview_image.source = result['url']
            preview_image.visible = True
            if current_selection['keyframe_id']:
                ui.run_javascript(
                    f"window.setKeyframeImage('{current_selection['keyframe_id']}', '{result['url']}')"
                )

        def delete_current():
            if current_selection['keyframe_id']:
                ui.run_javascript(f"window.deleteKeyframe('{current_selection['keyframe_id']}')")
                edit_panel.visible = False
                no_selection_label.visible = True
                preview_image.visible = False

        async def render_current_segment():
            # NOTE: rendering is defined per-segment (between two keyframes),
            # not per-keyframe. This assumes the backend can resolve "the
            # segment ending at this keyframe" — wire that lookup in once
            # segment IDs are settled; left as a TODO to keep this scaffold
            # honest rather than papering over it. Once a clip URL is produced,
            # drive the preview with:
            #   ui.run_javascript(f"window.setSegmentPreview('<url>')")
            render_status.text = 'TODO: resolve segment id for this keyframe, then POST /render'

        async def concat_final(pid):
            if not pid:
                ui.notify('No project saved yet.', type='warning')
                return
            async with httpx.AsyncClient() as client:
                resp = await client.post(api_url(f'/api/projects/{pid}/concat'))
            if resp.status_code == 200:
                ui.notify(f"Final video: {resp.json()['output_path']}")
            else:
                ui.notify(f'Concat failed: {resp.text}', type='negative')

        total_duration.on_value_change(
            lambda e: ui.run_javascript(f'window.setTotalDuration({e.value})')
        )

        ui.add_body_html('<script src="/static/timeline.js"></script>')
