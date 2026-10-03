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
import secrets
import time
from pathlib import Path

from nicegui import run, ui

from backend.models import Keyframe, RenderPlan, Segment
from backend.queue import render_queue
from backend.render import concat_segments
from backend.storage import (
    active_folder,
    load_plan,
    save_image,
    save_plan,
    set_active_folder,
)
from frontend.keyframe_tab import KeyframeTab
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
    """Keyframe images are stored as bare filenames; strip the legacy URL prefix."""
    if not path:
        return None
    return path.removeprefix(LEGACY_IMAGE_PREFIX)


def _image_url(name: str | None) -> str | None:
    return f'{LEGACY_IMAGE_PREFIX}{name}' if name else None


def plan_to_keyframes(plan: RenderPlan) -> list[dict]:
    """RenderPlan -> the widget's keyframe format. Keyframes are bare time
    markers on the canvas; the prompt and start/end frame images live on the
    segments (see the segment editor), not on the keyframes."""
    return [
        {'id': k.id, 'time': k.time}
        for k in sorted(plan.keyframes, key=lambda k: k.time)
    ]


def merge_keyframes_into_plan(plan: RenderPlan, kfs: list[dict]) -> RenderPlan:
    """Regenerate keyframes + segments from the widget's keyframes, carrying
    over each segment's prompt, start/end frame images and render state
    (status, output, trim, speed, ...) for any segment whose id ("kfA-kfB")
    still exists. A brand-new segment starts where the previous one ends, so
    its start frame is seeded from the previous segment's end frame; and when
    an existing segment is split, the new head piece keeps its start point's
    frame and the new tail piece keeps its end point's frame — in all cases
    only filling a slot that is still empty. Keyframes are bare time markers
    — the prompt and frame images are segment properties, so they are
    preserved here, not re-derived from the keyframes."""
    kfs = sorted(kfs, key=lambda k: k['time'])
    old = {s.id: s for s in plan.segments}
    # Old segments keyed by their start / END keyframe (id is "{start}-{end}");
    # at most one old segment starts (ends) at a given keyframe. Lets a split
    # carry the old segment's frames over to the new pieces that keep the same
    # start / end points.
    old_by_start = {seg.id.split('-', 1)[0]: seg for seg in plan.segments}
    old_by_end = {seg.id.split('-', 1)[1]: seg for seg in plan.segments}
    segs = []
    for a, b in zip(kfs, kfs[1:]):
        sid = f"{a['id']}-{b['id']}"
        s = old.get(sid)
        if s is None:
            # New segment — seed its frame slots from whatever already
            # describes the same points so a split or an append keeps the
            # first/last frame instead of dropping it. Only fills a slot that
            # is still empty.
            s = Segment(id=sid, start_time=0, end_time=0, duration=0)
            # Start frame: prefer the old segment this one split off from
            # (it kept the same start point); otherwise, for a segment appended
            # at the end, use the previous segment's end frame.
            head = old_by_start.get(a['id'])
            if head is not None and head.start_image_path:
                s.start_image_path = head.start_image_path
            elif segs and segs[-1].end_image_path and not s.start_image_path:
                s.start_image_path = segs[-1].end_image_path
            # End frame: the old segment this one split off from (it kept the
            # same end point) keeps its last frame.
            tail = old_by_end.get(b['id'])
            if tail is not None and tail.end_image_path and not s.end_image_path:
                s.end_image_path = tail.end_image_path
        s.start_time, s.end_time = a['time'], b['time']
        s.duration = round(b['time'] - a['time'], 1)
        segs.append(s)
    plan.keyframes = [Keyframe(id=k['id'], time=k['time']) for k in kfs]
    plan.segments = segs
    return plan


def _new_keyframe_id() -> str:
    """A unique keyframe id. Must not contain '-' so that "kfA-kfB" segment
    ids stay parseable (same constraint as the widget's own id generation)."""
    return f"kf{int(time.time() * 1000):x}{secrets.token_hex(2)}"


