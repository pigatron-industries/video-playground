"""
NiceGUI page for the keyframe app.

Layout is real NiceGUI (header, sidebar form, upload, buttons) — the only
thing that's raw HTML/JS is the canvas itself, embedded as a static div
plus <script src="/static/timeline.js">. The two sides talk over the
/api/ui/select polling bridge (see backend/api.py's docstring) rather than
NiceGUI's internal event system, so the canvas stays framework-agnostic.
"""

import httpx
from nicegui import ui


def build_page() -> None:
    @ui.page('/')
    def index():
        ui.add_head_html('<link rel="stylesheet" href="/static/timeline.css">')

        project_id_holder = {'id': None}

        with ui.header().classes('items-center q-pa-sm').style('background:#232328'):
            ui.label('Keyframe Timeline').classes('text-subtitle2')
            total_duration = ui.number('Total length (s)', value=60, min=1).props('dense outlined dark').classes('w-32')
            ui.space()
            ui.button('Export / Save', on_click=lambda: ui.run_javascript('window.exportPlan()'))
            ui.button('Concat Final Video', on_click=lambda: concat_final(project_id_holder['id']))

        with ui.row().classes('w-full no-wrap').style('height: calc(100vh - 60px)'):
            with ui.column().classes('flex-grow'):
                ui.html('<div id="canvasWrap"><canvas id="timeline"></canvas></div>')
                ui.label(
                    'Click the track to add a keyframe. Drag to reposition. '
                    'Click a keyframe to edit it in the panel on the right.'
                ).classes('text-caption text-grey q-pa-sm')

            with ui.column().classes('w-80 q-pa-md').style('border-left: 1px solid #34343c') as sidebar_col:
                ui.label('Selected Keyframe').classes('text-subtitle2 text-grey')
                no_selection_label = ui.label('Click a keyframe to edit it.').classes('text-caption text-grey')

                with ui.column().classes('w-full gap-2') as edit_panel:
                    edit_panel.visible = False
                    kf_time = ui.number('Time (s)', value=0).props('dense outlined')
                    kf_image_preview = ui.image().classes('w-full').style('aspect-ratio: 16/9; background:#232328')
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

        # --- state mirrored from the canvas via polling --------------------
        current_selection = {'keyframe_id': None}

        def api_url(path: str) -> str:
            # Same-origin — NiceGUI serves the API router on this app too.
            return f'http://127.0.0.1:8080{path}'

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
                else:
                    edit_panel.visible = True
                    no_selection_label.visible = False
                    kf_time.value = state.get('time', 0)
                    kf_prompt.value = state.get('prompt', '')
                    img_path = state.get('image_path')
                    kf_image_preview.source = img_path or ''

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

        kf_time.on('update:model-value', on_time_change)
        kf_prompt.on('update:model-value', on_prompt_change)

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
            kf_image_preview.source = result['url']
            if current_selection['keyframe_id']:
                ui.run_javascript(
                    f"window.setKeyframeImage('{current_selection['keyframe_id']}', '{result['url']}')"
                )

        def delete_current():
            if current_selection['keyframe_id']:
                ui.run_javascript(f"window.deleteKeyframe('{current_selection['keyframe_id']}')")
                edit_panel.visible = False
                no_selection_label.visible = True

        async def render_current_segment():
            # NOTE: rendering is defined per-segment (between two keyframes),
            # not per-keyframe. This assumes the backend can resolve "the
            # segment ending at this keyframe" — wire that lookup in once
            # segment IDs are settled; left as a TODO to keep this scaffold
            # honest rather than papering over it.
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

        total_duration.on(
            'update:model-value',
            lambda e: ui.run_javascript(f'window.setTotalDuration({e.value})'),
        )

        ui.add_body_html('<script src="/static/timeline.js"></script>')
