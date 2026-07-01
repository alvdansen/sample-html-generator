"""`grid` CLI — the Phase-1 ``build`` subcommand (RUN-01).

Thin entrypoint: it owns argument parsing, the output directory layout, copying
populated samples into a self-contained ``assets/`` bundle, and auto-opening the
result (D-07). All grid logic lives in the pure core/render layers.
"""
from __future__ import annotations

import html
import shutil
import webbrowser
from pathlib import Path

import typer

from sample_grid.core.grid import build_grid
from sample_grid.core.model import CellState, GridConfig
from sample_grid.core.parse.base import AutoDetectParser
from sample_grid.core.parse.filename import FilenameExtractor
from sample_grid.core.parse.sidecar import SidecarExtractor
from sample_grid.core.parse.subfolder import SubfolderExtractor
from sample_grid.core.scan import Scanner
from sample_grid.render.renderer import render
from sample_grid.render.resolver import RelativeResolver

app = typer.Typer(
    add_completion=False,
    help="Build comparison grids from a folder of model samples.",
)

# The directory name created inside the user-supplied output base (D-06).
GRID_OUTPUT_DIRNAME = "grid-output"
ASSETS_DIRNAME = "assets"

# How many example mappings / conflict / skip entries `detect` lists inline.
_PREVIEW_LIMIT = 5


def _auto_parse(folder: Path):
    """Run the shared auto-detect pipeline: scan → extract → merge.

    Returns ``(SampleIndex, DetectionReport)``. ``build`` discards the report
    (D-02 CLI-silent); ``detect`` prints it (D-01). One code path guarantees the
    two commands agree on what was detected.

    ``SidecarExtractor`` is fed the folder's sidecar files (surfaced by the
    disjoint ``scan_sidecars`` walk) and listed first for readability; actual
    precedence (``sidecar > filename > subfolder``, D-03) is decided by
    ``SOURCE_PRECEDENCE`` in the merge, not list order.
    """
    files = Scanner().scan(folder)
    sidecar_files = Scanner().scan_sidecars(folder)
    extractors = [
        SidecarExtractor(sidecar_files, root=folder),
        FilenameExtractor(),
        SubfolderExtractor(),
    ]
    return AutoDetectParser(extractors).parse(files)


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
) -> None:
    """Build a static Steps × Prompts grid from FOLDER.

    Sample convention (Phase 1): the immediate parent directory is the prompt and
    the first integer in the filename is the training step —
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
    index, _report = _auto_parse(folder)

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
def detect(
    folder: Path = typer.Argument(..., help="Folder of model samples to inspect."),
) -> None:
    """Preview auto-detection for FOLDER, then exit WITHOUT rendering (META-05 / D-01).

    Runs the exact same pipeline as ``build`` (scan → filename/subfolder extract →
    precedence merge → build_grid) and prints what it found: the detected axes and
    their values, populated/missing counts, a few example cell→dims mappings,
    source disagreements (D-04), unclassifiable files (D-05), and TWO distinct
    seed signals — per-coordinate multi-seed cells and cross-cell seed variance
    (D-09). It never writes an ``index.html``.
    """
    index, report = _auto_parse(folder)

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


if __name__ == "__main__":
    app()
