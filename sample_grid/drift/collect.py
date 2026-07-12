"""Ladder discovery — map a folder of checkpoint clips to {(cell, step): path}.

Generalizes the two naming schemes the reference implementation hard-coded,
and falls back to the repo's own auto-detect pipeline for anything else:

1. **step-cell** (gridwatch trainer-sampler layout): ``step_NNNNNN_K.mp4`` —
   the cell is the trailing sample index ``K``; when a gridwatch sidecar
   ``metadata.csv`` (``file_name,step,prompt`` columns) sits next to the clips,
   the prompt labels the cell (a trailing `` (text)`` qualifier is stripped,
   matching the validated reference). Without a sidecar the cell is ``cellK``.
2. **prompt-seed**: ``step-XXXX__<prompt>_seedNN.mp4`` — the cell is the
   embedded prompt token.
3. **auto** (fallback): the repo's shared detect pipeline (sidecar > filename >
   subfolder precedence — the same path ``build``/``detect``/``watch`` use).
   Cell = detected prompt, step = detected step; video samples only.

Schemes 1/2 scan the ladder folder NON-recursively (poster/asset subfolders like
``_posters`` never leak in); the fallback is the recursive repo scan.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from sample_grid.core.parse.base import AutoDetectParser
from sample_grid.core.parse.filename import FilenameExtractor
from sample_grid.core.parse.sidecar import SidecarExtractor
from sample_grid.core.parse.subfolder import SubfolderExtractor
from sample_grid.core.scan import Scanner

# Scheme 1 — step_NNNNNN_K.mp4 (cell = trailing sample index K).
STEP_CELL_RE = re.compile(r"^step_(\d+)_(\d+)\.(?:mp4|webm)$", re.IGNORECASE)

# Scheme 2 — step-XXXX__<prompt>_seedNN.mp4 (cell = prompt token). The greedy
# prompt group backtracks to the LAST ``_seed<digits>`` so prompts may contain
# underscores.
PROMPT_SEED_RE = re.compile(r"^step-(\d+)__(.+)_seed(\d+)\.(?:mp4|webm)$", re.IGNORECASE)

# The reference stripped the gridwatch sampler's `` (text)`` prompt qualifier.
_TEXT_QUALIFIER_RE = re.compile(r"\s*\(text\)\s*$")


def _sidecar_cell_labels(folder: Path) -> "dict[str, str]":
    """Map sample index K -> prompt label from a gridwatch ``metadata.csv``.

    Reads the ``file_name``/``prompt`` columns; rows whose file_name matches
    the step-cell scheme contribute ``K -> prompt`` (`` (text)`` stripped).
    Missing/malformed sidecars simply yield no labels (cells fall back to
    ``cellK``) — never an error.
    """
    meta = folder / "metadata.csv"
    labels: dict[str, str] = {}
    if not meta.is_file():
        return labels
    try:
        with open(meta, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                name = (row.get("file_name") or "").strip()
                prompt = (row.get("prompt") or "").strip()
                m = STEP_CELL_RE.match(name)
                if m and prompt:
                    labels[m.group(2)] = _TEXT_QUALIFIER_RE.sub("", prompt)
    except (OSError, csv.Error):
        return {}
    return labels


def _collect_step_cell(folder: Path) -> "dict[tuple[str, int], Path]":
    """Scheme 1: {(cell, step): path} for ``step_NNNNNN_K.mp4`` clips."""
    labels = _sidecar_cell_labels(folder)
    clips: dict[tuple[str, int], Path] = {}
    for path in folder.iterdir():
        if not path.is_file():
            continue
        m = STEP_CELL_RE.match(path.name)
        if not m:
            continue
        step, k = int(m.group(1)), m.group(2)
        cell = labels.get(k, f"cell{k}")
        clips[(cell, step)] = path
    return clips


def _collect_prompt_seed(folder: Path) -> "dict[tuple[str, int], Path]":
    """Scheme 2: {(cell, step): path} for ``step-XXXX__<prompt>_seedNN.mp4``."""
    clips: dict[tuple[str, int], Path] = {}
    for path in folder.iterdir():
        if not path.is_file():
            continue
        m = PROMPT_SEED_RE.match(path.name)
        if not m:
            continue
        clips[(m.group(2), int(m.group(1)))] = path
    return clips


def _collect_auto(folder: Path) -> "dict[tuple[str, int], Path]":
    """Fallback: the repo's shared auto-detect pipeline (video samples only)."""
    files = Scanner().scan(folder)
    sidecar_files = Scanner().scan_sidecars(folder)
    extractors = [
        SidecarExtractor(sidecar_files, root=folder),
        FilenameExtractor(root=folder),
        SubfolderExtractor(root=folder),
    ]
    index, _report = AutoDetectParser(extractors, root=folder).parse(files)
    clips: dict[tuple[str, int], Path] = {}
    for sample in index:
        if sample.media_type != "video":
            continue  # the metric decodes frame sequences; stills are not clips
        step = sample.dims.get("step")
        prompt = sample.dims.get("prompt")
        if step is None or prompt is None:
            continue
        clips[(str(prompt), int(step))] = sample.path
    return clips


def collect_ladder(folder: Path) -> "tuple[str, dict[tuple[str, int], Path]]":
    """Auto-detect the naming scheme of ``folder`` and collect its clips.

    Returns ``(scheme, {(cell, step): path})`` where scheme is ``"step-cell"``,
    ``"prompt-seed"``, or ``"auto"``. When both filename schemes match, the one
    matching MORE files wins (mixed folders are pathological; majority rules).
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"not a directory: {folder}")

    step_cell = _collect_step_cell(folder)
    prompt_seed = _collect_prompt_seed(folder)
    if step_cell and len(step_cell) >= len(prompt_seed):
        return "step-cell", step_cell
    if prompt_seed:
        return "prompt-seed", prompt_seed
    return "auto", _collect_auto(folder)


def collect_chain(
    folders: "list[Path]", echo=print
) -> "tuple[str, dict[tuple[str, int], Path]]":
    """Collect several rounds as ONE chained ladder, in argument order.

    Steps in each round's filenames are LOCAL to that round; the effective step
    adds the cumulative max local step of all prior rounds (the validated prime
    r1 -> r1b -> r1c -> r1d convention). Returns ``(scheme_summary, clips)``.
    """
    clips: dict[tuple[str, int], Path] = {}
    schemes: list[str] = []
    offset = 0
    for folder in folders:
        scheme, local = collect_ladder(folder)
        schemes.append(scheme)
        local_max = 0
        for (cell, step), path in local.items():
            local_max = max(local_max, step)
            clips[(cell, offset + step)] = path
        echo(
            f"  chain round {Path(folder).name} [{scheme}]: "
            f"{len(local)} clips, local steps up to {local_max}, "
            f"effective offset +{offset}"
        )
        offset += local_max
    return "+".join(dict.fromkeys(schemes)), clips
