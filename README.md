# sample-html-generator

A video-first **model-comparison grid builder** for evaluating generative-model
samples. Point it at a training/inference output folder and it renders an HTML
grid — by default **training-steps × prompts** — to watch a single LoRA or
checkpoint evolve over a run.

## Quick start

First install dependencies (one time):

```bash
uv sync
```

Arrange your samples following the convention (immediate parent folder =
prompt, first integer in the filename = step). Both images (`.png/.jpg/.jpeg/.webp`)
and **videos (`.mp4/.webm`)** are supported:

```
outputs/
  a serene lake/
    step_200.mp4
    step_600.mp4
    step_1000.mp4
  a city street/
    step_200.png
    ...
```

Then build a self-contained grid:

```bash
uv run grid build ./outputs
```

This writes `./grid-output/index.html` (plus a copied `assets/` folder) into the
current directory and opens it in your browser. Use `-o <dir>` to write the grid
under a different base directory, `--no-open` to skip the browser (CI/scripts),
and `--cell-size <px>` to change the cell width.

> **Note:** use `uv run grid …` in a fresh clone — the bare `grid` command only
> works after installing the package onto your PATH (`uv pip install -e .`).

Preview auto-detection without rendering:

```bash
uv run grid detect ./outputs
```

The output is a single, server-free page: open `index.html` directly from disk.
Video cells lazy-load, play on click (muted/looped), and support a synced
master-clock comparison plus Pause-all / Play-visible controls.

## Status

Phases 1–3 complete: **Steps × Prompts** grid with correct metadata
auto-detection and first-class video cells (lazy-loaded, synced eval-grade
playback). Live watch (Phase 4), freeze/export (Phase 5), and distribution
(Phase 6) follow.
