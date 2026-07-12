"""`grid` CLI — the Phase-1 ``build`` subcommand (RUN-01).

Thin entrypoint: it owns argument parsing, the output directory layout, copying
populated samples into a self-contained ``assets/`` bundle, and auto-opening the
result (D-07). All grid logic lives in the pure core/render layers.
"""
from __future__ import annotations

import asyncio
import contextlib
import csv
import html
import shutil
import socket
import sys
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import typer

from sample_grid.core.grid import build_grid
from sample_grid.core.model import CellState, GridConfig
from sample_grid.core.parse.base import AutoDetectParser
from sample_grid.core.parse.filename import FilenameExtractor
from sample_grid.core.parse.sidecar import SidecarExtractor
from sample_grid.core.parse.subfolder import SubfolderExtractor
from sample_grid.core.parse.template import TemplateParser
from sample_grid.core.scan import Scanner
from sample_grid.live.diff import diff
from sample_grid.live.server import Broadcaster, build_app
from sample_grid.live.watcher import watch_loop
from sample_grid.render.renderer import (
    render,
    render_cell_fragment,
    render_col_header_fragment,
    render_row_header_fragment,
)
from sample_grid.render.resolver import (
    InlineResolver,
    RelativeResolver,
    ServedResolver,
)

# Legacy Windows consoles default to a non-UTF-8 code page (e.g. cp1252) whose
# charmap cannot encode characters such as U+2192 (->). Rich writes help/output
# straight to the console, so a stray non-ASCII glyph would raise
# UnicodeEncodeError before anything renders. Reconfigure the standard streams to
# UTF-8 up front (module import runs before Typer parses args / Rich renders).
# Guarded: a no-op on streams without ``reconfigure`` (captured pipes, older
# wrappers). Worst case on a truly legacy console is mojibake, never a crash.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:  # pragma: no cover - defensive; never fail on import
            pass

app = typer.Typer(
    add_completion=False,
    help="Build comparison grids from a folder of model samples.",
)

# The directory name created inside the user-supplied output base (D-06).
GRID_OUTPUT_DIRNAME = "grid-output"
ASSETS_DIRNAME = "assets"

# How many example mappings / conflict / skip entries `detect` lists inline.
_PREVIEW_LIMIT = 5


def _auto_parse(folder: Path, template: "str | None" = None):
    """Run the shared auto-detect pipeline: scan → extract → merge.

    Returns ``(SampleIndex, DetectionReport)``. ``build`` discards the report
    (D-02 CLI-silent); ``detect`` prints it (D-01). One code path guarantees the
    two commands agree on what was detected.

    ``SidecarExtractor`` is fed the folder's sidecar files (surfaced by the
    disjoint ``scan_sidecars`` walk) and listed first for readability; actual
    precedence (``sidecar > filename > subfolder``, D-03) is decided by
    ``SOURCE_PRECEDENCE`` in the merge, not list order.

    When ``template`` is supplied (META-04 / D-06), a ``TemplateParser`` is added
    as the highest-precedence source (``source="template"``, precedence 4). It is
    NOT mutually exclusive with auto-detect: the template wins for the fields it
    captures and the other extractors fill only the gaps (A1) — the fill-gaps
    merge is the whole point of the override.
    """
    files = Scanner().scan(folder)
    sidecar_files = Scanner().scan_sidecars(folder)
    extractors = [
        SidecarExtractor(sidecar_files, root=folder),
        FilenameExtractor(root=folder),
        SubfolderExtractor(root=folder),
    ]
    if template:
        extractors.insert(0, TemplateParser(template, root=folder))
    return AutoDetectParser(extractors, root=folder).parse(files)


@app.callback()
def _root() -> None:
    """Force multi-command (group) behavior so ``build`` stays a subcommand.

    Without a callback, Typer collapses a single-command app into a root command
    and ``build`` would be swallowed as a positional arg. The callback keeps room
    for the future ``watch`` (P4) and ``freeze`` (P5) siblings (D-05).
    """


