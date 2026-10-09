"""
NiceGUI page for the keyframe app.

The plan is just an ordered list of segments laid end to end; a segment's start
time is the sum of the durations before it (see backend/models.py). All plan
edits live in backend/plan_ops.py — this module wires the UI to them.

The timeline is a native NiceGUI element (frontend/timeline.py + timeline.js).
Python owns the state: the widget receives segments, statuses, prompts and the
selection as props, and reports back with `select`, `resize` and `reorder`.

Page layout (three sections):
  - top-left   : the selected segment / project editor
  - top-right  : a video/image preview window
  - bottom     : the timeline, full page width, scrolls horizontally
"""

import asyncio
from pathlib import Path

from nicegui import run, ui

from backend.models import RenderPlan, Segment
from backend.plan_ops import (
    DEFAULT_DURATION,
    add_segment,
    duplicate_segment,
    move_segment,
    remove_segment,
    resize_segment,
)
from backend.queue import render_queue
from backend.render import concat_segments
from backend.storage import (
    active_folder,
    load_plan,
    save_image,
    save_plan,
    set_active_folder,
)
from frontend.media_tab import MediaTab
from frontend.preview_tab import PreviewTab
from frontend.project_tab import ProjectTab
from frontend.render_queue_tab import RenderQueueTab
from frontend.segment_tab import SegmentTab
from frontend.timeline import Timeline

# Single source of truth for the local server port — launch.py imports this
# for ui.run.
PORT = 8099

LEGACY_IMAGE_PREFIX = '/api/projects/images/'

# Common generation resolutions offered in the Project tab dropdown, as
# {display label: (width, height)}. The plan stores width/height separately so
# generate.py can pass [width, height] straight to the workflow API.
RESOLUTIONS = {
    'Landscape · 768 × 448': (768, 448),
    'Landscape · 1344 × 768': (1344, 768),
    'Portrait · 448 × 768': (448, 768),
    'Portrait · 768 × 1344': (768, 1344),
}
DEFAULT_RESOLUTION_LABEL = 'Landscape · 768 × 448'
RES_BY_SIZE = {(w, h): label for label, (w, h) in RESOLUTIONS.items()}


# ---------------------------------------------------------------------------
# Plan <-> widget conversion (pure functions)
# ---------------------------------------------------------------------------
def _image_name(path: str | None) -> str | None:
    """Frame images are stored as bare filenames; strip the legacy URL prefix."""
    if not path:
        return None
    return path.removeprefix(LEGACY_IMAGE_PREFIX)


def _image_url(name: str | None) -> str | None:
    return f'{LEGACY_IMAGE_PREFIX}{name}' if name else None


def segments_payload(plan: RenderPlan) -> list[dict]:
    """RenderPlan -> the timeline widget's segment format.

    Durations are sent as *seconds* (converted from each segment's stored frame
    count at its own fps): the canvas lays blocks out in pixels-per-second, so
    it needs seconds to get block widths right."""
    return [{'id': s.id, 'duration': s.duration_seconds} for s in plan.segments]


