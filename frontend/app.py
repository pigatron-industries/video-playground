"""
NiceGUI page for the keyframe app.

The timeline is now a native NiceGUI element (frontend/timeline.py +
frontend/timeline.js). Python owns the state: the widget receives keyframes,
total duration, segment status and selection as props, and reports back with
`select` and `change` events. No polling, no /api/ui/select bridge, no
window.* hooks, and no self-calls over HTTP.

Page layout (three sections):
  - top-left   : the selected keyframe / segment editor
  - top-right  : a video/image preview window
  - bottom     : the timeline, full page width, scrolls horizontally
"""

import asyncio
import time
from pathlib import Path

from nicegui import run, ui

from backend.models import Keyframe, RenderPlan, Segment
from backend.render import concat_segments, run_render
from backend.storage import (
    active_folder,
    load_plan,
    save_image,
    save_plan,
    set_active_folder,
)
from frontend.timeline import Timeline

# Single source of truth for the local server port — launch.py imports this
# for ui.run.
PORT = 8099

LEGACY_IMAGE_PREFIX = '/api/projects/images/'


# ---------------------------------------------------------------------------
# Plan <-> widget conversion (pure functions)
# ---------------------------------------------------------------------------
def _image_name(path: str | None) -> str | None:
    """Keyframe images are stored as bare filenames; strip the legacy URL prefix."""
    if not path:
        return None
    return path.removeprefix(LEGACY_IMAGE_PREFIX)


def _image_url(name: str | None) -> str | None:
    return f'{LEGACY_IMAGE_PREFIX}{name}' if name else None


def plan_to_keyframes(plan: RenderPlan) -> list[dict]:
    """RenderPlan -> the widget's keyframe format. A keyframe's prompt is
    persisted on the segment that *ends* at it, so map it back here."""
    prompt_by_end = {s.id.split('-', 1)[1]: s.prompt for s in plan.segments if '-' in s.id}
    return [
        {
            'id': k.id,
            'time': k.time,
            'prompt': prompt_by_end.get(k.id, ''),
            'imagePath': _image_name(k.image_path),
        }
        for k in sorted(plan.keyframes, key=lambda k: k.time)
    ]


def merge_keyframes_into_plan(plan: RenderPlan, kfs: list[dict]) -> RenderPlan:
    """Regenerate keyframes + segments from the widget's keyframes, carrying
    over render state (status, output, trim, speed, ...) for any segment whose
    id ("kfA-kfB") still exists."""
    kfs = sorted(kfs, key=lambda k: k['time'])
    old = {s.id: s for s in plan.segments}
    segs = []
    for a, b in zip(kfs, kfs[1:]):
        sid = f"{a['id']}-{b['id']}"
        s = old.get(sid) or Segment(id=sid, start_time=0, end_time=0, duration=0)
        s.start_time, s.end_time = a['time'], b['time']
        s.duration = round(b['time'] - a['time'], 1)
        s.start_image_path, s.end_image_path = a['imagePath'], b['imagePath']
        s.prompt = b.get('prompt', '')
        segs.append(s)
    plan.keyframes = [
        Keyframe(id=k['id'], time=k['time'], image_path=k['imagePath']) for k in kfs
    ]
    plan.segments = segs
    return plan