def _empty_state_html(folder: Path) -> str:
    """A self-contained empty-state page (UI-SPEC Copywriting Contract)."""
    looked_in = html.escape(str(folder))
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en" data-theme="dark" data-density="comfortable">\n'
        "<head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Sample Grid — no samples</title>"
        "<style>body{margin:0;min-height:100vh;display:flex;flex-direction:column;"
        "align-items:center;justify-content:center;background:#0e0f11;color:#e6e8eb;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
        "text-align:center;padding:32px}h1{font-size:18px;font-weight:600;margin:0 0 16px}"
        "p{font-size:13px;line-height:1.45;color:#9aa0a6;max-width:42ch}</style></head>\n"
        "<body><h1>No samples found</h1>"
        f"<p>Point build at a folder containing .png, .jpg, or .webp files. "
        f"Looked in: {looked_in}</p></body></html>\n"
    )


@app.command()
def build(
    folder: Path = typer.Argument(..., help="Folder of model samples to grid."),
    output: Path = typer.Option(
        Path("."),
        "-o",
        "--output",
        help="Output base directory; the grid is written to <output>/grid-output/.",
    ),
    no_open: bool = typer.Option(
        False, "--no-open", help="Do not open the result in a browser (CI/scripts)."
    ),
    cell_size: int = typer.Option(
        240, "--cell-size", help="Cell width in px (default Comfortable)."
    ),
    template: str = typer.Option(
        None,
        "--template",
        help="Override auto-detect: {prompt}/step_{step}_seed{seed}.mp4",
    ),
) -> None:
    """Build a static Steps x Prompts grid from FOLDER.

    Sample convention (Phase 1): the immediate parent directory is the prompt and
    the first integer in the filename is the training step --
    ``<prompt>/step_<N>.<ext>`` for .png/.jpg/.jpeg/.webp files.

    Writes ``<output>/grid-output/index.html`` plus a self-contained
    ``assets/`` bundle, then opens it in your browser (suppress with --no-open).
    """
    out_dir = output / GRID_OUTPUT_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "index.html"

    # Auto-detect (filename + subfolder). The DetectionReport is deliberately
    # DISCARDED here — `build` renders with best-guess detection and prints no
    # conflicts/skips/multi-seed warnings to the terminal (D-02). Inspection is
    # the explicit `detect` step; the artifact still carries the D-09 marker.
    index, _report = _auto_parse(folder, template=template)

    # Empty-state: never emit a silent content-free grid (UI-SPEC).
    if not index:
        message = (
            "No samples found. Point build at a folder containing "
            f".png, .jpg, or .webp files. Looked in: {folder}"
        )
        index_path.write_text(_empty_state_html(folder), encoding="utf-8")
        typer.echo(message, err=True)
        raise typer.Exit(0)

    grid = build_grid(index, GridConfig())

    # Copy each populated sample into the bundle, preserving its relative id path
    # so identical basenames across prompts never collide.
    resolver = RelativeResolver(assets_dir=ASSETS_DIRNAME)
    for row in grid.cells:
        for cell in row:
            if cell.state == CellState.POPULATED and cell.sample is not None:
                dest = out_dir / ASSETS_DIRNAME / Path(cell.sample.id)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cell.sample.path, dest)

    html_str = render(
        grid, resolver, live=False, cell_size_px=cell_size
    )
    index_path.write_text(html_str, encoding="utf-8")

    typer.echo(f"Wrote {index_path}")
    if not no_open:
        webbrowser.open(index_path.resolve().as_uri())


