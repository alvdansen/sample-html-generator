"""The composition-drift metric — numerically identical to the validated reference.

Hypothesis being operationalized: on a seed-locked ladder (same prompt+seed
rendered at successive training checkpoints), a healthily-learning model keeps
SHIFTING composition between checkpoints; an overfitting model LOCKS
composition (snaps to memorized framings).

Metric (unchanged from ``mima_ltx2/plasticity_gate/drift_metric.py``):
  - Per clip: sample ~9 frames evenly, skipping the first/last 5 frames.
  - Crop letterbox bars first (black rows top/bottom, detected by row
    intensity on the mean sampled frame).
  - Composition signature per frame: grayscale -> bilinear downscale to
    32x18 -> zero-mean/unit-variance normalize. Kills texture, keeps
    layout/tone masses.
  - drift_vs_prev(step s): mean over frame index i of
    (1 - Pearson corr(sig_prev[i], sig_cur[i])) against the previous
    available checkpoint of the SAME cell. ~0 = identical composition,
    ~1+ = unrelated.
  - motion_baseline: same statistic computed WITHIN the clip at step s,
    offset by one sampled frame (sig[i] vs sig[i+1]) — calibrates how much
    "drift" mere motion produces. Reported raw (drift is NOT netted).

The heavy deps (numpy + OpenCV) are the optional ``[drift]`` extra — imported
lazily so ``grid build``/``watch``/``freeze`` never pay for them (same pattern
as the local ``uvicorn`` import in ``watch``).
"""
from __future__ import annotations

import sys

N_SAMPLES = 9          # frames sampled per clip
EDGE_SKIP = 5          # skip first/last N frames
SIG_W, SIG_H = 32, 18  # composition signature resolution
DARK_ROW_THRESH = 16.0  # mean row intensity below this = letterbox bar
MAX_BAR_FRAC = 0.30    # never trim more than this fraction per side

_cv2 = None
_np = None


class DriftDependencyError(RuntimeError):
    """Raised when the optional [drift] extra (numpy + OpenCV) is missing."""


def _lazy_import():
    """Import numpy + cv2 on first use; raise a friendly error if absent."""
    global _cv2, _np
    if _cv2 is None or _np is None:
        try:
            import cv2  # noqa: PLC0415 — deliberate lazy import (optional extra)
            import numpy as np  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover — depends on install
            raise DriftDependencyError(
                "grid drift needs the optional metric dependencies "
                "(numpy + opencv-python-headless). Install them with:\n"
                '  uv pip install -e ".[drift]"   # or: pip install -e ".[drift]"'
            ) from exc
        _cv2, _np = cv2, np
    return _cv2, _np


def crop_letterbox(mean_frame):
    """Return (top, bottom) row bounds after trimming dark letterbox bars."""
    row_means = mean_frame.mean(axis=1)
    h = len(row_means)
    max_bar = int(h * MAX_BAR_FRAC)
    top = 0
    while top < max_bar and row_means[top] < DARK_ROW_THRESH:
        top += 1
    bot = h
    while bot > h - max_bar and row_means[bot - 1] < DARK_ROW_THRESH:
        bot -= 1
    if bot - top < 20:  # degenerate (near-black clip): keep full frame
        return 0, h
    return top, bot


def corr(a, b):
    """Pearson correlation of two already-normalized signature vectors."""
    _, np = _lazy_import()
    if a is None and b is None:
        return 1.0  # both flat: identical composition
    if a is None or b is None:
        return 0.0
    return float(np.dot(a, b) / len(a))


def pairwise_drift(sigs_a, sigs_b):
    """Mean over paired frame index of (1 - Pearson corr)."""
    _, np = _lazy_import()
    vals = [1.0 - corr(a, b) for a, b in zip(sigs_a, sigs_b)]
    return float(np.mean(vals)) if vals else None


def signatures_for_clip(path):
    """Return (list of 9 normalized 32x18 signature vectors, motion_baseline)
    or (None, None) if the clip is unreadable."""
    cv2, np = _lazy_import()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None, None
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = None
    if n > 2 * EDGE_SKIP + N_SAMPLES:
        lo, hi = EDGE_SKIP, n - 1 - EDGE_SKIP
        wanted = sorted(set(np.linspace(lo, hi, N_SAMPLES).round().astype(int)))
        got = {}
        idx = 0
        while idx <= wanted[-1]:
            if not cap.grab():
                break
            if idx in wanted:
                ok, fr = cap.retrieve()
                if not ok:
                    break
                got[idx] = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
            idx += 1
        if len(got) == len(wanted):
            frames = [got[i] for i in wanted]
    if frames is None:
        # fallback: full sequential decode, then sample
        cap.release()
        cap = cv2.VideoCapture(str(path))
        allf = []
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            allf.append(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY))
        cap.release()
        if len(allf) < N_SAMPLES:
            return None, None
        lo = min(EDGE_SKIP, max(0, (len(allf) - N_SAMPLES) // 2))
        hi = len(allf) - 1 - lo
        idxs = sorted(set(np.linspace(lo, hi, N_SAMPLES).round().astype(int)))
        frames = [allf[i] for i in idxs]
    else:
        cap.release()

    mean_frame = np.mean(np.stack([f.astype(np.float32) for f in frames]), axis=0)
    top, bot = crop_letterbox(mean_frame)

    sigs = []
    for f in frames:
        small = cv2.resize(
            f[top:bot, :], (SIG_W, SIG_H), interpolation=cv2.INTER_LINEAR
        ).astype(np.float64).ravel()
        mu, sd = small.mean(), small.std()
        if sd < 1e-8:
            sigs.append(None)  # flat frame, correlation undefined
        else:
            sigs.append((small - mu) / sd)

    motion = pairwise_drift(sigs[:-1], sigs[1:])
    return sigs, motion


def process_ladder(label, clips):
    """Compute drift rows for one ladder. ``clips``: {(cell, step): path}.

    Returns a list of row dicts — ``ladder``, ``cell``, ``step``,
    ``drift_vs_prev`` (None on each cell's first checkpoint), and
    ``motion_baseline`` — exactly the reference CSV row shape.
    """
    cells = sorted({c for c, _ in clips})
    rows = []
    for cell in cells:
        steps = sorted(s for c, s in clips if c == cell)
        prev_sigs = None
        for step in steps:
            sigs, motion = signatures_for_clip(clips[(cell, step)])
            if sigs is None:
                print(f"  WARN unreadable clip: {clips[(cell, step)]}", file=sys.stderr)
                prev_sigs = None
                continue
            drift = pairwise_drift(prev_sigs, sigs) if prev_sigs is not None else None
            rows.append({"ladder": label, "cell": cell, "step": step,
                         "drift_vs_prev": drift, "motion_baseline": motion})
            prev_sigs = sigs
    return rows
