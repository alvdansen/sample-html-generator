"""Guardrail analysis over drift rows — the rules the validation run produced.

Pure Python (no numpy) so the guardrails are unit-testable without the optional
``[drift]`` extra installed. Three validated guardrails, ON BY DEFAULT:

1. **High-motion cell exclusion.** A cell whose MEDIAN motion_baseline exceeds
   ``motion_cap`` (default 0.30) carries too much intrinsic motion for the
   drift signal to separate from it (the validation's "medium cell" case).
   It stays in the CSV but is excluded from floor/knee analysis, with a
   printed warning.
2. **Knee detection.** Per included cell, the motion floor is
   ``median(motion_baseline) * floor_mult``; a knee is a run of >= 3
   CONSECUTIVE checkpoints whose drift_vs_prev sits below that floor —
   composition change indistinguishable from mere motion, i.e. composition
   lock. Reported as step ranges.
3. **Comparability warning.** Drift levels are only comparable WITHIN a
   ladder — checkpoint spacing scales per-step drift — so cross-ladder
   comparisons are flagged, never silently implied.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

# Guardrail defaults from the 2026-07-12 validation (mima2 T1 chain + prime).
DEFAULT_MOTION_CAP = 0.30
DEFAULT_FLOOR_MULT = 1.5
KNEE_MIN_RUN = 3  # >= 3 CONSECUTIVE below-floor checkpoints = a knee


@dataclass
class CellAnalysis:
    """Per-cell guardrail verdicts for one ladder cell."""

    cell: str
    steps: list = field(default_factory=list)           # all checkpoint steps
    drifts: list = field(default_factory=list)          # drift_vs_prev (None on first)
    motions: list = field(default_factory=list)         # motion_baseline per step
    motion_median: float = 0.0
    excluded: bool = False                              # guardrail 1
    floor: float = 0.0                                  # median motion * floor_mult
    knees: list = field(default_factory=list)           # [(start_step, end_step), ...]


@dataclass
class LadderAnalysis:
    """One ladder's cells + the parameters the verdicts were computed under."""

    ladder: str
    cells: "list[CellAnalysis]" = field(default_factory=list)
    motion_cap: float = DEFAULT_MOTION_CAP
    floor_mult: float = DEFAULT_FLOOR_MULT

    @property
    def excluded_cells(self) -> "list[CellAnalysis]":
        return [c for c in self.cells if c.excluded]


def _knee_runs(steps, drifts, floor) -> "list[tuple[int, int]]":
    """Step ranges of >= KNEE_MIN_RUN consecutive below-floor drift checkpoints.

    ``drifts[i]`` is the drift at ``steps[i]`` (None on a cell's first
    checkpoint — never part of a run). Consecutive means adjacent CHECKPOINTS
    in ladder order, not adjacent integers.
    """
    runs: list[tuple[int, int]] = []
    run_start = None
    prev_step = None
    for step, drift in zip(steps, drifts):
        below = drift is not None and drift < floor
        if below and run_start is None:
            run_start = step
        elif not below and run_start is not None:
            runs.append((run_start, prev_step))
            run_start = None
        prev_step = step
    if run_start is not None:
        runs.append((run_start, prev_step))

    def run_len(r):
        lo, hi = r
        return sum(1 for s in steps if lo <= s <= hi)

    return [r for r in runs if run_len(r) >= KNEE_MIN_RUN]


def analyze_ladder(
    ladder: str,
    rows: "list[dict]",
    *,
    motion_cap: float = DEFAULT_MOTION_CAP,
    floor_mult: float = DEFAULT_FLOOR_MULT,
) -> LadderAnalysis:
    """Apply the guardrails to one ladder's metric rows (``process_ladder`` shape)."""
    result = LadderAnalysis(ladder=ladder, motion_cap=motion_cap, floor_mult=floor_mult)
    for cell_name in sorted({r["cell"] for r in rows}):
        cell_rows = sorted(
            (r for r in rows if r["cell"] == cell_name), key=lambda r: r["step"]
        )
        cell = CellAnalysis(
            cell=cell_name,
            steps=[r["step"] for r in cell_rows],
            drifts=[r["drift_vs_prev"] for r in cell_rows],
            motions=[r["motion_baseline"] for r in cell_rows],
        )
        motions = [m for m in cell.motions if m is not None]
        cell.motion_median = median(motions) if motions else 0.0
        cell.excluded = cell.motion_median > motion_cap  # guardrail 1
        cell.floor = cell.motion_median * floor_mult
        if not cell.excluded:
            cell.knees = _knee_runs(cell.steps, cell.drifts, cell.floor)  # guardrail 2
        result.cells.append(cell)
    return result
