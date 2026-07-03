"""Folder watcher + wait-for-stable-size settle gate (RUN-03 / D-03 / T-4-04).

``watchfiles`` coalesces a burst of filesystem events but does **not** wait for
write completion: a training ``.mp4`` mid-encode keeps firing ``Change.modified``
while still incomplete. Admitting on the raw event would flash a truncated/0-byte
cell — exactly what D-03 forbids ("never flash a bad cell").

The settle gate closes that gap: after ``awatch`` hands back a batch, each pending
media path is polled until its size is ``>0`` and unchanged across the quiet
window before it is admitted. On Windows the encoder may hold an advisory lock so
``os.stat`` raises ``PermissionError`` / ``OSError`` mid-write — every such error
is treated as "not ready yet", never as an exception and never as a BROKEN cell.

Both ``_settle`` and ``watch_loop`` are pure async with no server dependency, so
they are unit-testable in isolation; the server (04-03) supplies the ``on_ready``
callback that re-scans → ``build_grid`` → ``diff`` → broadcasts.
"""
from __future__ import annotations

import asyncio
import os

from watchfiles import Change, awatch

from sample_grid.core.scan import MEDIA_EXTENSIONS


async def _settle(
    paths,
    *,
    settle_ms: int = 1000,
    poll_ms: int = 500,
    timeout_ms: "int | None" = None,
) -> set:
    """Return the subset of ``paths`` whose size is stable and non-zero.

    A path is *ready* only when ``os.stat(path).st_size`` is ``> 0`` and unchanged
    across ``max(1, settle_ms // poll_ms)`` consecutive polls (the quiet window).
    A still-growing file resets its stable count on every change and never becomes
    ready. Any ``OSError`` (Windows lock during write) also resets the count —
    "not ready yet", never surfaced as an exception or BROKEN.

    Polling continues while paths remain pending so a file that finishes writing
    after the first window is still caught (no further FS event would re-queue it).
    ``timeout_ms`` caps the total wait so a perpetually-growing (or locked) file
    can never hang the caller — such a path is simply left out of the ready set and
    will re-queue on the next ``awatch`` batch it triggers.
    """
    stable_needed = max(1, settle_ms // poll_ms)
    counts: dict = {}
    last: dict = {}
    ready: set = set()
    remaining = set(paths)
    waited = 0

    while remaining:
        await asyncio.sleep(poll_ms / 1000)
        waited += poll_ms
        for p in list(remaining):
            try:
                size = os.stat(p).st_size  # Windows: may raise while locked
            except OSError:
                counts[p] = 0  # not ready yet — never BROKEN
                continue
            if size > 0 and size == last.get(p):
                counts[p] = counts.get(p, 0) + 1
            else:
                counts[p] = 0
            last[p] = size
            if counts[p] >= stable_needed:
                ready.add(p)
                remaining.discard(p)
        if timeout_ms is not None and waited >= timeout_ms:
            break

    return ready


def _is_media_event(change: Change, path) -> bool:
    """A media add/modify event — deletions are ignored (MISSING on next re-scan)."""
    return change in (Change.added, Change.modified) and (
        os.path.splitext(path)[1].lower() in MEDIA_EXTENSIONS
    )


async def watch_loop(
    folder,
    on_ready,
    *,
    stop_event,
    settle_ms: int = 1000,
    poll_ms: int = 500,
) -> None:
    """Watch ``folder`` and fire ``on_ready`` when settled media files land.

    Coalesces filesystem events via ``awatch(debounce=500)``, keeps only
    ``added``/``modified`` events whose suffix is in ``MEDIA_EXTENSIONS`` (a
    removed file becomes MISSING on the next full re-scan, so ``deleted`` is
    dropped), passes them through the settle gate, and awaits ``on_ready()`` when
    one or more become ready. Never admits a file on the first event — the gate
    always runs first. ``stop_event`` (an ``anyio``/``threading`` style event
    accepted by ``awatch``) ends the loop on shutdown.
    """
    async for batch in awatch(folder, debounce=500, stop_event=stop_event):
        pending = {p for change, p in batch if _is_media_event(change, p)}
        if not pending:
            continue
        ready = await _settle(pending, settle_ms=settle_ms, poll_ms=poll_ms)
        if ready:
            await on_ready()  # re-scan → build_grid → diff → broadcast
