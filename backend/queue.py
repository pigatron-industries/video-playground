"""Render queue — segments are pushed here and drained in order by a single
background worker.

Both entry points (the NiceGUI "Render segment" button and the API render
endpoint) call ``enqueue`` instead of running a render inline. A lone worker
thread then pops IDs off the FIFO one at a time and runs each job via
``backend.render.run_render``, so renders happen sequentially and in request
order no matter where they were requested from.

The queue is a module-level singleton (see ``render_queue``), matching how the
rest of this single-user app keeps shared state: one active project, one plan
cache (storage._plan), one selection object (api._selection).
"""

import threading
from collections import deque

from backend.render import run_render


class RenderQueue:
    """A thread-safe FIFO queue of segment IDs awaiting rendering.

    Producers may call ``enqueue`` from any thread (UI event handler, API
    request handler) while the worker drains it in order. A single worker keeps
    renders sequential and predictable; add more workers later if parallelism
    is wanted."""

    def __init__(self) -> None:
        self._items: deque[str] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._worker: threading.Thread | None = None
        self._stopped = False

    # -- producer side ------------------------------------------------------
    def enqueue(self, segment_id: str) -> None:
        """Add a segment to the queue (no-op if it's already queued)."""
        with self._not_empty:
            if segment_id not in self._items:  # don't render the same segment twice
                self._items.append(segment_id)
            self._not_empty.notify()
        self.start()  # idempotent — make sure a worker is draining

    def pending(self) -> list[str]:
        """Snapshot of queued IDs (oldest first), for status display."""
        with self._lock:
            return list(self._items)

    # -- consumer side ------------------------------------------------------
    def start(self) -> None:
        """Start the background worker if it isn't running yet. Idempotent, so
        it's safe to call from startup and again on every enqueue."""
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stopped = False
            self._worker = threading.Thread(
                target=self._run, name="render-queue", daemon=True
            )
            self._worker.start()

    def _run(self) -> None:
        while True:
            with self._not_empty:
                while not self._items and not self._stopped:
                    self._not_empty.wait(timeout=1.0)
                if self._stopped and not self._items:
                    return
                segment_id = self._items.popleft()
            # Run outside the lock so producers/inspectors aren't blocked by a render.
            try:
                run_render(segment_id)
            except Exception as exc:  # noqa: BLE001 — one bad render mustn't stall the queue
                print(f"render queue: failed to render {segment_id}: {exc}")

    def stop(self) -> None:
        """Signal the worker to exit once it has drained any remaining items."""
        with self._not_empty:
            self._stopped = True
            self._not_empty.notify_all()


# Module-level singleton shared by the UI and API.
render_queue = RenderQueue()
