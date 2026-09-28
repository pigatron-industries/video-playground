"""A server-side folder/file browser dialog.

The app server and the browser usually run on the same machine, and project
paths are absolute paths on that machine's filesystem. Browsers don't expose
real filesystem paths from their native folder pickers (for security reasons),
so instead this walks the server's own filesystem and returns the absolute
path the user picks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from nicegui import ui


def _resolve_start(path: str) -> Path:
    if path:
        candidate = Path(path).expanduser()
        if candidate.is_dir():
            return candidate
        if candidate.parent.is_dir():
            return candidate.parent
    return Path.home()


def _normalize_extensions(extensions: Sequence[str] | None) -> set[str] | None:
    if not extensions:
        return None
    return {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}


def _volumes() -> list[Path]:
    volumes = Path("/Volumes")
    try:
        entries = [e for e in volumes.iterdir() if e.is_dir()]
    except (PermissionError, OSError):
        return []
    return sorted(entries, key=lambda e: e.name.lower())


def _entries(path: Path, *, include_files: bool, extensions: set[str] | None) -> tuple[list[Path], list[Path]]:
    try:
        children = [e for e in path.iterdir() if not e.name.startswith(".")]
    except (PermissionError, OSError):
        return [], []
    dirs = sorted((e for e in children if e.is_dir()), key=lambda e: e.name.lower())
    if not include_files:
        return dirs, []
    files = [e for e in children if e.is_file() and (extensions is None or e.suffix.lower() in extensions)]
    return dirs, sorted(files, key=lambda e: e.name.lower())


async def pick_path(
    start_path: str = "",
    *,
    mode: Literal["folder", "file"] = "folder",
    extensions: Sequence[str] | None = None,
) -> str | None:
    """Open a dialog to browse the server's filesystem and pick a folder or file.

    ``extensions`` (e.g. ``[".py", "txt"]``) restricts which files are shown and
    is only used when ``mode="file"``.

    Returns the chosen absolute path, or ``None`` if the user cancels.
    """
    result: asyncio.Future[str | None] = asyncio.get_event_loop().create_future()
    current = {"path": _resolve_start(start_path)}
    normalized_extensions = _normalize_extensions(extensions)

    dialog = ui.dialog()
    with dialog, ui.card().classes("w-[480px]"):
        ui.label("Choose a folder" if mode == "folder" else "Choose a file").classes(
            "text-base font-medium"
        )
        volumes = _volumes()
        if volumes:
            with ui.row().classes("w-full gap-1 mt-1"):
                for volume in volumes:
                    with ui.column().classes("items-center gap-0 w-14"):
                        ui.button(icon="storage", on_click=lambda v=volume: navigate(v)).props(
                            "flat dense round size=sm"
                        ).tooltip(volume.name)
                        ui.label(volume.name).classes(
                            "text-[10px] text-gray-500 w-full text-center truncate"
                        ).tooltip(volume.name)
        path_label = ui.label().classes("text-xs text-gray-500 break-all")
        list_container = ui.column().classes("w-full max-h-80 overflow-y-auto gap-0 mt-1")

        def render() -> None:
            path_label.set_text(str(current["path"]))
            list_container.clear()
            with list_container:
                parent = current["path"].parent
                if parent != current["path"]:
                    ui.button("..", on_click=lambda: navigate(parent)).props(
                        "flat dense no-caps align=left"
                    ).classes("w-full justify-start")
                dirs, files = _entries(
                    current["path"], include_files=mode == "file", extensions=normalized_extensions
                )
                for entry in dirs:
                    ui.button(entry.name, icon="folder", on_click=lambda e=entry: navigate(e)).props(
                        "flat dense no-caps align=left"
                    ).classes("w-full justify-start")
                for entry in files:
                    ui.button(
                        entry.name, icon="description", on_click=lambda e=entry: finish(str(e))
                    ).props("flat dense no-caps align=left").classes("w-full justify-start")

        def navigate(path: Path) -> None:
            current["path"] = path
            render()

        render()

        def finish(value: str | None) -> None:
            if not result.done():
                result.set_result(value)
            dialog.close()

        # "New folder" affordance (folder mode only): type a name, create it
        # inside the directory currently being browsed, and select it at once.
        def do_create() -> None:
            name = (name_input.value or "").strip()
            if not name:
                ui.notify("Enter a folder name first.", type="warning")
                return
            new_dir = current["path"] / name
            new_dir.mkdir(parents=True, exist_ok=True)
            finish(str(new_dir))

        create_row = ui.row().classes("w-full items-center gap-2 mt-1")
        with create_row:
            name_input = ui.input("New folder name").classes("flex-1")
            ui.button("Create", on_click=do_create).props("dense")
        create_row.visible = False

        def show_create() -> None:
            create_row.visible = True
            name_input.value = ""
            if hasattr(name_input, "focus"):
                name_input.focus()

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            if mode == "folder":
                ui.button("New folder", on_click=show_create).props("flat")
            ui.button("Cancel", on_click=lambda: finish(None)).props("flat")
            if mode == "folder":
                ui.button(
                    "Select this folder", on_click=lambda: finish(str(current["path"]))
                ).props("flat")

    dialog.on("hide", lambda: finish(None))
    dialog.open()
    return await result


async def pick_folder(start_path: str = "") -> str | None:
    """Open a dialog to browse the server's filesystem and pick a folder."""
    return await pick_path(start_path, mode="folder")


async def pick_file(start_path: str = "", extensions: Sequence[str] | None = None) -> str | None:
    """Open a dialog to browse the server's filesystem and pick a file.

    ``extensions`` (e.g. ``[".py", "txt"]``) restricts which files are shown.
    """
    return await pick_path(start_path, mode="file", extensions=extensions)
