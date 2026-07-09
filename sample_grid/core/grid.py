"""Fixed-lattice grid construction (GRID-05 / Pitfall 7) + universal-AR detect.

``build_grid`` derives the full axis domains up front, then pins a ``Cell`` for
*every* (row, col) coordinate via the cartesian product — the lattice never
collapses or shifts, even when samples are absent. A coordinate with no sample
is ``MISSING``; a present-but-undecodable file is ``BROKEN`` (Pillow
``verify()``, D-10); otherwise ``POPULATED`` with a per-cell ``ar_mismatch``
flag set when its aspect ratio differs from the detected universal AR (D-11).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from math import gcd
from pathlib import Path

from PIL import Image

from sample_grid.core.model import (
    Cell,
    CellState,
    GridConfig,
    GridModel,
    SampleIndex,
)


_DIGITS = re.compile(r"(\d+)")


def natural_key(value):
    """Numeric-aware ("natural") sort key (GRID-06 / D-11).

    ``step_200`` < ``step_1000`` < ``step_30000`` even when unpadded: embedded
    digit runs compare as integers, not lexically. Pure-int values sort ahead of
    any non-numeric label (numeric-first tier preserved from the old ``_as_number``
    stub), and the same key orders a numeric seed axis correctly too.
    """
    s = str(value)
    # A whole-value pure number gets the strongest (numeric-first) ordering tier.
    # NOTE: the tier flag must NOT carry the full string alongside it — doing so
    # (as an earlier sketch did) makes the outer tuple compare lexically and
    # defeats the natural key ("step_1000" would sort before "step_200"). The
    # natural `key` tuple alone must break ties within the label tier.
    try:
        return (0, int(s))
    except (TypeError, ValueError):
        pass
    parts = _DIGITS.split(s)  # e.g. "step_200" -> ["step_", "200", ""]
    key = tuple(
        (1, p.lower()) if i % 2 == 0 else (0, int(p))
        for i, p in enumerate(parts)
        if p != ""
    )
    return (1, key)


def _image_dims(path: Path) -> "tuple[int, int] | None":
    """(w, h) of a still image via Pillow, or None if it isn't a readable image."""
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def _read_head_tail(path: Path, n: int = 4_000_000) -> bytes:
    """First + last ``n`` bytes of a file — the mp4 ``moov`` / WebM ``Tracks`` box
    lives near one end (front for faststart, back otherwise), so this bounds the
    read for large videos instead of loading the whole file."""
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(n)
        if size <= n:
            return head
        f.seek(size - n)
        return head + f.read(n)


_MP4_EXTS = {".mp4", ".m4v", ".mov"}
_WEBM_EXTS = {".webm", ".mkv"}


def _mp4_dims(buf: bytes) -> "tuple[int, int] | None":
    """Display (w, h) from an mp4/mov ``tkhd`` box (16.16 fixed) — stdlib, no ffmpeg.

    A file carries one ``tkhd`` per track; audio tracks store 0x0, so the first
    ``tkhd`` with non-zero, in-range display dimensions is the video track.
    """
    off = 0
    while True:
        i = buf.find(b"tkhd", off)
        if i < 0:
            return None
        s = i + 4
        ver = buf[s] if s < len(buf) else 0
        blen = 92 if ver == 1 else 84
        body = buf[s : s + blen]
        if len(body) >= 84:
            w = int.from_bytes(body[-8:-4], "big") >> 16
            h = int.from_bytes(body[-4:], "big") >> 16
            if 0 < w <= 16384 and 0 < h <= 16384:
                return (w, h)
        off = i + 4


def _webm_dims(buf: bytes) -> "tuple[int, int] | None":
    """PixelWidth (0xB0) / PixelHeight (0xBA) from a WebM/Matroska header — no ffmpeg.

    Best-effort: those ID bytes can also occur inside arbitrary payload, so each
    candidate is validated as a short EBML element (1-byte data-size vint, value in
    1..16384). If either dimension can't be read plausibly, returns None (the caller
    falls back to the square default rather than trusting a garbage match).
    """

    def _val(tag: bytes) -> "int | None":
        off = 0
        while True:
            i = buf.find(tag, off)
            if i < 0:
                return None
            s = buf[i + 1] if i + 1 < len(buf) else 0
            if s & 0x80:  # single-byte data-size vint
                ln = s & 0x7F
                if 1 <= ln <= 2 and i + 2 + ln <= len(buf):
                    v = int.from_bytes(buf[i + 2 : i + 2 + ln], "big")
                    if 0 < v <= 16384:
                        return v
            off = i + 1

    w = _val(b"\xb0")
    h = _val(b"\xba")
    return (w, h) if w and h else None


def _video_dims(path: Path) -> "tuple[int, int] | None":
    """(w, h) of a video from its container header (mp4/mov ``tkhd`` or WebM pixel
    elements), or None if the format is unsupported / dimensions unreadable."""
    ext = path.suffix.lower()
    if ext not in _MP4_EXTS and ext not in _WEBM_EXTS:
        return None
    try:
        buf = _read_head_tail(path)
    except Exception:
        return None
    return _mp4_dims(buf) if ext in _MP4_EXTS else _webm_dims(buf)