@app.command()
def freeze(
    folder: Path = typer.Argument(..., help="Folder of model samples to freeze."),
    output: Path = typer.Option(
        Path("."),
        "-o",
        "--output",
        help="Output base directory; the bundle is written to <output>/grid-output/.",
    ),
    no_open: bool = typer.Option(
        False, "--no-open", help="Do not open the result in a browser (CI/scripts)."
    ),
    cell_size: int = typer.Option(
        240, "--cell-size", help="Cell width in px (default Comfortable)."
    ),
    template: str = typer.Option(
        None,
        "--template",
        help="Override auto-detect: {prompt}/step_{step}_seed{seed}.mp4",
    ),
    inline: bool = typer.Option(
        False,
        "--inline",
        help="Opt-in single-file base64 export -- images / tiny grids only.",
    ),
    max_inline_mb: float = typer.Option(
        75.0,
        "--max-inline-mb",
        help="Degrade --inline to the folder bundle above this total media size.",
    ),
) -> None:
    """Freeze FOLDER into a self-contained standalone bundle (EXPORT-01/EXPORT-02).

    Freeze is the offline-artifact sibling of ``watch``: it re-parses the same
    folder (one detect path with ``build``/``detect``/``watch``) and emits the
    ``build`` output layout -- ``<output>/grid-output/index.html`` + a relative
    ``assets/`` bundle -- rendered with ``live=False`` via ``RelativeResolver``. The
    result opens straight from ``file://`` with NO server: ``live=False`` strips the
    only server-coupled markup (the ``LIVE_ENDPOINT`` injection), so the frozen page
    is the live grid MINUS exactly the live-reload wiring (EXPORT-02). This is the
    command the ``watch`` handoff prints (``grid freeze <folder>``).

    The whole export reuses the proven render seam -- no freeze-specific rendering,
    template, or JS. It swaps only the (already relative) resolver.
    """
    out_dir = output / GRID_OUTPUT_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "index.html"

    # Same shared detect path build/detect/watch use, so freeze can never disagree
    # with them on what was found. The report is discarded (offline artifact, no
    # terminal eval-integrity output — mirror build's D-02 silence).
    index, _report = _auto_parse(folder, template=template)

    # Empty-state early-exit BEFORE any copy: never emit a silent content-free
    # bundle, and never create an assets/ folder for a grid with no samples.
    if not index:
        message = (
            "No samples found. Point freeze at a folder containing "
            f".png, .jpg, or .webp files. Looked in: {folder}"
        )
        index_path.write_text(_empty_state_html(folder), encoding="utf-8")
        typer.echo(message, err=True)
        raise typer.Exit(0)

    grid = build_grid(index, GridConfig())

    populated = [
        c
        for row in grid.cells
        for c in row
        if c.state == CellState.POPULATED and c.sample is not None
    ]

    # SC-3 guardrail (T-5-04 DoS mitigation): --inline is a single-file base64
    # opt-in for images / tiny grids ONLY. base64 video is unreliable (iOS Safari,
    # ~33% inflation, ``#t=`` fragments on data: URIs) and a huge total crashes the
    # tab, so ANY video cell OR a total over --max-inline-mb DEGRADES back to the
    # folder bundle with a warning (degrade-to-folder is friendlier than a hard
    # refuse — RESEARCH A3). base64 is never the default and never used for video.
    if inline:
        total = sum(c.sample.path.stat().st_size for c in populated)
        has_video = any(c.sample.media_type == "video" for c in populated)
        limit = max_inline_mb * 1024 * 1024
        if has_video or total > limit:
            reasons = []
            if has_video:
                reasons.append("it contains video")
            if total > limit:
                reasons.append(
                    f"its total media size ({total / 1024 / 1024:.3f} MB) exceeds "
                    f"--max-inline-mb ({max_inline_mb} MB)"
                )
            typer.echo(
                "Warning: --inline single-file base64 is for images / tiny grids "
                f"only, but {' and '.join(reasons)}. Falling back to the folder "
                "bundle (relative assets/).",
                err=True,
            )
            inline = False

    if inline:
        # Single-file mode: embed each sample as a base64 data: URI and copy NO
        # assets — the whole page is self-contained in index.html.
        resolver = InlineResolver()
    else:
        # DEFAULT folder-bundle path: copy each populated sample into the relative
        # bundle, keyed on its posix ``sample.id`` so identical basenames across
        # prompts never collide. ``dest`` is built with pathlib (never string
        # concat) and ``sample.id`` is scanner-confined, so no ``..`` can traverse
        # out of assets/ (T-5-01).
        resolver = RelativeResolver(assets_dir=ASSETS_DIRNAME)
        for cell in populated:
            dest = out_dir / ASSETS_DIRNAME / Path(cell.sample.id)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cell.sample.path, dest)

    # SAME render seam for both modes — the renderer never learns which resolver
    # it got (resolver-swap seam; test_renderer_resolver_agnostic).
    html_str = render(grid, resolver, live=False, cell_size_px=cell_size)
    index_path.write_text(html_str, encoding="utf-8")

    typer.echo(f"Wrote {index_path}")
    if not no_open:
        webbrowser.open(index_path.resolve().as_uri())


