# Keyframe App — NiceGUI + canvas hybrid

## Run it
```bash
pip install -r requirements.txt
python launch.py
```
Opens at http://127.0.0.1:8080. Set `native=True` in `launch.py` once
you want it in a real desktop window (via pywebview) instead of a browser
tab — no Tauri needed for that, though you can still wrap it in Tauri
later if you want native packaging/installers beyond what pywebview gives
you.

## Layout
```
launch.py              entry point — mounts API + page, starts the server
backend/
  models.py             Segment / RenderPlan / SelectionState schemas
  storage.py             project persistence, image storage
  render.py               ffmpeg trim/speed + H3 stub (same logic as before)
  api.py                  all /api/* routes, incl. the selection-state bridge
frontend/
  app.py                   the NiceGUI page: header, canvas embed, sidebar form
  static/timeline.js        canvas drag/click logic, exposes window.* hooks
  static/timeline.css
```

## How the canvas <-> NiceGUI bridge works
The canvas is plain client-side JS — dragging never touches Python, so it
stays responsive. It only calls the backend on "settled" events (selection
changed, drag ended, keyframe added): `POST /api/ui/select`.

The NiceGUI sidebar polls `GET /api/ui/select` every 400ms (`ui.timer`) and
refreshes its form fields when the selected keyframe changes. When you edit
a field in the sidebar (prompt, time), NiceGUI pushes the change back into
the canvas via `ui.run_javascript('window.setKeyframePrompt(...)')`.

This polling approach was chosen over NiceGUI's internal event bridge
deliberately — it's simple, doesn't depend on NiceGUI-version-specific
APIs, and keeps the canvas genuinely framework-agnostic (you could swap
NiceGUI out later without touching timeline.js).

## Known gaps / TODOs (left visible rather than papered over)
- `render_current_segment()` in `frontend/app.py` doesn't yet resolve
  *which* segment corresponds to a selected keyframe — segments live
  between two keyframes, not on one. Needs a small lookup once you decide
  how segment IDs should be derived/stored.
- `_generate_with_h3` in `backend/render.py` is still a stub — needs real
  credentials and the current H3 request/response shape confirmed.
- No video/audio track (drag-in clips, trim handles, waveform) yet — this
  is still the keyframe-only version of the timeline.
- 400ms polling for selection state is simple but not instant — fine for
  editing, but if it ever feels laggy, that's the first thing to tune
  (interval) or replace (NiceGUI's own event bridge, if you want to trade
  the framework-independence for lower latency).
