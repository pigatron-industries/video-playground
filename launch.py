"""
Run with: python launch.py

Wires the backend API routes and the NiceGUI frontend page onto one
FastAPI/NiceGUI app. Starts as a normal local web server by default —
switch native=True below once you want the pywebview desktop-window
experience instead of a browser tab.
"""

from pathlib import Path

from nicegui import app as nicegui_app
from nicegui import ui

from backend.api import router as api_router
from frontend.app import build_page

nicegui_app.include_router(api_router, prefix='/api')

# Serves frontend/static/* at /static/* — without this, timeline.js and
# timeline.css 404 silently and the canvas never gets drawn into.
STATIC_DIR = Path(__file__).parent / 'frontend' / 'static'
nicegui_app.add_static_files('/static', str(STATIC_DIR))

build_page()

if __name__ in {'__main__', '__mp_main__'}:
    ui.run(
        title='Keyframe App',
        host='127.0.0.1',
        port=8080,
        native=False,  # flip to True for a native window (needs pywebview)
        reload=False,
    )