@app.command()
def detect(
    folder: Path = typer.Argument(..., help="Folder of model samples to inspect."),
    template: str = typer.Option(
        None,
        "--template",
        help="Override auto-detect: {prompt}/step_{step}_seed{seed}.mp4",
    ),
) -> None:
    """Preview auto-detection for FOLDER, then exit WITHOUT rendering (META-05 / D-01).

    Runs the exact same pipeline as ``build`` (scan -> filename/subfolder extract ->
    precedence merge -> build_grid) and prints what it found: the detected axes and
    their values, populated/missing counts, a few example cell->dims mappings,
    source disagreements (D-04), unclassifiable files (D-05), and TWO distinct
    seed signals -- per-coordinate multi-seed cells and cross-cell seed variance
    (D-09). It never writes an ``index.html``.
    """
    index, report = _auto_parse(folder, template=template)

    if not index:
        typer.echo(
            "No samples found. Point detect at a folder containing "
            f".png, .jpg, or .webp files. Looked in: {folder}"
        )
        raise typer.Exit(0)

    grid = build_grid(index, GridConfig())

    populated = [
        c for row in grid.cells for c in row if c.state == CellState.POPULATED
    ]
    missing = [c for row in grid.cells for c in row if c.state == CellState.MISSING]

    # Detected axes and their actual values (D-11: headers show real values).
    typer.echo(f"Rows (step): {grid.row_values}")
    typer.echo(f"Cols (prompt): {grid.col_values}")
    typer.echo(f"Cells: {len(populated)} populated, {len(missing)} missing")

    typer.echo("Example mappings:")
    for cell in populated[:_PREVIEW_LIMIT]:
        s = cell.sample
        typer.echo(f"  (step={s.dims.get('step')}, prompt={s.dims.get('prompt')}) -> {s.id}")

    # D-04: conflicts resolve silently by precedence but are counted + listed here.
    typer.echo(
        f"{len(report.conflicts)} samples had source disagreement: "
        f"{report.conflicts[:_PREVIEW_LIMIT]}"
    )
    # D-05: unclassifiable files are skipped + counted here.
    typer.echo(
        f"{len(report.skipped)} files could not be classified: "
        f"{report.skipped[:_PREVIEW_LIMIT]}"
    )

    # Seed signal 1 (per-coordinate) — cells where >1 sample collided at one
    # (step, prompt) coordinate; the lowest seed rendered, the rest are alternates.
    alt_cells = [c for c in populated if c.has_alternates]
    typer.echo(
        f"{len(alt_cells)} cells hold multiple seeds: "
        f"{[c.alternate_seeds for c in alt_cells][:_PREVIEW_LIMIT]}"
    )

    # Seed signal 2 (cross-cell, D-09) — DISTINCT from the per-coordinate count:
    # the seeds of the chosen samples differ across populated cells, the silent
    # confound the seed-locked ablation methodology forbids. Only printed when it
    # actually applies (>1 distinct non-None seed across the grid).
    distinct_seeds = sorted(
        {
            c.sample.dims.get("seed")
            for c in populated
            if c.sample.dims.get("seed") is not None
        }
    )
    if len(distinct_seeds) > 1:
        typer.echo(f"seeds vary across grid: {distinct_seeds}")

    # Exit BEFORE any render / asset copy (mirror build's empty-state early exit).
    raise typer.Exit(0)