def duplicate_segment_keyframes(kfs: list[dict], seg_id: str) -> tuple[list[dict], str] | None:
    """Insert a copy of segment ``seg_id`` ("a-b") immediately to its right.

    A new end keyframe lands one segment length after b, and every keyframe
    after b keeps its id but moves back by that same length to make room — so
    the original "a-b" is untouched, the copy becomes "b-<new>", and only the
    segment right after it ("b-c") gets a new id ("<new>-c"). Returns
    ``(new keyframes, new keyframe id)``, or None if ``seg_id`` is not an
    adjacent pair in ``kfs`` (e.g. a stale selection)."""
    a_id, _, b_id = seg_id.partition('-')
    kfs = sorted(kfs, key=lambda k: k['time'])
    ia = next((i for i, k in enumerate(kfs) if k['id'] == a_id), None)
    ib = next((i for i, k in enumerate(kfs) if k['id'] == b_id), None)
    if ia is None or ib != ia + 1:
        return None
    dur = round(kfs[ib]['time'] - kfs[ia]['time'], 1)
    new_id = _new_keyframe_id()
    new_kf = {'id': new_id, 'time': round(kfs[ib]['time'] + dur, 1)}
    shifted = [{'id': k['id'], 'time': round(k['time'] + dur, 1)} for k in kfs[ib + 1:]]
    return kfs[: ib + 1] + [new_kf] + shifted, new_id


def _copy_segment_content(dst: Segment, src: Segment) -> None:
    """Mirror every content field from ``src`` onto ``dst``. Times/duration are
    set by the keyframe merge; status/output/history are handled separately."""
    dst.prompt = src.prompt
    dst.start_image_path = src.start_image_path
    dst.end_image_path = src.end_image_path
    dst.source_clip_path = src.source_clip_path
    dst.trim_in = src.trim_in
    dst.trim_out = src.trim_out
    dst.speed_factor = src.speed_factor


