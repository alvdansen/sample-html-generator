"""The load-bearing seam: ``render(GridModel, AssetResolver) -> HTML``.

This function is PURE. It reads no sample bytes, never touches the disk for media,
and never branches on mode — it only consumes a ``GridModel`` and calls
``resolver.url(sample)``. That purity is what guarantees the P4 (Served) / P5
(Inline) resolver swap with zero renderer change.

The ``live`` flag is always ``False`` in P1 (no live-reload / no server). Keeping
it in the signature means P4 flips it to ``True`` with no signature change.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from sample_grid.core.model import CellState, GridModel
from sample_grid.render.resolver import AssetResolver

_MODULE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _MODULE_DIR / "templates"
_CLIENT_DIR = _MODULE_DIR / "client"


def _aspect_ratio_css(cell_ar) -> str:
    """Render the detected universal AR as a CSS ``aspect-ratio`` value."""
    if isinstance(cell_ar, tuple):
        return f"{cell_ar[0]} / {cell_ar[1]}"
    return str(cell_ar)


def render(
    grid: GridModel,
    resolver: AssetResolver,
    *,
    live: bool = False,
    cell_size_px: int = 240,
) -> str:
    """Render a GridModel to a self-contained HTML string (autoescape ON)."""
    # autoescape=True UNCONDITIONALLY. select_autoescape() keys off the file
    # extension and our template is `grid.html.j2` (ends in `.j2`, not `.html`),
    # so the extension heuristic would leave escaping OFF — a stored-XSS hole for
    # prompt text / filenames (T-1-01). This template only ever emits HTML, so
    # force escaping on. Verified by tests/test_render.py::test_prompt_html_escaped.
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )
    template = env.get_template("grid.html.j2")

    # Inline the CSS and JS so the artifact is self-contained from file:// (no
    # fetch). grid.js is the ONLY client JS in P1: a vanilla theme/density toggle
    # + sticky-shadow cue, deliberately free of any live-reload / server wiring
    # (live is always False here; P4 adds the served path with no signature change).
    # fonts.css carries the embedded (base64) webfaces; prepend it so the
    # @font-face rules are defined before grid.css references them via --font-*.
    # Both are static, trusted assets inlined into the single-file artifact — no
    # network fetch, preserving the file:// offline guarantee.
    fonts_css = (_CLIENT_DIR / "fonts.css").read_text(encoding="utf-8")
    css = fonts_css + "\n" + (_CLIENT_DIR / "grid.css").read_text(encoding="utf-8")
    js = (_CLIENT_DIR / "grid.js").read_text(encoding="utf-8")

    # Prezip the dense lattice into (step, [(prompt, cell), ...]) rows so the
    # template stays free of index gymnastics.
    rows = []
    for r_index, step in enumerate(grid.row_values):
        row_cells = []
        for c_index, cell in enumerate(grid.cells[r_index]):
            row_cells.append({"prompt": grid.col_values[c_index], "cell": cell})
        rows.append({"step": step, "cells": row_cells})

    return template.render(
        grid=grid,
        rows=rows,
        prompts=grid.col_values,
        url=resolver.url,
        live=live,
        # D-09: seed-variance state is classified in Python (build_grid), never
        # inferred in JS. The prezipped `cell` already carries has_alternates /
        # alternate_seeds for the per-cell badge.
        seed_varies=grid.seed_varies,
        cell_size_px=cell_size_px,
        css=css,
        js=js,
        cell_ar_css=_aspect_ratio_css(grid.cell_ar),
        n_cols=len(grid.col_values),
        POPULATED=CellState.POPULATED,
        MISSING=CellState.MISSING,
        BROKEN=CellState.BROKEN,
    )
