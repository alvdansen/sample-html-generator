"""Render the drift report — ``drift.html`` with per-ladder SVG small multiples.

Follows the repo's render conventions: a Jinja2 template in
``sample_grid/render/templates`` (the wheel already force-includes that dir)
rendered with autoescape UNCONDITIONALLY ON (same rationale as
``render/renderer.py`` — ``.j2`` extension would leave the heuristic OFF).
All chart geometry is precomputed here in Python; the template only emits
structure, so no coordinate math lives in markup.

Chart design (dataviz method, dark mode selected — not flipped):
one small-multiple chart per cell; the drift curve is the single categorical
series (blue), the motion baseline is the de-emphasis gray reference, the
per-cell motion floor is a dashed reference line, and knee ranges are shaded
in the warning hue. Excluded (high-motion) cells render dimmed with a badge.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from sample_grid.drift.analyze import KNEE_MIN_RUN, LadderAnalysis

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "render" / "templates"

# Chart box (viewBox units).
_W, _H = 380, 172
_ML, _MR, _MT, _MB = 46, 14, 16, 30


def _nice_ceil(v: float) -> float:
    """Round up to a clean axis max (1/2/2.5/5 x 10^k)."""
    if v <= 0:
        return 1.0
    exp = math.floor(math.log10(v))
    for mult in (1.0, 2.0, 2.5, 5.0, 10.0):
        cand = mult * 10 ** exp
        if v <= cand + 1e-12:
            return cand
    return 10.0 ** (exp + 1)


def _fmt_step(step: int) -> str:
    return f"{step:,}"


def _cell_chart(cell) -> dict:
    """Precompute one cell's SVG geometry from its ``CellAnalysis``."""
    steps = cell.steps
    lo, hi = min(steps), max(steps)
    span = max(1, hi - lo)

    values = [d for d in cell.drifts if d is not None]
    values += [m for m in cell.motions if m is not None]
    values.append(cell.floor)
    ymax = _nice_ceil(max(values) * 1.05 if values else 1.0)

    def x(step):
        return _ML + (step - lo) / span * (_W - _ML - _MR)

    def y(v):
        return _MT + (1 - v / ymax) * (_H - _MT - _MB)

    drift_pts, markers = [], []
    for step, drift, motion in zip(cell.steps, cell.drifts, cell.motions):
        if drift is None:
            continue
        px, py = x(step), y(drift)
        drift_pts.append(f"{px:.1f},{py:.1f}")
        markers.append({
            "cx": round(px, 1), "cy": round(py, 1),
            "title": (f"step {_fmt_step(step)} — drift {drift:.3f}, "
                      f"motion {motion:.3f}" if motion is not None
                      else f"step {_fmt_step(step)} — drift {drift:.3f}"),
        })
    motion_pts = [
        f"{x(s):.1f},{y(m):.1f}"
        for s, m in zip(cell.steps, cell.motions) if m is not None
    ]

    knee_rects = [
        {"x": round(x(a), 1), "w": round(max(2.0, x(b) - x(a)), 1),
         "label": f"{_fmt_step(a)}–{_fmt_step(b)}"}
        for a, b in cell.knees
    ]

    n_yticks = 4
    yticks = [
        {"y": round(y(ymax * i / n_yticks), 1), "label": f"{ymax * i / n_yticks:g}"}
        for i in range(n_yticks + 1)
    ]
    xticks = [
        {"x": round(x(s), 1), "label": _fmt_step(s)}
        for s in ({lo, lo + (hi - lo) // 2, hi} if hi > lo else {lo})
    ]

    return {
        "w": _W, "h": _H, "ml": _ML, "mr": _MR, "mt": _MT, "mb": _MB,
        "plot_x": _ML, "plot_y": _MT,
        "plot_w": _W - _ML - _MR, "plot_h": _H - _MT - _MB,
        "drift_points": " ".join(drift_pts),
        "markers": markers,
        "motion_points": " ".join(motion_pts),
        "floor_y": round(y(cell.floor), 1),
        "knee_rects": knee_rects,
        "yticks": sorted(yticks, key=lambda t: t["y"]),
        "xticks": sorted(xticks, key=lambda t: t["x"]),
    }


def render_report(analyses: "list[LadderAnalysis]", scheme_by_ladder: dict) -> str:
    """Render the full drift.html from the per-ladder guardrail analyses."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,  # forced ON — .j2 defeats select_autoescape (T-1-01)
    )
    template = env.get_template("drift.html.j2")

    ladders = []
    for analysis in analyses:
        cells = []
        for cell in analysis.cells:
            cells.append({
                "name": cell.cell,
                "excluded": cell.excluded,
                "motion_median": cell.motion_median,
                "floor": cell.floor,
                "n_checkpoints": len(cell.steps),
                "knees": [
                    {"start": _fmt_step(a), "end": _fmt_step(b)} for a, b in cell.knees
                ],
                "chart": _cell_chart(cell),
            })
        ladders.append({
            "label": analysis.ladder,
            "scheme": scheme_by_ladder.get(analysis.ladder, "?"),
            "motion_cap": analysis.motion_cap,
            "floor_mult": analysis.floor_mult,
            "cells": cells,
            "n_excluded": len(analysis.excluded_cells),
        })

    return template.render(
        ladders=ladders,
        knee_min_run=KNEE_MIN_RUN,
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