def _ar_of(path: Path) -> "tuple[int, int] | None":
    """Reduced (w, h) aspect ratio of an image OR video, or None if unreadable.

    Images are measured with Pillow; videos have their display dimensions parsed
    from the container header with stdlib only (no ffmpeg). This lets D-11
    universal-AR detection cover all-video grids, which previously fell back to a
    square (1, 1) and cropped widescreen clips.
    """
    dims = _image_dims(path) or _video_dims(path)
    if dims is None:
        return None
    w, h = dims
    if w <= 0 or h <= 0:
        return None
    g = gcd(w, h) or 1
    return (w // g, h // g)


def detect_universal_ar(index: SampleIndex) -> "tuple[int, int]":
    """Dominant aspect ratio across the index (D-11); (1, 1) when none readable."""
    ars = Counter(a for s in index if (a := _ar_of(s.path)))
    return ars.most_common(1)[0][0] if ars else (1, 1)


def is_decodable(path: Path) -> bool:
    """True if Pillow can decode the file; False (no raise) if it can't (D-10).

    Opens a *fresh* image inside the try and calls ``verify()`` — which validates
    the file structure without loading full pixel data. Per Pillow's contract the
    image object must not be reused after ``verify()``, so it is opened anew each
    call and never returned. Any exception (missing file, truncated/corrupt bytes,
    unidentified format) → ``False`` → the coordinate classifies as ``BROKEN``.
    """
    try:
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        return False


def build_grid(index: SampleIndex, config: GridConfig) -> GridModel:
    """Build a dense Steps x Prompts lattice from a SampleIndex (Pattern 2)."""
    # Group every sample by its (row, col) coordinate, then deterministically pick
    # ONE winner per coordinate — the lowest numeric seed, falling back to the
    # posix-first Sample.id when no seed is parseable. This replaces the old
    # last-write-wins dict comprehension, whose winner depended on iteration order
    # (a cherry-picking hazard — the worst failure for a seed-locked eval, D-10).
    groups: "dict[tuple, list]" = defaultdict(list)
    for s in index:  # index is posix-sorted by the scanner → s.id order is stable
        groups[(s.dims[config.rows], s.dims[config.cols])].append(s)

    def _seed_key(s):
        try:
            return (0, int(s.dims["seed"]))  # lowest numeric seed wins
        except (KeyError, TypeError, ValueError):
            return (1, 0)                     # no/unparseable seed → posix order

    by_coord: dict = {}
    alternates: dict = {}
    for coord, samples in groups.items():
        by_coord[coord] = min(samples, key=lambda s: (_seed_key(s), s.id))
        # WR-04: only mark alternates when the coordinate genuinely holds MORE
        # THAN ONE DISTINCT non-None seed. A no-seed or same-seed duplicate must
        # NOT fire the badge (the old unconditional list produced false
        # [None, None] / [7, 7] alarms). The winner's own seed stays in the set
        # (test_duplicate_lowest_seed pins {42, 7}).
        distinct = sorted({s.dims.get("seed") for s in samples if s.dims.get("seed") is not None})
        if len(distinct) > 1:
            alternates[coord] = distinct

    # (a) Per-coordinate variance — any coordinate holding >1 DISTINCT non-None
    # seed. Classified in Python (never inferred in JS), independent of render.
    per_coord_varies = any(
        len({s.dims.get("seed") for s in samples if s.dims.get("seed") is not None}) > 1
        for samples in groups.values()
    )

    row_values = sorted({s.dims[config.rows] for s in index}, key=natural_key)
    col_values = sorted({s.dims[config.cols] for s in index}, key=natural_key)
    cell_ar = detect_universal_ar(index)

    # (b) Cross-cell confound — every coordinate single-sample, but the seeds of
    # the chosen samples differ across POPULATED cells. This is the "silently
    # mixed" failure the user's seed-locked non-cherry-picked ablation forbids
    # (success criterion 4), so the marker must fire for it too — collected below.
    cross_cell_seeds: set = set()

    cells: list[list[Cell]] = []
    for row in row_values:
        row_cells: list[Cell] = []
        for col in col_values:
            coord = (row, col)
            sample = by_coord.get(coord)
            is_video = sample is not None and sample.media_type == "video"
            if sample is None:
                # Absent coordinate — never skipped (Pitfall 1). (D-09)
                row_cells.append(Cell(CellState.MISSING))
            elif not is_video and not is_decodable(sample.path):
                # File present but won't decode → BROKEN, sample retained. (D-10)
                # Video decodability can't be probed with Pillow — it is decided at
                # runtime by the browser (a won't-play clip degrades to poster +
                # data-blocked via the Plan 02 play() fallback, D-10), so a video
                # sample is always POPULATED here rather than falsely BROKEN.
                row_cells.append(Cell(CellState.BROKEN, sample=sample))
            else:
                # Populated; flag a stray aspect ratio for letterbox fallback (D-11)
                # and surface any duplicate-seed alternates on this cell (D-10/D-09).
                # Video now contributes to the detected universal AR (dimensions come
                # from the container header, no ffmpeg), but per-cell video mismatch
                # stays off — object-fit: cover frames each clip to the cell.
                seed = sample.dims.get("seed")
                if seed is not None:
                    cross_cell_seeds.add(seed)
                alt = alternates.get(coord)
                row_cells.append(
                    Cell(
                        CellState.POPULATED,
                        sample=sample,
                        ar_mismatch=(False if is_video else _ar_of(sample.path) != cell_ar),
                        has_alternates=alt is not None,
                        alternate_seeds=alt if alt is not None else [],
                    )
                )
        cells.append(row_cells)

    cross_cell_varies = len(cross_cell_seeds) > 1
    seed_varies = per_coord_varies or cross_cell_varies

    return GridModel(
        row_values=row_values,
        col_values=col_values,
        cells=cells,
        cell_ar=cell_ar,
        seed_varies=seed_varies,
    )
