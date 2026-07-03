"""Wait-for-stable-size settle gate (RUN-03 / D-03 / T-4-04).

The watcher must admit a media file only once its size is >0 and has stopped
changing across the quiet window, and must treat a Windows advisory-lock
``OSError`` on a pending file as "not ready yet" — never as an exception and
never as a BROKEN cell.

These tests are Wave-0 RED: ``sample_grid.live.watcher`` does not exist yet, so
the ``_settle`` import fails until Task 3 implements it. They drive the settle
coroutine directly with ``asyncio.run`` and a short poll window (no live watcher,
no real ``awatch``), per the validation strategy.
"""
from __future__ import annotations

import asyncio
import itertools
import os

# RED until Task 3 creates sample_grid/live/watcher.py.
from sample_grid.live.watcher import _settle


def test_settle_gate_admits_only_stable(growing_file, monkeypatch):
    """A stabilised file is admitted; a still-growing file is withheld."""
    # A file written once and left alone reports a stable, non-zero size.
    stable = growing_file("stable.mp4", initial_chunks=2)
    admitted = asyncio.run(
        _settle({str(stable.path)}, settle_ms=30, poll_ms=10, timeout_ms=2000)
    )
    assert str(stable.path) in admitted

    # A file whose size keeps climbing every poll never stabilises within the
    # window. Simulate the encoder still flushing by reporting an ever-growing
    # st_size for this path (real os.stat for everything else).
    growing_path = str(growing_file("growing.mp4", initial_chunks=1).path)
    sizes = itertools.count(2048, 2048)
    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if str(path) == growing_path:
            class _Stat:
                st_size = next(sizes)

            return _Stat()
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", fake_stat)
    withheld = asyncio.run(
        _settle({growing_path}, settle_ms=30, poll_ms=10, timeout_ms=120)
    )
    assert growing_path not in withheld


def test_settle_gate_tolerates_locked_file(monkeypatch):
    """A stat OSError (Windows lock) is treated as not-ready, never raised."""
    locked_path = "Z:/encoder/locked_clip.mp4"

    def raise_locked(path, *args, **kwargs):
        raise PermissionError("file is held by the encoder")

    monkeypatch.setattr(os, "stat", raise_locked)

    # Must return cleanly (no PermissionError propagated) and never admit the
    # locked file — a stat error is "not ready yet", not BROKEN.
    result = asyncio.run(
        _settle({locked_path}, settle_ms=30, poll_ms=10, timeout_ms=120)
    )
    assert locked_path not in result