def apply_segment_duplicate(
    plan: RenderPlan, seg_id: str
) -> tuple[list[dict], Segment, Segment | None] | None:
    """Duplicate segment ``seg_id`` in place and rebuild the plan around it.

    The copy mirrors the source (prompt, start/end frames, trim/speed, clip +
    earlier takes); the segment that used to follow the source keeps its full
    state but gets a new id ("b-c" -> "<new>-c"), so that is carried over too.
    Returns ``(keyframes, copy, shifted follower or None)`` — or None if
    ``seg_id`` isn't an adjacent keyframe pair in the plan."""
    kfs = [{'id': k.id, 'time': k.time} for k in sorted(plan.keyframes, key=lambda k: k.time)]
    result = duplicate_segment_keyframes(kfs, seg_id)
    if result is None:
        return None
    new_kfs, new_id = result

    src = next((s for s in plan.segments if s.id == seg_id), None)
    b_id = seg_id.split('-', 1)[1]
    nxt_old = next((s for s in plan.segments if s.id.startswith(f'{b_id}-')), None)
    if src is None:
        return None

    merge_keyframes_into_plan(plan, new_kfs)

    copy_seg = next(s for s in plan.segments if s.id == f'{b_id}-{new_id}')
    _copy_segment_content(copy_seg, src)
    if src.output_path:
        # The generated clip is content-addressed — the copy references the
        # same file and is immediately playable/previewable.
        copy_seg.output_path = src.output_path
        copy_seg.history = list(src.history or [])
        copy_seg.status = 'done'
    else:
        copy_seg.status = 'empty'
    copy_seg.error = None

    shifted = next((s for s in plan.segments if s.id.startswith(f'{new_id}-')), None)
    if nxt_old is not None and shifted is not None:
        _copy_segment_content(shifted, nxt_old)
        shifted.status = nxt_old.status
        shifted.output_path = nxt_old.output_path
        shifted.history = list(nxt_old.history or [])
        shifted.error = nxt_old.error

    return new_kfs, copy_seg, shifted


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

        # Per-page working state. `kfs` is the list of keyframe dicts the
        # widget shows ([{id, time}]); it is the working copy that gets merged
        # into the plan and persisted on every change. Segments own the prompt
        # and start/end frame images — edit those directly on the plan.
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
                # top-left: editor — three tabs. "Keyframe" edits the selected
                # keyframe (a bare time marker); "Segment" edits the selected
                # segment (the unit of work: prompt, start/end frames, render);
                # "Project" holds project-wide settings such as resolution. The
                # active tab follows whatever is currently selected in the timeline.
                with ui.column().classes('w-full no-wrap').style(
                    'flex: 1 1 0; min-width: 0; border-right: 1px solid #34343c; background:#232328'
                ):
                    with ui.tabs().classes('w-full') as left_tabs:
                        keyframe_tab = ui.tab('Keyframe')
                        segment_tab = ui.tab('Segment')
                        project_tab = ui.tab('Project')

                    # Shared hint shown above the editor panels when nothing is selected.
                    no_selection_label = ui.label(
                        'Click a keyframe or a video segment in the timeline.'
                    ).classes('text-caption text-grey w-full').style('padding: 8px 10px')

                    with ui.tab_panels(left_tabs, value=keyframe_tab).style(
                        'flex: 1 1 auto; min-height: 0'
                    ).classes('w-full'):
                        # ---- Keyframe tab --------------------------------
                        # A standalone widget (frontend/keyframe_tab.py) — edits the
                        # selected keyframe's time and deletes it. The prompt and frame
                        # images belong to the segment, not here.
                        with ui.tab_panel(keyframe_tab):
                            keyframe_widget = KeyframeTab(
                                on_time_change=lambda value: on_kf_time_change(value),
                                on_delete=lambda: delete_current(),
                            )

                        # ---- Segment tab ---------------------------------
                        # A standalone widget (frontend/segment_tab.py) — the unit of work:
                        # transition prompt, start/end frame images, render status + history.
                        with ui.tab_panel(segment_tab):
                            segment_widget = SegmentTab(
                                on_prompt_change=lambda value: on_seg_prompt_change(value),
                                on_frame_upload=lambda which, e: handle_frame_upload(which, e),
                                on_remove_frame=lambda which: remove_frame(which),
                                on_copy_prev_end=lambda: copy_prev_end_frame(),
                                on_copy_start_to_end=lambda: copy_start_to_end_frame(),
                                on_generate=lambda: generate_selected_segment(),
                                on_duplicate=lambda: duplicate_selected_segment(),
                                on_history_select=lambda path: on_history_select(path),
                            )

                        # ---- Project tab ---------------------------------
                        # A standalone widget (frontend/project_tab.py) — project-wide
                        # settings; the resolution presets themselves stay in app.py.
                        with ui.tab_panel(project_tab):
                            project_widget = ProjectTab(
                                options=list(RESOLUTIONS),
                                default_value=DEFAULT_RESOLUTION_LABEL,
                                on_resolution_change=lambda value: on_resolution_change(value),
                            )

                # top-right: tabbed panel — "Preview" shows the rendered clip for
                # the selected segment; "Render Queue" lists what's waiting to be
                # rendered (kept live by a short timer). Start/end frame thumbnails
                # live in the left editor, not here.
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
                        # ---- Preview tab ---------------------------------
                        # A standalone widget (frontend/preview_tab.py) — the rendered
                        # clip for the selected segment, or an earlier take.
                        with ui.tab_panel(preview_tab):
                            preview_widget = PreviewTab()

                        # ---- Render Queue tab ----------------------------
                        # A standalone widget (frontend/render_queue_tab.py) — lists what's
                        # waiting to render; kept live by a short timer in app.py below.
                        with ui.tab_panel(queue_tab):
                            queue_widget = RenderQueueTab()

                        # ---- Media tab -----------------------------------
                        # A standalone widget (frontend/media_tab.py) — every image and clip
                        # stored in the project, with a thumbnail, its type, and how many
                        # segments use it; kept live by the same short timer as the queue.
                        with ui.tab_panel(media_tab):
                            media_widget = MediaTab()

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
                'height: 250px; flex-shrink: 0; overflow: hidden; '
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

        def current_segment() -> Segment | None:
            return find_segment(state['sel_id']) if state['sel_kind'] == 'segment' else None

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
            # Segments are re-derived from the keyframes; refresh the canvas's
            # prompt indicators to match whichever segments still exist.
            timeline.set_segment_prompts({s.id: bool(s.prompt) for s in plan.segments})

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
                # Keyframe selected -> show the Keyframe tab, hide the Segment one.
                keyframe_tab.visible = True
                segment_tab.visible = False
                left_tabs.set_value(keyframe_tab)
                segment_widget.hide()
                keyframe_widget.set_keyframe(kf['time'])
                no_selection_label.visible = False
                # Keyframes carry no image; nothing to preview yet.
                preview_widget.clear('Select a video segment to preview its rendered clip here.')
                return

            if kind == 'segment' and id_ and '-' in id_:
                seg = find_segment(id_)
                if seg:
                    # Segment selected -> show the Segment tab, hide the Keyframe one.
                    keyframe_tab.visible = False
                    segment_tab.visible = True
                    left_tabs.set_value(segment_tab)
                    keyframe_widget.hide()
                    no_selection_label.visible = False

                    a_id, b_id = id_.split('-', 1)
                    a, b = find_kf(a_id), find_kf(b_id)
                    range_text = (
                        f"{a['time']:.1f}s → {b['time']:.1f}s · {b['time'] - a['time']:.1f}s"
                        if a and b else ''
                    )
                    prev_seg = previous_segment(seg)
                    # Offer earlier takes of this segment (newest first).
                    history_opts = {
                        p: ('Previous render' if i == 0 else f'Earlier render {i + 1}')
                        for i, p in enumerate(seg.history or [])
                    }
                    err = f' — {seg.error}' if seg.error else ''
                    segment_widget.set_segment({
                        'range_text': range_text,
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
                    # Mirror the status onto the canvas block too.
                    timeline.set_segment_status(id_, seg.status)

                    if seg.status == 'done':
                        # Clips are content-addressed (<sha256>.mp4); fall back
                        # to the legacy segment-id name for takes rendered
                        # before that scheme.
                        clip_name = (
                            Path(seg.output_path).name
                            if seg.output_path else f'{id_}.mp4'
                        )
                        src = f'/api/projects/clips/{clip_name}'
                        if state['video_src'] != src:
                            state['video_src'] = src
                            preview_widget.show_clip(src, 'Rendered clip — press play to watch.')
                    else:
                        state['video_src'] = None
                        preview_widget.clear('No rendered clip yet — render the segment to preview video here.')
                    return

            # Nothing (valid) selected — hide both editor tabs, fall back to the
            # Project tab (the only one left), and show the hint.
            state['sel_kind'] = state['sel_id'] = None
            keyframe_tab.visible = False
            segment_tab.visible = False
            left_tabs.set_value(project_tab)
            keyframe_widget.hide()
            segment_widget.hide()
            no_selection_label.visible = True
            preview_widget.clear('Click a keyframe or a video segment in the timeline.')

        def refresh_status(seg_id: str) -> None:
            """Mirror a segment's render status onto its canvas block and, if it's the
            selected segment, the sidebar label + Generate button state."""
            seg = find_segment(seg_id)
            status = seg.status if seg else 'empty'
            timeline.set_segment_status(seg_id, status)
            # Keep the Generate button in sync with render state (cheap; no-op when idle).
            segment_widget.update_generate_button(status in ('queued', 'rendering'))
            if state['sel_kind'] == 'segment' and state['sel_id'] == seg_id:
                err = f' — {seg.error}' if seg and seg.error else ''
                segment_widget.set_status_text(f'Status: {status}{err}')

        def refresh_queue_panel() -> None:
            """Rebuild the Render Queue tab from render_queue.pending(). The widget
            only touches the DOM when the list actually changed, so the short timer
            that keeps it live is cheap while idle."""
            items = []
            for seg_id in render_queue.pending():
                seg = find_segment(seg_id)
                status = seg.status if seg else 'queued'
                label = (
                    f'{seg_id}  ({seg.start_time:.1f}s – {seg.end_time:.1f}s)'
                    if seg is not None else seg_id
                )
                items.append((label, status))
            queue_widget.set_items(items)

        def _media_usage_counts(plan: RenderPlan) -> tuple[dict[str, int], dict[str, int]]:
            """How many distinct segments reference each image / clip file.

            A segment counts once per file even if it uses the same image as
            both its start and end frame; a clip referenced by a segment's
            current output or any of its earlier takes all count for that
            segment."""
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
            """Rebuild the Media tab from the project's images/ and clips/ folders.

            Files are listed straight off disk (content-addressed names), so
            anything saved by an upload or a finished render shows up; each file
            is annotated with how many segments reference it in the plan."""
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
        def on_kf_time_change(value) -> None:
            kf = current_kf()
            if kf is None or value is None:
                return
            t = round(min(total(), max(0.0, float(value))), 1)
            if t == kf['time']:
                return
            kf['time'] = t
            push_keyframes()

        def on_seg_prompt_change(value) -> None:
            seg = current_segment()
            if seg is None or (value or '') == (seg.prompt or ''):
                return
            seg.prompt = value or ''
            save_plan(load_plan())
            # Live-update the canvas indicator for the segment ending here.
            timeline.set_segment_prompts({s.id: bool(s.prompt) for s in load_plan().segments})

        def on_history_select(path) -> None:
            """Preview an earlier take of the selected segment."""
            if not path or state['sel_kind'] != 'segment':
                return
            url = f'/api/projects/clips/{Path(path).name}'
            state['video_src'] = url
            preview_widget.show_clip(url, 'Showing an earlier render — re-select the segment for the latest.')

        def on_total_change(e) -> None:
            if not e.value:
                return
            timeline.set_total_duration(e.value)
            commit()

        def on_resolution_change(value) -> None:
            """Persist the project's generation resolution (Project tab). The
            guard skips the save when nothing actually changed, so loading a
            project can't trigger a redundant write."""
            if not is_open() or value not in RESOLUTIONS:
                return
            w, h = RESOLUTIONS[value]
            plan = load_plan()
            if (w, h) == (plan.width, plan.height):
                return
            plan.width, plan.height = w, h
            save_plan(plan)

        # The tab widgets wire their own inputs; only the timeline toolbar's total
        # length field lives directly in app.py.
        total_duration.on_value_change(on_total_change)

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
            # Content-addressed storage: identical uploads resolve to the same
            # file, so use the stored name (which may differ from the upload's).
            filename = stored.name
            if which == 'start':
                seg.start_image_path = filename
            else:
                seg.end_image_path = filename
                # The next segment starts where this one ends — if it has no
                # start frame of its own yet, seed it with this image so the
                # two segments line up.
                end_kf = seg.id.split('-', 1)[1]
                nxt = next(
                    (s for s in load_plan().segments if s.id.startswith(f'{end_kf}-')),
                    None,
                )
                if nxt is not None and not nxt.start_image_path:
                    nxt.start_image_path = filename
                    ui.notify(
                        f"Also set as the start frame of the next segment ({nxt.id}).",
                        type='positive',
                    )
            segment_widget.reset_upload(which)
            save_plan(load_plan())
            show_selection()

        def remove_frame(which: str) -> None:
            """Clear this segment's start/end frame slot and its uploader.

            Only the reference is cleared — the underlying image file stays on
            disk (content-addressed, possibly shared by other segments)."""
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

        def previous_segment(seg: Segment) -> Segment | None:
            """The segment ending where ``seg`` starts — its predecessor in time."""
            if not is_open():
                return None
            start_kf = seg.id.split('-', 1)[0]
            for s in load_plan().segments:
                # Segment ids are "{start}-{end}"; keyframe ids never contain '-'.
                if s.id != seg.id and s.id.rsplit('-', 1)[-1] == start_kf:
                    return s
            return None

        def copy_prev_end_frame() -> None:
            """Copy the previous segment's end frame into this segment's start slot."""
            seg = current_segment()
            if seg is None:
                ui.notify('Select a video segment first.', type='warning')
                return
            prev = previous_segment(seg)
            if prev is None or not prev.end_image_path:
                ui.notify('No end frame to copy from the previous segment.', type='negative')
                return
            seg.start_image_path = prev.end_image_path
            save_plan(load_plan())
            show_selection()
            ui.notify(
                f"Copied {prev.id}'s end frame as this segment's start frame.",
                type='positive',
            )

        def copy_start_to_end_frame() -> None:
            """Copy this segment's own start frame into its end slot."""
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

        def duplicate_selected_segment() -> None:
            """Duplicate the selected segment in place: a copy with its prompt,
            start/end frames and rendered clip is inserted right after it, and
            every later keyframe moves back by one segment length to make room."""
            seg = current_segment()
            if seg is None:
                ui.notify('Select a video segment first.', type='warning')
                return

            plan = load_plan()
            b_id = seg.id.split('-', 1)[1]
            nxt_old = next((s for s in plan.segments if s.id.startswith(f'{b_id}-')), None)
            # A queued/in-flight render is keyed by segment id, and the follower's
            # id is about to change — duplicating now would orphan that job.
            if nxt_old is not None and (
                nxt_old.status in ('queued', 'rendering') or nxt_old.id in render_queue.pending()
            ):
                ui.notify(f"Wait for {nxt_old.id} to finish rendering first.", type='warning')
                return

            result = apply_segment_duplicate(plan, seg.id)
            if result is None:
                ui.notify('That segment no longer exists on the timeline.', type='negative')
                return
            new_kfs, copy_seg, _shifted = result

            # The shifted tail may now run past the timeline's end — extend it
            # rather than leaving keyframes outside the drawable range.
            if new_kfs[-1]['time'] > total():
                total_duration.value = round(new_kfs[-1]['time'], 1)
            state['kfs'] = new_kfs
            plan.total_duration = total()
            save_plan(plan)

            # Sync the canvas: fresh keyframe layout plus status/prompt maps for
            # the rebuilt segment set (the follower's old id is gone).
            timeline.set_keyframes(new_kfs)
            timeline.set_segment_statuses({s.id: s.status for s in plan.segments})
            timeline.set_segment_prompts({s.id: bool(s.prompt) for s in plan.segments})

            # Select the copy so its prompt, frames and clip show up in the sidebar.
            select('segment', copy_seg.id)
            ui.notify(
                f'Duplicated {seg.id} — the copy sits right after it; later keyframes moved back.',
                type='positive',
            )

        def delete_current() -> None:
            kf = current_kf()
            if kf is None:
                return
            state['kfs'] = [k for k in state['kfs'] if k['id'] != kf['id']]
            timeline.set_keyframes(state['kfs'])
            select(None, None)
            commit()

        # ------------------------------------------------------------------
        # Generate / concat / save
        # ------------------------------------------------------------------
        async def generate_selected_segment() -> None:
            seg_id = state['sel_id'] if state['sel_kind'] == 'segment' else None
            if not seg_id:
                ui.notify('Select a video segment in the timeline first.', type='warning')
                return
            if not is_open():
                ui.notify('Open a project folder before generating.', type='warning')
                return

            commit()  # make sure the segment exists in the plan
            plan = load_plan()
            seg = next((s for s in plan.segments if s.id == seg_id), None)
            if seg is None:
                ui.notify('That segment is not in the plan.', type='negative')
                return
            if seg.status in ('queued', 'rendering'):
                ui.notify('This segment is already generating.', type='warning')
                return

            # Push onto the backend generate queue; a single worker thread drains
            # it in order and runs each job via run_generate. Status transitions
            # (queued -> generating -> done/error) are written on the shared
            # in-memory plan, so we just keep refreshing until this segment
            # reaches a terminal state.
            seg.status = 'queued'
            seg.error = None
            save_plan(plan)
            refresh_status(seg_id)
            render_queue.enqueue(seg_id)
            refresh_queue_panel()  # reflect the new item in the Render Queue tab now

            while True:
                await asyncio.sleep(0.5)
                refresh_status(seg_id)
                seg = find_segment(seg_id)
                if seg is None or seg.status in ('done', 'error'):
                    break

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
            # Reflect the stored resolution in the dropdown. If it isn't one of
            # the offered presets (e.g. an old custom size), snap to the default
            # preset and normalize the plan so UI and generation stay consistent.
            res_label = RES_BY_SIZE.get((plan.width, plan.height))
            if res_label is None:
                w, h = RESOLUTIONS[DEFAULT_RESOLUTION_LABEL]
                plan.width, plan.height = w, h
                save_plan(plan)
                res_label = DEFAULT_RESOLUTION_LABEL
            project_widget.set_resolution(res_label)
            timeline.set_total_duration(plan.total_duration)
            timeline.set_keyframes(state['kfs'])
            timeline.set_segment_statuses({s.id: s.status for s in plan.segments})
            timeline.set_segment_prompts({s.id: bool(s.prompt) for s in plan.segments})
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

        # Keep the Render Queue and Media tabs live: refresh on a short interval
        # so they reflect items enqueued from either the UI or the API, and new
        # files saved by uploads or finished renders (cheap — each widget only
        # rebuilds when its list actually changes).
        def refresh_live_panels() -> None:
            refresh_queue_panel()
            refresh_media_panel()

        ui.timer(0.5, refresh_live_panels)

        # Restore on page load / browser refresh if the server already has a
        # project open (replaces the old first-poll restore).
        if is_open():
            load_project_into_ui()