def build_page() -> None:
    @ui.page('/')
    def index():
        # Reset the default body margins so 100vh truly fills the viewport
        # and the sections line up edge to edge.
        ui.add_head_html('<style>html,body{margin:0;padding:0}</style>')
        ui.add_css(
            """
            .q-page-container { height: 100vh; }
            .q-page { height: 100%; }
            .nicegui-content { height: 100%; padding: 0; gap: 0; }
            /* Keep the start/end frame uploaders compact — the file list is
               redundant since the thumbnail above each already shows the image. */
            .q-uploader__list { display: none; }
            /* Compact tab bars (Quasar's default 48px eats vertical space). */
            .q-tabs .q-tab { min-height: 32px; font-size: 13px; }
            /* Let each tab panel fill its flex-sized container so the inner
               columns can scroll instead of overflowing past the viewport. */
            .q-tab-panel { height: 100%; min-height: 0; }
            """
        )

        # Per-page working state: just the selection and the previewed clip.
        # The segments themselves live in the plan (storage.load_plan()).
        state = {
            'sel_id': None,     # selected segment id, or None
            'video_src': None,
        }

        def is_open() -> bool:
            return active_folder() is not None

        # One full-viewport flex column: header (fixed) / top (form + preview,
        # flex-grow) / timeline toolbar + timeline (fixed at the bottom).
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
                # top-left: editor — "Segment" edits the selected segment (the
                # unit of work: duration, prompt, start/end frames, render);
                # "Project" holds project-wide settings such as resolution. The
                # Segment tab only shows while a segment is selected.
                with ui.column().classes('w-full no-wrap').style(
                    'flex: 1 1 0; min-width: 0; border-right: 1px solid #34343c; background:#232328'
                ):
                    with ui.tabs().classes('w-full') as left_tabs:
                        segment_tab = ui.tab('Segment')
                        project_tab = ui.tab('Project')

                    no_selection_label = ui.label(
                        'Click a video segment in the timeline.'
                    ).classes('text-caption text-grey w-full').style('padding: 8px 10px')

                    with ui.tab_panels(left_tabs, value=project_tab).style(
                        'flex: 1 1 auto; min-height: 0'
                    ).classes('w-full'):
                        with ui.tab_panel(segment_tab):
                            segment_widget = SegmentTab(
                                on_prompt_change=lambda value: on_seg_prompt_change(value),
                                on_duration_change=lambda value: on_seg_duration_change(value),
                                on_frame_upload=lambda which, e: handle_frame_upload(which, e),
                                on_remove_frame=lambda which: remove_frame(which),
                                on_copy_prev_end=lambda: copy_prev_end_frame(),
                                on_copy_start_to_end=lambda: copy_start_to_end_frame(),
                                on_generate=lambda: generate_selected_segment(),
                                on_duplicate=lambda: duplicate_selected_segment(),
                                on_history_select=lambda path: on_history_select(path),
                            )

                        with ui.tab_panel(project_tab):
                            project_widget = ProjectTab(
                                options=list(RESOLUTIONS),
                                default_value=DEFAULT_RESOLUTION_LABEL,
                                on_resolution_change=lambda value: on_resolution_change(value),
                            )

                # top-right: tabbed panel — Preview / Render Queue / Media.
                with ui.column().classes('w-full no-wrap').style(
                    'flex: 1 1 0; min-width: 0; border-right: 1px solid #34343c; background:#232328; height: 100%; overflow: hidden'
                ):
                    with ui.tabs().classes('w-full') as preview_tabs:
                        preview_tab = ui.tab('Preview')
                        queue_tab = ui.tab('Render Queue')
                        media_tab = ui.tab('Media')

                    with ui.tab_panels(preview_tabs, value=preview_tab).style(
                        'flex: 1 1 auto; min-height: 0;'
                    ).classes('w-full'):
                        with ui.tab_panel(preview_tab):
                            preview_widget = PreviewTab()
                        with ui.tab_panel(queue_tab):
                            queue_widget = RenderQueueTab()
                        with ui.tab_panel(media_tab):
                            media_widget = MediaTab()

            # ---- timeline toolbar ----------------------------------------
            with ui.row().classes('items-center w-full no-wrap').style(
                'height: 44px; padding: 0 16px; background:#232328; flex-shrink: 0; gap: 8px; '
                'border-top: 1px solid #34343c'
            ):
                ui.button(
                    'Duplicate', icon='content_copy',
                    on_click=lambda: duplicate_selected_segment(),
                ).props('dense outlined dark')
                # Insert a fresh segment before the selected one, or at the end
                # of the timeline when nothing is selected.
                ui.button(
                    'Insert', icon='add',
                    on_click=lambda: insert_segment(),
                ).props('dense outlined dark')
                ui.button(
                    'Delete', icon='delete',
                    on_click=lambda: delete_selected_segment(),
                ).props('dense outlined dark')
                ui.space()
                total_label = ui.label('Total 0.0s').classes('text-caption text-grey')

            # ---- bottom: the timeline widget -----------------------------
            with ui.column().classes('w-full').style(
                'height: 200px; flex-shrink: 0; overflow: hidden; '
                'border-top: 1px solid #34343c; background:#1a1a1e'
            ):
                timeline = Timeline().style('width: 100%; height: 100%; min-width: 0;')

        # ------------------------------------------------------------------
        # State helpers
        # ------------------------------------------------------------------
        def find_segment(seg_id: str | None) -> Segment | None:
            if not is_open() or not seg_id:
                return None
            return load_plan().get(seg_id)

        def current_segment() -> Segment | None:
            return find_segment(state['sel_id'])

        def neighbor(seg: Segment, offset: int) -> Segment | None:
            """The segment ``offset`` slots away from ``seg`` (-1 previous, +1 next)."""
            if not is_open():
                return None
            plan = load_plan()
            i = plan.index_of(seg.id)
            if i is None:
                return None
            j = i + offset
            return plan.segments[j] if 0 <= j < len(plan.segments) else None

        def range_text_for(seg: Segment) -> str:
            span = load_plan().span_of(seg.id)
            if span is None:
                return ''
            return f'{span[0]:.1f}s → {span[1]:.1f}s · {seg.duration_seconds:.1f}s'

        def push_timeline(plan: RenderPlan) -> None:
            """Python-initiated change: send the whole layout to the widget."""
            timeline.load(
                segments=segments_payload(plan),
                statuses={s.id: s.status for s in plan.segments},
                prompts={s.id: bool(s.prompt) for s in plan.segments},
            )
            total_label.text = f'Total {plan.duration:.1f}s'

        # ------------------------------------------------------------------
        # Selection -> sidebar
        # ------------------------------------------------------------------
        def select(seg_id: str | None) -> None:
            state['sel_id'] = seg_id
            timeline.select(seg_id)
            show_selection()

        def show_selection() -> None:
            seg = find_segment(state['sel_id'])

            if seg is None:
                # Nothing (valid) selected — hide the Segment tab, fall back to
                # the Project tab, and show the hint.
                if state['sel_id'] is not None:
                    state['sel_id'] = None
                    timeline.select(None)
                segment_tab.visible = False
                left_tabs.set_value(project_tab)
                segment_widget.hide()
                no_selection_label.visible = True
                preview_widget.clear('Click a video segment in the timeline.')
                return

            segment_tab.visible = True
            left_tabs.set_value(segment_tab)
            no_selection_label.visible = False

            prev_seg = neighbor(seg, -1)
            # Offer earlier takes of this segment (newest first).
            history_opts = {
                p: ('Previous render' if i == 0 else f'Earlier render {i + 1}')
                for i, p in enumerate(seg.history or [])
            }
            err = f' — {seg.error}' if seg.error else ''
            segment_widget.set_segment({
                'range_text': range_text_for(seg),
                'duration': seg.duration_seconds,
                'prompt': seg.prompt or '',
                'start_image_url': _image_url(_image_name(seg.start_image_path)),
                'end_image_url': _image_url(_image_name(seg.end_image_path)),
                'status_text': f'Status: {seg.status}{err}',
                'generate_busy': seg.status in ('queued', 'rendering'),
                'history_options': history_opts,
                # Corner copy buttons only make sense when there is something to
                # copy (the previous segment's end frame for start, this segment's
                # own start frame for end).
                'show_start_copy': bool(prev_seg and prev_seg.end_image_path),
                'show_end_copy': bool(seg.start_image_path),
            })
            timeline.set_segment_status(seg.id, seg.status)

            if seg.status == 'done':
                # Clips are content-addressed (<sha256>.mp4); fall back to the
                # legacy segment-id name for takes rendered before that scheme.
                clip_name = (
                    Path(seg.output_path).name if seg.output_path else f'{seg.id}.mp4'
                )
                src = f'/api/projects/clips/{clip_name}'
                if state['video_src'] != src:
                    state['video_src'] = src
                    preview_widget.show_clip(src, 'Rendered clip — press play to watch.')
                # Cap playback at the segment's duration (the rendered clip may be longer).
                preview_widget.set_end(seg.duration_seconds)
            else:
                state['video_src'] = None
                preview_widget.clear('No rendered clip yet — render the segment to preview video here.')

        def refresh_status(seg_id: str) -> None:
            """Mirror a segment's render status onto its canvas block and, if it's the
            selected segment, the sidebar label + Generate button state."""
            seg = find_segment(seg_id)
            status = seg.status if seg else 'empty'
            timeline.set_segment_status(seg_id, status)
            if state['sel_id'] == seg_id:
                segment_widget.update_generate_button(status in ('queued', 'rendering'))
                err = f' — {seg.error}' if seg and seg.error else ''
                segment_widget.set_status_text(f'Status: {status}{err}')

        def refresh_queue_panel() -> None:
            """Rebuild the Render Queue tab from render_queue.pending(). The widget
            only touches the DOM when the list actually changed."""
            items = []
            plan = load_plan() if is_open() else None
            for seg_id in render_queue.pending():
                seg = plan.get(seg_id) if plan else None
                status = seg.status if seg else 'queued'
                span = plan.span_of(seg_id) if plan else None
                label = f'{seg_id}  ({span[0]:.1f}s – {span[1]:.1f}s)' if span else seg_id
                items.append((label, status))
            queue_widget.set_items(items)

        def _media_usage_counts(plan: RenderPlan) -> tuple[dict[str, int], dict[str, int]]:
            """How many distinct segments reference each image / clip file."""
            images: dict[str, int] = {}
            clips: dict[str, int] = {}
            for seg in plan.segments:
                img_names = {Path(p).name for p in (seg.start_image_path, seg.end_image_path) if p}
                for name in img_names:
                    images[name] = images.get(name, 0) + 1
                clip_names = set()
                if seg.output_path:
                    clip_names.add(Path(seg.output_path).name)
                for take in seg.history or []:
                    clip_names.add(Path(take).name)
                for name in clip_names:
                    clips[name] = clips.get(name, 0) + 1
            return images, clips

        def refresh_media_panel() -> None:
            """Rebuild the Media tab from the project's images/ and clips/ folders."""
            if not is_open():
                media_widget.set_items([])
                return
            folder = active_folder()
            image_counts, clip_counts = _media_usage_counts(load_plan())
            items = [
                {'kind': 'image', 'name': p.name,
                 'url': f'/api/projects/images/{p.name}', 'uses': image_counts.get(p.name, 0)}
                for p in sorted((folder / 'images').glob('*')) if p.is_file()
            ] + [
                {'kind': 'video', 'name': p.name,
                 'url': f'/api/projects/clips/{p.name}', 'uses': clip_counts.get(p.name, 0)}
                for p in sorted((folder / 'clips').glob('*')) if p.is_file()
            ]
            media_widget.set_items(items)

        # ------------------------------------------------------------------
        # Widget events
        # ------------------------------------------------------------------
        def on_select(e) -> None:
            select(e.args['id'])

        def on_resize(e) -> None:
            """An edge drag settled: the segment got a new duration, later ones ripple."""
            if not is_open():
                return
            plan = load_plan()
            if not resize_segment(plan, e.args['id'], e.args['duration']):
                return
            save_plan(plan)
            timeline.sync_segments(segments_payload(plan))  # keep the prop in sync, no echo
            total_label.text = f'Total {plan.duration:.1f}s'
            show_selection()  # the selected segment's range may have shifted

        def on_reorder(e) -> None:
            """A segment was drag-reordered (e.args = {from, to}, slot indices).
            Ids are stable, so queued/rendering jobs are unaffected."""
            if not is_open():
                return
            plan = load_plan()
            to_idx = int(e.args['to'])
            if not move_segment(plan, int(e.args['from']), to_idx):
                return
            save_plan(plan)
            push_timeline(plan)
            select(plan.segments[to_idx].id)

        timeline.on('select', on_select)
        timeline.on('resize', on_resize)
        timeline.on('reorder', on_reorder)

        # ------------------------------------------------------------------
        # Form -> state
        # ------------------------------------------------------------------
        def on_seg_duration_change(value) -> None:
            seg = current_segment()
            if seg is None or value is None:
                return
            plan = load_plan()
            if not resize_segment(plan, seg.id, float(value)):
                return
            save_plan(plan)
            push_timeline(plan)
            # Keep the preview's playback cap in sync with the new duration.
            preview_widget.set_end(seg.duration_seconds)

        def on_seg_prompt_change(value) -> None:
            seg = current_segment()
            if seg is None or (value or '') == (seg.prompt or ''):
                return
            seg.prompt = value or ''
            plan = load_plan()
            save_plan(plan)
            timeline.set_segment_prompts({s.id: bool(s.prompt) for s in plan.segments})

        def on_history_select(path) -> None:
            """Preview an earlier take of the selected segment."""
            if not path or state['sel_id'] is None:
                return
            seg = current_segment()
            url = f'/api/projects/clips/{Path(path).name}'
            state['video_src'] = url
            preview_widget.show_clip(url, 'Showing an earlier render — re-select the segment for the latest.')
            if seg is not None:
                preview_widget.set_end(seg.duration_seconds)

        def on_resolution_change(value) -> None:
            """Persist the project's generation resolution (Project tab)."""
            if not is_open() or value not in RESOLUTIONS:
                return
            w, h = RESOLUTIONS[value]
            plan = load_plan()
            if (w, h) == (plan.width, plan.height):
                return
            plan.width, plan.height = w, h
            save_plan(plan)

        async def handle_frame_upload(which: str, e) -> None:
            if not is_open():
                ui.notify('Open a project folder before adding images.', type='warning')
                return
            seg = current_segment()
            if seg is None:
                ui.notify('Select a video segment first.', type='warning')
                return
            # NiceGUI 1.x/2.x: e.name + e.content. On 3.x use e.file.name /
            # `await e.file.read()` instead.
            stored = save_image(Path(e.name).name, e.content.read())
            filename = stored.name  # content-addressed; may differ from the upload's name
            if which == 'start':
                seg.start_image_path = filename
            else:
                seg.end_image_path = filename
                # The next segment starts where this one ends — if it has no
                # start frame yet, seed it with this image so they line up.
                nxt = neighbor(seg, +1)
                if nxt is not None and not nxt.start_image_path:
                    nxt.start_image_path = filename
                    ui.notify("Also set as the start frame of the next segment.", type='positive')
            segment_widget.reset_upload(which)
            save_plan(load_plan())
            show_selection()

        def remove_frame(which: str) -> None:
            """Clear this segment's start/end frame slot and its uploader. Only the
            reference is cleared — the image file stays on disk (it may be shared)."""
            seg = current_segment()
            if seg is None:
                return
            if which == 'start':
                seg.start_image_path = None
            else:
                seg.end_image_path = None
            segment_widget.reset_upload(which)
            save_plan(load_plan())
            show_selection()

        def copy_prev_end_frame() -> None:
            seg = current_segment()
            if seg is None:
                ui.notify('Select a video segment first.', type='warning')
                return
            prev = neighbor(seg, -1)
            if prev is None or not prev.end_image_path:
                ui.notify('No end frame to copy from the previous segment.', type='negative')
                return
            seg.start_image_path = prev.end_image_path
            save_plan(load_plan())
            show_selection()
            ui.notify("Copied the previous segment's end frame as this segment's start frame.", type='positive')

        def copy_start_to_end_frame() -> None:
            seg = current_segment()
            if seg is None:
                ui.notify('Select a video segment first.', type='warning')
                return
            if not seg.start_image_path:
                ui.notify('No start frame to copy from yet.', type='negative')
                return
            seg.end_image_path = seg.start_image_path
            save_plan(load_plan())
            show_selection()
            ui.notify("Copied this segment's start frame as its end frame.", type='positive')

        # ------------------------------------------------------------------
        # Timeline toolbar actions
        # ------------------------------------------------------------------
        def duplicate_selected_segment() -> None:
            """Insert a copy (prompt, frames, clip) right after the selected segment."""
            seg = current_segment()
            if seg is None:
                ui.notify('Select a video segment first.', type='warning')
                return
            plan = load_plan()
            copy = duplicate_segment(plan, seg.id)
            if copy is None:
                ui.notify('That segment no longer exists on the timeline.', type='negative')
                return
            save_plan(plan)
            push_timeline(plan)
            select(copy.id)
            ui.notify('Duplicated — the copy sits right after the original.', type='positive')

        def insert_segment() -> None:
            """Insert a fresh segment before the selected one, or at the end of
            the timeline when nothing is selected."""
            if not is_open():
                ui.notify('Open a project folder first.', type='warning')
                return
            seg = current_segment()
            plan = load_plan()
            new_seg = add_segment(plan, seg.id if seg else None, DEFAULT_DURATION)
            if new_seg is None:
                ui.notify('That segment no longer exists on the timeline.', type='negative')
                return
            save_plan(plan)
            push_timeline(plan)
            select(new_seg.id)
            if seg is not None:
                ui.notify(f'Inserted a {DEFAULT_DURATION:.0f}s segment before the selected one.', type='positive')
            else:
                ui.notify(f'Added a {DEFAULT_DURATION:.0f}s segment at the end.', type='positive')

        def delete_selected_segment() -> None:
            """Delete the selected segment; later segments move back to fill its time."""
            seg = current_segment()
            if seg is None:
                ui.notify('Select a video segment first.', type='warning')
                return
            # A queued/in-flight render holds a reference to this segment.
            if seg.status in ('queued', 'rendering') or seg.id in render_queue.pending():
                ui.notify('Wait for this segment to finish rendering first.', type='warning')
                return
            plan = load_plan()
            idx = remove_segment(plan, seg.id)
            if idx is None:
                ui.notify('That segment no longer exists on the timeline.', type='negative')
                return
            save_plan(plan)
            state['video_src'] = None
            push_timeline(plan)
            if plan.segments:
                # Select whatever now occupies the deleted slot (or the new last one).
                select(plan.segments[min(idx, len(plan.segments) - 1)].id)
            else:
                select(None)
            ui.notify('Deleted — later segments moved back to fill its time.', type='positive')

        # ------------------------------------------------------------------
        # Generate / concat / save
        # ------------------------------------------------------------------
        async def generate_selected_segment() -> None:
            seg_id = state['sel_id']
            if not seg_id:
                ui.notify('Select a video segment in the timeline first.', type='warning')
                return
            if not is_open():
                ui.notify('Open a project folder before generating.', type='warning')
                return

            plan = load_plan()
            seg = plan.get(seg_id)
            if seg is None:
                ui.notify('That segment is not in the plan.', type='negative')
                return
            if seg.status in ('queued', 'rendering'):
                ui.notify('This segment is already generating.', type='warning')
                return

            # Push onto the backend queue; a single worker thread drains it in
            # order. Status transitions are written on the shared in-memory plan,
            # so we just keep refreshing until this segment reaches a terminal state.
            seg.status = 'queued'
            seg.error = None
            save_plan(plan)
            refresh_status(seg_id)
            render_queue.enqueue(seg_id)
            refresh_queue_panel()

            while True:
                await asyncio.sleep(0.5)
                refresh_status(seg_id)
                seg = find_segment(seg_id)
                if seg is None or seg.status in ('done', 'error'):
                    break

            seg = find_segment(seg_id)
            if seg and seg.status == 'done':
                state['video_src'] = None  # force the preview to reload the new clip
                if state['sel_id'] == seg_id:
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
            plan = load_plan()
            if not plan.segments:
                ui.notify('Nothing to concat — add a segment first.', type='warning')
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
            save_plan(load_plan())
            ui.notify('Saved.', type='positive')

        # ------------------------------------------------------------------
        # Project open / restore
        # ------------------------------------------------------------------
        def load_project_into_ui() -> None:
            plan = load_plan()
            folder_label.text = str(active_folder())
            state['video_src'] = None
            # Reflect the stored resolution in the dropdown. If it isn't one of
            # the offered presets, snap to the default and normalize the plan.
            res_label = RES_BY_SIZE.get((plan.width, plan.height))
            if res_label is None:
                w, h = RESOLUTIONS[DEFAULT_RESOLUTION_LABEL]
                plan.width, plan.height = w, h
                save_plan(plan)
                res_label = DEFAULT_RESOLUTION_LABEL
            project_widget.set_resolution(res_label)
            push_timeline(plan)
            select(None)

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

        # Keep the Render Queue and Media tabs live (each widget only rebuilds
        # when its list actually changes, so this is cheap while idle).
        def refresh_live_panels() -> None:
            refresh_queue_panel()
            refresh_media_panel()

        ui.timer(0.5, refresh_live_panels)

        # Restore on page load / browser refresh if the server already has a
        # project open; otherwise just put the editor in its "nothing selected" state.
        if is_open():
            load_project_into_ui()
        else:
            show_selection()