@app.command(
    # Beta marker lives in the SHORT help so it shows in `grid --help`'s command
    # list, not only the long docstring. ASCII-only (no arrows/em-dashes) so the
    # one-liner renders on a legacy cp1252 Windows console; the ``\[`` escapes the
    # bracket for Typer's rich_markup_mode (same convention as ``\[drift]`` below).
    short_help=r"\[beta] Composition-plasticity metric over seed-locked ladders.",
)
def drift(
    ladders: "list[Path]" = typer.Argument(
        ...,
        help="One or more ladder folders of seed-locked checkpoint clips.",
    ),
    output: Path = typer.Option(
        Path("."),
        "-o",
        "--output",
        help="Output directory; writes <output>/drift.csv and <output>/drift.html.",
    ),
    chain: bool = typer.Option(
        False,
        "--chain",
        help=(
            "Treat ALL ladder folders as ONE chained ladder in argument order "
            "(effective step = local step + cumulative max of prior rounds)."
        ),
    ),
    label: str = typer.Option(
        None,
        "--label",
        help=(
            "Ladder label override -- applies to a single-folder run or a --chain "
            "run (defaults: the folder name / 'chain')."
        ),
    ),
    motion_cap: float = typer.Option(
        0.30,
        "--motion-cap",
        help=(
            "Auto-exclude cells whose MEDIAN motion_baseline exceeds this -- "
            "their intrinsic motion drowns the drift signal (validated default)."
        ),
    ),
    floor_mult: float = typer.Option(
        1.5,
        "--floor-mult",
        help="Motion floor = per-cell median motion_baseline x this multiplier.",
    ),
    no_open: bool = typer.Option(
        False, "--no-open", help="Do not open the report in a browser (CI/scripts)."
    ),
) -> None:
    r"""\[beta] Composition-drift ("plasticity") metric over seed-locked LADDERS.

    Beta: validated on one training program so far (mima2 T1 chain + prime
    ladders, 2026-07-12). Interpretation rules and guardrail defaults may evolve
    as it is tried on more trainings.

    Measures how much each cell's COMPOSITION keeps shifting between successive
    checkpoints (drift = 1 - Pearson r of 32x18 grayscale composition
    signatures) against a per-clip ``motion_baseline`` (the same statistic
    within one clip -- what mere motion produces). Drift holding above the
    motion floor = composition headroom; >=3 consecutive checkpoints below it
    (a "knee") = composition lock -- paired with identity lock, that is the
    overfit signature. Plasticity, NOT fit.

    Naming auto-detect per ladder folder: ``step_NNNNNN_K.mp4`` (cell = sample
    index K, labeled from a gridwatch ``metadata.csv`` sidecar when present),
    ``step-XXXX__<prompt>_seedNN.mp4`` (cell = prompt), else the repo's shared
    auto-detect pipeline (video samples only). Writes ``<output>/drift.csv`` +
    ``<output>/drift.html``. Needs the optional ``\[drift]`` extra
    (``uv pip install -e ".\[drift]"``).
    """
    # Guardrail metric deps live behind the optional [drift] extra; import the
    # drift package lazily (mirrors watch's local uvicorn import) and fail with
    # the install hint, not a traceback.
    from sample_grid.drift import analyze as drift_analyze
    from sample_grid.drift import collect as drift_collect
    from sample_grid.drift import metric as drift_metric
    from sample_grid.drift.report import render_report

    try:
        drift_metric._lazy_import()
    except drift_metric.DriftDependencyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    for folder in ladders:
        if not folder.is_dir():
            typer.echo(f"Not a directory: {folder}", err=True)
            raise typer.Exit(1)

    # Collect: each folder is its own ladder (label = folder name), or ONE
    # chained ladder in argument order under --chain (the prime r1→r1d case).
    collected: "list[tuple[str, str, dict]]" = []  # (label, scheme, clips)
    if chain:
        ladder_label = label or "chain"
        typer.echo(f"Collecting chained ladder '{ladder_label}' ...")
        scheme, clips = drift_collect.collect_chain(ladders, echo=typer.echo)
        collected.append((ladder_label, scheme, clips))
    else:
        seen: dict[str, int] = {}
        for folder in ladders:
            name = label if (label and len(ladders) == 1) else folder.name
            seen[name] = seen.get(name, 0) + 1
            if seen[name] > 1:  # keep labels unique when basenames collide
                name = f"{name}-{seen[name]}"
            scheme, clips = drift_collect.collect_ladder(folder)
            collected.append((name, scheme, clips))

    all_rows: list = []
    analyses: list = []
    scheme_by_ladder: dict = {}
    for ladder_label, scheme, clips in collected:
        if not clips:
            typer.echo(
                f"WARN ladder '{ladder_label}': no clips matched any naming "
                "scheme -- skipped.",
                err=True,
            )
            continue
        cells = sorted({c for c, _ in clips})
        typer.echo(
            f"Processing {ladder_label} [{scheme}]: {len(clips)} clips, "
            f"{len(cells)} cells: {cells}"
        )
        rows = drift_metric.process_ladder(ladder_label, clips)
        all_rows.extend(rows)
        analysis = drift_analyze.analyze_ladder(
            ladder_label, rows, motion_cap=motion_cap, floor_mult=floor_mult
        )
        analyses.append(analysis)
        scheme_by_ladder[ladder_label] = scheme

        # Guardrail 1 — high-motion cells are excluded from floor/knee analysis
        # (they stay in the CSV), loudly.
        for cell in analysis.excluded_cells:
            typer.echo(
                f"  WARN cell '{cell.cell}': median motion_baseline "
                f"{cell.motion_median:.3f} > --motion-cap {motion_cap:.2f} -- "
                "excluded from floor/knee analysis (intrinsic motion drowns "
                "the drift signal).",
                err=True,
            )
        # Guardrail 2 — knees, reported with step ranges.
        for cell in analysis.cells:
            for start, end in cell.knees:
                typer.echo(
                    f"  KNEE {ladder_label}/{cell.cell}: steps {start}-{end} -- "
                    f">={drift_analyze.KNEE_MIN_RUN} consecutive checkpoints "
                    f"below the motion floor ({cell.floor:.4f}) -- composition "
                    "lock."
                )

    if not all_rows:
        typer.echo(
            "No ladder clips found. Point drift at folders of seed-locked "
            "checkpoint clips (step_NNNNNN_K.mp4, step-XXXX__<prompt>_seedNN.mp4, "
            "or the grid folder convention).",
            err=True,
        )
        raise typer.Exit(1)

    # Guardrail 3 — drift levels are only comparable within one ladder.
    typer.echo(
        "Note: drift levels are only comparable WITHIN a ladder -- checkpoint "
        "spacing scales per-step drift (a 400-step ladder and a 1000-step "
        "ladder are not comparable on absolute drift).",
        err=True,
    )

    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / "drift.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ladder", "cell", "step", "drift_vs_prev", "motion_baseline"])
        for r in all_rows:
            writer.writerow([
                r["ladder"], r["cell"], r["step"],
                "" if r["drift_vs_prev"] is None else f"{r['drift_vs_prev']:.6f}",
                "" if r["motion_baseline"] is None else f"{r['motion_baseline']:.6f}",
            ])

    html_path = output / "drift.html"
    html_path.write_text(
        render_report(analyses, scheme_by_ladder), encoding="utf-8"
    )

    typer.echo(f"Wrote {csv_path} ({len(all_rows)} rows)")
    typer.echo(f"Wrote {html_path}")
    if not no_open:
        webbrowser.open(html_path.resolve().as_uri())