def build_page() -> None:
    @ui.page('/')
    def index():
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

        # Per-page working state. `kfs` is the list of keyframe dicts the
        # widget shows ([{id, time, prompt, imagePath}]); it is the working
        # copy that gets merged into the plan and persisted on every change.
        state = {
            'kfs': [],
            'sel_kind': None,   # 'keyframe' | 'segment' | None
            'sel_id': None,
            'video_src': None,
        }

        def is_open() -> bool:
            return active_folder() is not None

        # One full-viewport flex column: header (fixed) / top (form + preview,
        # flex-grow) / timeline (fixed at the bottom).
        with ui.column().classes('w-full').style(
            'height: 100vh; overflow: hidden; background:#1a1a1e'
        ):

            # ---- header --------------------------------------------------
            with ui.row().classes('items-center w-full').style(
                'height: 56px; padding: 0 16px; background:#232328; flex-shrink: 0; gap: 8px'
            ):
                ui.label('Keyframe Timeline').classes('text-subtitle2')
                folder_label = ui.label('No project open').classes('text-caption text-grey')
                ui.space()
                ui.button('Open Project', icon='folder_open', on_click=lambda: open_project()).props('outline')
                ui.button('Save', icon='save', on_click=lambda: do_save())
                ui.button('Concat Final Video', icon='movie', on_click=lambda: concat_final())

            # ---- top: editor form (left) + preview window (right) --------
            with ui.row().classes('w-full no-wrap').style(
                'flex: 1 1 auto; min-height: 0; overflow: hidden'
            ):
                # top-left: editor
                with ui.column().classes('q-pa-md gap-2').style(
                    'flex: 1 1 0; min-width: 0; overflow-y: auto; border-right: 1px solid #34343c; background:#232328'
                ):
                    sel_title = ui.label('Selected Keyframe').classes('text-subtitle2 text-grey')
                    no_selection_label = ui.label(
                        'Click a keyframe or a video segment in the timeline.'
                    ).classes('text-caption text-grey')

                    with ui.column().classes('w-full gap-2') as edit_panel:
                        edit_panel.visible = False
                        kf_time = ui.number('Time (s)', value=0).props('dense outlined')
                        kf_upload = ui.upload(
                            label='Keyframe image',
                            auto_upload=True,
                            on_upload=lambda e: handle_upload(e),
                        ).props('dense').classes('w-full')
                        kf_thumb = ui.image().style(
                            'width: 50%; min-width: 0; height: 96px; '
                            'object-fit: contain; background:#1a1a1e; '
                            'border: 1px solid #34343c'
                        )
                        kf_thumb.visible = False
                        kf_prompt = ui.textarea(
                            'Transition prompt (segment ending here)'
                        ).props('dense outlined debounce=400').classes('w-full')
                        with ui.row().classes('w-full gap-2'):
                            ui.button('Delete', on_click=lambda: delete_current())

                    # Selected-segment panel. Segments are the gaps between
                    # consecutive keyframes; the prompt is edited on the
                    # ending keyframe's panel.
                    with ui.column().classes('w-full gap-2') as seg_panel:
                        seg_panel.visible = False
                        seg_range = ui.label('').classes('text-subtitle2')
                        seg_prompt = ui.label('').classes('text-caption')
                        seg_status_label = ui.label('Status: empty').classes('text-caption')
                        with ui.row().classes('w-full gap-2'):
                            ui.button('Render segment', icon='movie', on_click=lambda: render_selected_segment())

                # top-right: preview
                with ui.column().classes('items-stretch gap-2 q-pa-md').style(
                    'flex: 1 1 0; min-width: 0; overflow-y: auto; background:#1a1a1e'
                ):
                    ui.label('Preview').classes('text-subtitle2 text-grey')
                    video_box = ui.column().classes('w-full')
                    video_status = ui.label(
                        'No rendered segment yet — render one to preview video here.'
                    ).classes('text-caption text-grey')
                    preview_image = ui.image().style('width:100%; aspect-ratio:16/9; background:#232328')
                    preview_image.visible = False

            # ---- timeline toolbar ----------------------------------------
            with ui.row().classes('items-center w-full no-wrap').style(
                'height: 44px; padding: 0 16px; background:#232328; flex-shrink: 0; gap: 8px; '
                'border-top: 1px solid #34343c'
            ):
                total_duration = ui.number('Total length (s)', value=60, min=1).props(
                    'dense outlined dark'
                ).classes('w-32')
                ui.space()

            # ---- bottom: the timeline widget -----------------------------
            with ui.column().classes('w-full').style(
                'height: 360px; flex-shrink: 0; overflow: hidden; '
                'border-top: 1px solid #34343c; background:#1a1a1e'
            ):
                timeline = Timeline().style('width: 100%; height: 100%; min-width: 0;')

        # ------------------------------------------------------------------
        # State helpers
        # ------------------------------------------------------------------
        def find_kf(kf_id: str | None) -> dict | None:
            return next((k for k in state['kfs'] if k['id'] == kf_id), None)

        def current_kf() -> dict | None:
            return find_kf(state['sel_id']) if state['sel_kind'] == 'keyframe' else None

        def find_segment(seg_id: str) -> Segment | None:
            if not is_open():
                return None
            return next((s for s in load_plan().segments if s.id == seg_id), None)

        def total() -> float:
            return total_duration.value or 60

        def commit() -> None:
            """Merge the working keyframes into the plan and persist it."""
            if not is_open():
                return
            plan = load_plan()
            plan.total_duration = total()
            merge_keyframes_into_plan(plan, state['kfs'])
            save_plan(plan)

        def push_keyframes() -> None:
            """Python-initiated change: send keyframes to the widget and persist."""
            timeline.set_keyframes(state['kfs'])
            commit()

        # ------------------------------------------------------------------
        # Selection -> sidebar
        # ------------------------------------------------------------------
        def select(kind: str | None, id_: str | None) -> None:
            state['sel_kind'], state['sel_id'] = kind, id_
            timeline.select(kind, id_)
            show_selection()

        def show_selection() -> None:
            kind, id_ = state['sel_kind'], state['sel_id']

            if kind == 'keyframe' and find_kf(id_):
                kf = find_kf(id_)
                sel_title.text = 'Selected Keyframe'
                seg_panel.visible = False
                edit_panel.visible = True
                no_selection_label.visible = False
                # Setting these fires on_value_change, but the handlers no-op
                # when the value already matches the keyframe.
                kf_time.value = kf['time']
                kf_prompt.value = kf['prompt']
                url = _image_url(kf['imagePath'])
                for img in (preview_image, kf_thumb):
                    if url:
                        img.source = url
                    img.visible = bool(url)
                return

            if kind == 'segment' and id_ and '-' in id_:
                a_id, b_id = id_.split('-', 1)
                a, b = find_kf(a_id), find_kf(b_id)
                if a and b:
                    sel_title.text = 'Selected Segment'
                    edit_panel.visible = False
                    no_selection_label.visible = False
                    preview_image.visible = False
                    kf_thumb.visible = False
                    seg_panel.visible = True
                    seg_range.text = (
                        f"{a['time']:.1f}s → {b['time']:.1f}s · {b['time'] - a['time']:.1f}s"
                    )
                    seg_prompt.text = (
                        b['prompt'] or 'No transition prompt set — edit it on the ending keyframe.'
                    )
                    refresh_status(id_)
                    seg = find_segment(id_)
                    if seg and seg.status == 'done':
                        src = f'/api/projects/clips/{id_}.mp4'
                        if state['video_src'] != src:
                            state['video_src'] = src
                            video_box.clear()
                            with video_box:
                                ui.video(f'{src}?t={int(time.time())}').style(
                                    'width:100%; aspect-ratio:16/9; background:#111')
                        video_status.text = 'Rendered clip — press play to watch.'
                    else:
                        state['video_src'] = None
                        video_box.clear()
                        video_status.text = 'No rendered clip yet — render the segment to preview video here.'
                    return

            # Nothing (valid) selected.
            state['sel_kind'] = state['sel_id'] = None
            sel_title.text = 'Selected Keyframe'
            seg_panel.visible = False
            edit_panel.visible = False
            no_selection_label.visible = True
            preview_image.visible = False
            kf_thumb.visible = False

        def refresh_status(seg_id: str) -> None:
            """Mirror a segment's render status onto its canvas block and,
            if it's the selected segment, the sidebar label."""
            seg = find_segment(seg_id)
            status = seg.status if seg else 'empty'
            timeline.set_segment_status(seg_id, status)
            if state['sel_id'] == seg_id:
                err = f' — {seg.error}' if seg and seg.error else ''
                seg_status_label.text = f'Status: {status}{err}'

        # ------------------------------------------------------------------
        # Widget events
        # ------------------------------------------------------------------
        def on_select(e) -> None:
            select(e.args['kind'], e.args['id'])

        def on_change(e) -> None:
            """A settled edit in the widget (drag end, keyframe added)."""
            state['kfs'] = e.args['keyframes']
            timeline.sync_keyframes(state['kfs'])  # keep the prop in sync, no echo
            commit()
            show_selection()  # a drag may have changed the selected time

        timeline.on('select', on_select)
        timeline.on('change', on_change)

        # ------------------------------------------------------------------
        # Form -> state (plain Python, no run_javascript)
        # ------------------------------------------------------------------
        def on_time_change(e) -> None:
            kf = current_kf()
            if kf is None or e.value is None:
                return
            t = round(min(total(), max(0.0, float(e.value))), 1)
            if t == kf['time']:
                return
            kf['time'] = t
            push_keyframes()

        def on_prompt_change(e) -> None:
            kf = current_kf()
            if kf is None or (e.value or '') == kf['prompt']:
                return
            kf['prompt'] = e.value or ''
            push_keyframes()

        def on_total_change(e) -> None:
            if not e.value:
                return
            timeline.set_total_duration(e.value)
            commit()

        kf_time.on_value_change(on_time_change)
        kf_prompt.on_value_change(on_prompt_change)
        total_duration.on_value_change(on_total_change)

        async def handle_upload(e) -> None:
            if not is_open():
                ui.notify('Open a project folder before adding images.', type='warning')
                return
            kf = current_kf()
            if kf is None:
                ui.notify('Select a keyframe first.', type='warning')
                return
            # NiceGUI 1.x/2.x: e.name + e.content. On 3.x use e.file.name /
            # `await e.file.read()` instead.
            filename = Path(e.name).name
            save_image(filename, e.content.read())
            kf['imagePath'] = filename
            kf_upload.reset()
            push_keyframes()
            show_selection()

        def delete_current() -> None:
            kf = current_kf()
            if kf is None:
                return
            state['kfs'] = [k for k in state['kfs'] if k['id'] != kf['id']]
            timeline.set_keyframes(state['kfs'])
            select(None, None)
            commit()

        # ------------------------------------------------------------------
        # Rendering / concat / save
        # ------------------------------------------------------------------
        async def render_selected_segment() -> None:
            seg_id = state['sel_id'] if state['sel_kind'] == 'segment' else None
            if not seg_id:
                ui.notify('Select a video segment in the timeline first.', type='warning')
                return
            if not is_open():
                ui.notify('Open a project folder before rendering.', type='warning')
                return

            commit()  # make sure the segment exists in the plan
            plan = load_plan()
            seg = next((s for s in plan.segments if s.id == seg_id), None)
            if seg is None:
                ui.notify('That segment is not in the plan.', type='negative')
                return
            if seg.status in ('queued', 'rendering'):
                ui.notify('This segment is already rendering.', type='warning')
                return

            seg.status = 'queued'
            seg.error = None
            save_plan(plan)
            refresh_status(seg_id)

            # run_render works on the shared in-memory plan, so status changes
            # are visible here while it runs in a worker thread.
            task = asyncio.create_task(run.io_bound(run_render, seg_id))
            while not task.done():
                await asyncio.sleep(0.5)
                refresh_status(seg_id)
            await task
            refresh_status(seg_id)

            seg = find_segment(seg_id)
            if seg and seg.status == 'done':
                state['video_src'] = None  # force the preview to reload the new clip
                show_selection()
                ui.notify('Segment rendered.', type='positive')
            else:
                ui.notify(
                    f"Render failed: {(seg.error if seg else None) or 'unknown error'}",
                    type='negative',
                )

        async def concat_final() -> None:
            if not is_open():
                ui.notify('Open a project folder first.', type='warning')
                return
            commit()
            plan = load_plan()
            if not plan.segments:
                ui.notify('Nothing to concat — add at least two keyframes.', type='warning')
                return
            missing = [s.id for s in plan.segments if s.status != 'done']
            if missing:
                ui.notify(f'Segments not yet rendered: {missing}', type='negative')
                return
            try:
                final_path = await run.io_bound(concat_segments, plan)
            except Exception as exc:  # noqa: BLE001 — surface ffmpeg failures
                ui.notify(f'Concat failed: {exc}', type='negative')
                return
            ui.notify(f'Final video: {final_path}')

        def do_save() -> None:
            if not is_open():
                ui.notify('Open a project folder before saving.', type='warning')
                return
            commit()
            ui.notify('Saved.', type='positive')

        # ------------------------------------------------------------------
        # Project open / restore
        # ------------------------------------------------------------------
        def load_project_into_ui() -> None:
            plan = load_plan()
            folder_label.text = str(active_folder())
            state['kfs'] = plan_to_keyframes(plan)
            state['video_src'] = None
            total_duration.value = plan.total_duration
            timeline.set_total_duration(plan.total_duration)
            timeline.set_keyframes(state['kfs'])
            timeline.set_segment_statuses({s.id: s.status for s in plan.segments})
            select(None, None)

        async def open_project() -> None:
            # Server-side folder dialog (see path_picker.py) — the browser can't
            # expose real filesystem paths, so we walk the server's own tree.
            from frontend.path_picker import pick_folder

            folder = active_folder()
            path = await pick_folder(str(folder) if folder else '')
            if not path:
                return
            folder = set_active_folder(path)
            load_project_into_ui()
            ui.notify(f'Opened project: {folder}', type='positive')

        # Restore on page load / browser refresh if the server already has a
        # project open (replaces the old first-poll restore).
        if is_open():
            load_project_into_ui()