def _free_port(start: int, host: str = "127.0.0.1", tries: int = 100) -> int:
    """Return the first bindable port at or after ``start`` on ``host``.

    Probes ``start``, ``start+1``, … by attempting a throwaway bind; the first that
    succeeds is free. This is the RESEARCH ``--port`` fallback (default 8000, next
    free on ``OSError``) done up-front so Uvicorn always binds a port it can own.
    """
    for offset in range(tries):
        candidate = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError:
                continue
    return start


def _patch_envelope(patch, grid, resolver) -> dict:
    """Render ``patch``'s HTML from the NEW ``grid`` into the CANONICAL SSE envelope.

    The field names are the cross-plan contract 04-04's ``applyPatch`` reads
    EXACTLY (RESEARCH §Patch envelope):

    * ``replace_cell`` → ``{op, r, c, html}`` — ONLY this op carries a bare ``html``;
    * ``insert_row``   → ``{op, index, step, header_html, cells:[html, …]}``;
    * ``insert_col``   → ``{op, index, prompt, n_cols, header_html,
      cells:[{r, html}, …]}``.

    Header markup NEVER folds into a bare ``html`` key on an insert op — dropping
    that would silently strip a new row/column header on the client. Every HTML
    string comes from the 04-01 fragment renderers (autoescape ON, T-4-02); this
    function never f-strings cell/header markup.
    """
    if patch.op == "replace_cell":
        r, c = patch.r, patch.c
        cell = grid.cells[r][c]
        step = grid.row_values[r]
        prompt = grid.col_values[c]
        item = {"cell": cell, "prompt": prompt}
        return {
            "op": "replace_cell",
            "r": r,
            "c": c,
            "html": render_cell_fragment(item, r, c, step, prompt, resolver),
        }

    if patch.op == "insert_row":
        index = patch.index
        step = patch.step
        cells = []
        for c, prompt in enumerate(grid.col_values):
            item = {"cell": grid.cells[index][c], "prompt": prompt}
            cells.append(render_cell_fragment(item, index, c, step, prompt, resolver))
        return {
            "op": "insert_row",
            "index": index,
            "step": step,
            "header_html": render_row_header_fragment(step, index),
            "cells": cells,
        }

    if patch.op == "insert_col":
        index = patch.index
        prompt = patch.prompt
        cells = []
        for r, step in enumerate(grid.row_values):
            item = {"cell": grid.cells[r][index], "prompt": prompt}
            cells.append(
                {"r": r, "html": render_cell_fragment(item, r, index, step, prompt, resolver)}
            )
        return {
            "op": "insert_col",
            "index": index,
            "prompt": prompt,
            "n_cols": patch.n_cols,
            "header_html": render_col_header_fragment(prompt, index),
            "cells": cells,
        }

    raise ValueError(f"unknown patch op: {patch.op!r}")


@app.command()
def watch(
    folder: Path = typer.Argument(..., help="Folder of model samples to watch live."),
    output: Path = typer.Option(
        Path("."),
        "-o",
        "--output",
        help="Output base directory; the grid is written to <output>/grid-output/.",
    ),
    no_open: bool = typer.Option(
        False, "--no-open", help="Do not open the result in a browser (CI/scripts)."
    ),
    cell_size: int = typer.Option(
        240, "--cell-size", help="Cell width in px (default Comfortable)."
    ),
    template: str = typer.Option(
        None,
        "--template",
        help="Override auto-detect: {prompt}/step_{step}_seed{seed}.mp4",
    ),
    port: int = typer.Option(
        8000, "--port", help="Localhost port (falls back to the next free port)."
    ),
    settle_ms: int = typer.Option(
        1000, "--settle-ms", help="Quiet window a new file must be size-stable for."
    ),
    poll_ms: int = typer.Option(
        500, "--poll-ms", help="How often the settle gate re-stats a pending file."
    ),
    once: bool = typer.Option(
        False,
        "--once",
        hidden=True,
        help="Render current state, write the artifact, and exit (no serve loop).",
    ),
) -> None:
    """Watch FOLDER and serve a live Steps x Prompts grid on localhost (RUN-02).

    Renders the folder's current state immediately, writes the same
    ``<output>/grid-output/index.html`` + ``assets/`` layout ``build`` produces
    (D-04/D-05), and auto-opens the browser (suppress with --no-open). It then owns
    a localhost-only Uvicorn loop: an ``awatch`` task in the app lifespan re-scans ->
    diffs -> renders fragment(s) -> broadcasts each settled batch over SSE, without a
    page reload. On Ctrl-C the last static artifact is left on disk and the ``freeze``
    next-step is printed (D-05/D-06 -- Phase 4 names ``freeze`` only, no export here).
    """
    out_dir = output / GRID_OUTPUT_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "index.html"
    resolver = ServedResolver()

    def _scan_build():
        """Re-run the shared detect path → build the grid (one path with build)."""
        index, _report = _auto_parse(folder, template=template)
        return build_grid(index, GridConfig()) if index else None

    def _copy_assets(grid) -> None:
        """Copy populated samples into the build-layout assets bundle (D-05 freeze)."""
        for row in grid.cells:
            for cell in row:
                if cell.state == CellState.POPULATED and cell.sample is not None:
                    dest = out_dir / ASSETS_DIRNAME / Path(cell.sample.id)
                    if not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(cell.sample.path, dest)

    def _render_page(grid) -> str:
        """The SERVED live page (ServedResolver + live=True); empty-state when bare."""
        if grid is None or not grid.row_values:
            return _empty_state_html(folder)
        return render(grid, resolver, live=True, cell_size_px=cell_size)

    # (D-04) Render current state immediately + write the build-layout artifact.
    current = _scan_build()
    if current is not None:
        _copy_assets(current)
    else:
        # Start empty: hold an empty grid so a later diff is uniform (insert ops).
        current = build_grid([], GridConfig())
    page_html = _render_page(current)
    index_path.write_text(page_html, encoding="utf-8")
    typer.echo(f"Watching {folder} -- serving {index_path}")

    if once:
        # Hidden non-blocking hook (tests/CI): current state rendered, artifact
        # written, return WITHOUT entering the blocking serve loop.
        return

    state = {"grid": current, "html": page_html}
    broadcaster = Broadcaster()

    async def on_ready() -> None:
        """Settled-batch callback: re-scan → diff → render fragments → broadcast."""
        new_grid = _scan_build()
        if new_grid is None:
            return
        _copy_assets(new_grid)
        patches = diff(state["grid"], new_grid)
        for patch in patches:
            await broadcaster.broadcast(_patch_envelope(patch, new_grid, resolver))
        state["grid"] = new_grid
        # Refresh the served page + on-disk artifact so a fresh GET / and the D-05
        # freeze handoff always reflect the latest grid.
        state["html"] = _render_page(new_grid)
        index_path.write_text(state["html"], encoding="utf-8")

    served_app = build_app(
        root=folder,
        page_html_getter=lambda: state["html"],
        broadcaster=broadcaster,
    )

    stop_event = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_app):
        # Launch the awatch task on startup; stop + drain it on shutdown (D-05).
        task = asyncio.create_task(
            watch_loop(
                folder,
                on_ready,
                stop_event=stop_event,
                settle_ms=settle_ms,
                poll_ms=poll_ms,
            )
        )
        try:
            yield
        finally:
            stop_event.set()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    served_app.router.lifespan_context = lifespan

    # Bind 127.0.0.1 ONLY (never 0.0.0.0) — this serves the user's private folder
    # (T-4-03). Pick a free port up-front so Uvicorn always binds one it can own.
    bind_port = _free_port(port)
    import uvicorn  # local import: keep server deps out of the build/detect path.

    server = uvicorn.Server(
        uvicorn.Config(
            served_app, host="127.0.0.1", port=bind_port, log_level="warning"
        )
    )

    async def _open_when_ready() -> None:
        while not server.started:
            await asyncio.sleep(0.05)
        webbrowser.open(f"http://127.0.0.1:{bind_port}/")

    async def _run() -> None:
        opener = None if no_open else asyncio.create_task(_open_when_ready())
        try:
            await server.serve()
        finally:
            if opener is not None:
                opener.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await opener

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        # Uvicorn normally absorbs SIGINT; belt-and-suspenders so the handoff prints.
        pass

    # (D-05/D-06) Leave the artifact; print the freeze pointer. Phase 4 names the
    # freeze command only — it does NOT implement export (Phase 5 owns the bundle).
    typer.echo(
        f"Watch stopped. Static grid left at {index_path}. "
        f"To share it: grid freeze {folder}"
    )


if __name__ == "__main__":
    app()
