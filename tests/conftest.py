"""Shared pytest fixtures for the sample-html-generator suite.

The fixtures here build *real* on-disk sample folders that follow the documented
Phase-1 grouping convention (immediate parent dir = prompt, first integer in the
file stem = step): ``outputs/<prompt>/step_<N>.png``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from sample_grid.core.model import Sample, SampleIndex

# Documented Phase-1 convention fixture axes.
PROMPTS = ["a serene lake", "a city street"]
STEPS = [200, 600, 1000]

# Tiny, uniform aspect ratio (32x18 ~= 16:9) so the dense happy path has no
# AR mismatch and every cell shares the detected universal aspect ratio.
IMG_W, IMG_H = 32, 18

# The prompt fixture used to prove HTML escaping (Pitfall 4). Note: Windows
# forbids < > " in path names, so this string is carried only in a Sample's
# prompt DIMENSION (hand-built index), never as an on-disk directory name.
XSS_PROMPT = 'a "x" <b> & c'


def _write_png(
    path: Path,
    color: tuple[int, int, int],
    size: tuple[int, int] = (IMG_W, IMG_H),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="PNG")


def _write_dense(outputs: Path) -> None:
    """Populate every (prompt, step) coordinate under ``outputs`` with a valid PNG."""
    for pi, prompt in enumerate(PROMPTS):
        for si, step in enumerate(STEPS):
            color = (30 + pi * 40, 60 + si * 30, 90)
            _write_png(outputs / prompt / f"step_{step}.png", color)


@pytest.fixture
def dense_sample_folder(tmp_path: Path) -> Path:
    """A finished output folder where every (prompt, step) coordinate is populated.

    Layout: ``<tmp>/outputs/<prompt>/step_<N>.png`` for the cartesian product of
    PROMPTS x STEPS. Returns the ``outputs`` directory to point ``build`` at.
    """
    outputs = tmp_path / "outputs"
    _write_dense(outputs)
    return outputs


# The single coordinate dropped / corrupted / made stray across the fixtures
# below — kept as a named constant so tests can assert on the exact cell.
HOLE_PROMPT, HOLE_STEP = PROMPTS[0], STEPS[1]


@pytest.fixture
def grid_axes() -> dict:
    """The fixture axis domains (prompts, steps) for assertion convenience."""
    return {"prompts": list(PROMPTS), "steps": list(STEPS)}


@pytest.fixture
def hole_coord() -> dict:
    """The single (prompt, step) coordinate that the sparse/corrupt/stray
    fixtures drop, corrupt, or make stray — so a test can assert on that cell."""
    return {"prompt": HOLE_PROMPT, "step": HOLE_STEP}


@pytest.fixture
def sparse_sample_folder(tmp_path: Path) -> Path:
    """A dense layout with exactly one (prompt, step) coordinate absent.

    Proves the fixed lattice (GRID-05): build_grid must still emit rows x cols
    cells with that single coordinate classified ``MISSING`` — never collapsed.
    """
    outputs = tmp_path / "sparse"
    _write_dense(outputs)
    (outputs / HOLE_PROMPT / f"step_{HOLE_STEP}.png").unlink()
    return outputs


@pytest.fixture
def corrupt_sample_folder(tmp_path: Path) -> Path:
    """A dense layout where one coordinate's file is present but undecodable.

    The corrupt file keeps a ``.png`` suffix (so the scanner discovers it) but
    contains non-image bytes, so Pillow ``verify()`` fails → ``BROKEN`` (D-10).
    """
    outputs = tmp_path / "corrupt"
    _write_dense(outputs)
    corrupt = outputs / HOLE_PROMPT / f"step_{HOLE_STEP}.png"
    corrupt.write_bytes(b"\x89PNG not really an image at all")
    return outputs


@pytest.fixture
def stray_ar_sample_folder(tmp_path: Path) -> Path:
    """A dense layout where one image has a different aspect ratio than the rest.

    All cells are 32x18 (~16:9) except the stray, which is 18x32 (~9:16). The
    stray cell must flag ``ar_mismatch`` (D-11) while every other cell does not.
    """
    outputs = tmp_path / "stray"
    _write_dense(outputs)
    # Overwrite the one coordinate with a portrait image (different AR).
    _write_png(
        outputs / HOLE_PROMPT / f"step_{HOLE_STEP}.png",
        (200, 40, 40),
        size=(IMG_H, IMG_W),
    )
    return outputs


# Unpadded step magnitudes spanning three orders of magnitude — the natural-sort
# proof (GRID-06 / D-11): lexical order would wrongly interleave 1000 before 200.
UNPADDED_STEPS = [200, 1000, 30000]


@pytest.fixture
def unpadded_step_folder(tmp_path: Path) -> Path:
    """A single-prompt folder whose steps are unpadded and span magnitudes.

    Layout: ``<tmp>/unpadded/<prompt>/step_<N>.png`` for N in 200, 1000, 30000.
    A lexical sort would order these [1000, 200, 30000]; a numeric-aware
    ``natural_key`` must order them [200, 1000, 30000].
    """
    outputs = tmp_path / "unpadded"
    prompt = PROMPTS[0]
    for si, step in enumerate(UNPADDED_STEPS):
        _write_png(outputs / prompt / f"step_{step}.png", (40, 60 + si * 20, 90))
    return outputs


@pytest.fixture
def unclassifiable_folder(tmp_path: Path) -> Path:
    """A folder mixing one classifiable file with one auto-detect can't place.

    ``a_lake/step_600.png`` parses cleanly (step + prompt); ``a_lake/notes.png``
    has NO integer in its stem, so the auto-detect picker cannot derive a step
    and must skip-and-count it (D-05). Returns the folder to point the parser at.
    """
    outputs = tmp_path / "unclassifiable"
    _write_png(outputs / "a_lake" / "step_600.png", (40, 60, 90))
    _write_png(outputs / "a_lake" / "notes.png", (90, 40, 60))
    return outputs


@pytest.fixture
def aitoolkit_style_folder(tmp_path: Path) -> Path:
    """An ai-toolkit-style sample folder (A2): ``{ts}__{step:09d}_{idx}.jpg``.

    The step is zero-padded to 9 digits; the trailing small int is the prompt
    sample index (surfaced honestly as an integer prompt, NO index→text
    resolution). Returns the folder to point the parser at.
    """
    outputs = tmp_path / "aitoolkit"
    _write_png(outputs / "job" / "20260630__000000600_3.jpg", (30, 50, 70))
    _write_png(outputs / "job" / "20260630__000001200_3.jpg", (30, 50, 70))
    return outputs


@pytest.fixture
def template_noise_folder(tmp_path: Path) -> Path:
    """A media file whose name carries trailing noise the ``{*}`` token absorbs.

    Layout: ``<tmp>/template_noise/a_lake/step_600_seed42_00042_20260630.png``.
    The ``_00042_20260630`` tail (sample index + a date) is real-world junk that a
    template like ``{prompt}/step_{step}_seed{seed}_{*}.png`` must still match by
    letting the explicit ``{*}`` ignore token absorb it. Returns the folder.
    """
    outputs = tmp_path / "template_noise"
    _write_png(
        outputs / "a_lake" / "step_600_seed42_00042_20260630.png", (40, 60, 90)
    )
    return outputs


# ---------------------------------------------------------------------------
# Sidecar fixtures (Plan 02-03 / META-03) — the three real-world association
# shapes plus the caption-file convention. Each folder carries image media the
# scanner still finds AND a sidecar file the media scanner must never surface as
# a cell. Alias keys are deliberately case-varied to exercise the alias tables.
# ---------------------------------------------------------------------------


@pytest.fixture
def sidecar_json_folder(tmp_path: Path) -> Path:
    """Per-file ``<stem>.json`` sidecars next to the media (highest precedence).

    Uses case-varied aliases (``Steps``/``noise_seed``/``positive_prompt``) so a
    passing test also proves the alias tables are case-insensitive.
    """
    outputs = tmp_path / "sidecar_json"
    _write_png(outputs / "a_lake" / "sample_1.png", (40, 60, 90))
    (outputs / "a_lake" / "sample_1.json").write_text(
        json.dumps({"Steps": 800, "noise_seed": 42, "positive_prompt": "a serene lake"}),
        encoding="utf-8",
    )
    return outputs


@pytest.fixture
def sidecar_csv_folder(tmp_path: Path) -> Path:
    """A ``metadata.csv`` keyed by ``file_name`` with a comma-containing prompt.

    The prompt cell ``"a lake, at dusk, cinematic"`` proves ``csv.DictReader``
    keeps the whole string intact (Pitfall 4 — never ``line.split(',')``).
    """
    outputs = tmp_path / "sidecar_csv"
    _write_png(outputs / "run" / "img_0.png", (40, 60, 90))
    _write_png(outputs / "run" / "img_1.png", (50, 70, 100))
    csv_text = (
        "file_name,step,seed,prompt\r\n"
        'img_0.png,500,42,"a lake, at dusk, cinematic"\r\n'
        'img_1.png,900,42,"a city, at night"\r\n'
    )
    (outputs / "run" / "metadata.csv").write_text(csv_text, encoding="utf-8")
    return outputs


@pytest.fixture
def per_folder_meta_folder(tmp_path: Path) -> Path:
    """A folder-level ``meta.json`` applying its dims to every media file in it."""
    outputs = tmp_path / "per_folder"
    _write_png(outputs / "a_lake" / "frame_a.png", (40, 60, 90))
    _write_png(outputs / "a_lake" / "frame_b.png", (50, 70, 100))
    (outputs / "a_lake" / "meta.json").write_text(
        json.dumps({"global_step": 1200, "seed": 7, "prompt": "a serene lake"}),
        encoding="utf-8",
    )
    return outputs


@pytest.fixture
def caption_txt_folder(tmp_path: Path) -> Path:
    """A per-file ``<stem>.txt`` whose entire contents are the prompt (kohya style)."""
    outputs = tmp_path / "caption"
    _write_png(outputs / "shots" / "clip_1.png", (40, 60, 90))
    (outputs / "shots" / "clip_1.txt").write_text(
        "a lone figure on a snowy ridge, wide shot", encoding="utf-8"
    )
    return outputs


@pytest.fixture
def malformed_sidecar_folder(tmp_path: Path) -> Path:
    """A per-file JSON sidecar with corrupt contents — must be skipped, never raise."""
    outputs = tmp_path / "malformed"
    _write_png(outputs / "a_lake" / "broken_1.png", (40, 60, 90))
    (outputs / "a_lake" / "broken_1.json").write_text(
        "{ this is not: valid json ", encoding="utf-8"
    )
    return outputs


# ---------------------------------------------------------------------------
# Gap-closure fixtures (Plan 02-05 / CR-01 · WR-01 · WR-05) — the previously
# UNTESTED territory: paths >2 segments below the scan root, ai-toolkit
# integer-index layouts, and contradictory filename-vs-subfolder metadata. Each
# produced DUPLICATE Samples before the shared rel_id_for merge key.
# ---------------------------------------------------------------------------


@pytest.fixture
def nested_template_folder(tmp_path: Path) -> Path:
    """A media file THREE segments below the scan root: ``root/lake/2023/step_600.png``.

    The extra ``2023/`` directory makes the file's rel-to-root path 3 segments —
    the case where the old full-rel template key (``lake/2023/step_600.png``) and
    the old prompt-derived filename key (``2023/step_600.png``) diverged into two
    buckets (CR-01). Returns the scan root.
    """
    outputs = tmp_path / "nested_template"
    _write_png(outputs / "lake" / "2023" / "step_600.png", (40, 60, 90))
    return outputs


@pytest.fixture
def aitoolkit_sidecar_folder(tmp_path: Path) -> Path:
    """An ai-toolkit integer-index media file WITH a per-file sidecar prompt.

    Layout: ``root/my-job/20260630__000000600_3.jpg`` (step=600, trailing index 3)
    beside ``root/my-job/20260630__000000600_3.json`` = ``{"prompt": "a serene lake"}``.
    FilenameExtractor surfaces the trailing ``3`` as an integer prompt; the sidecar
    (highest precedence, D-03) must override it. Pre-fix the two keyed on different
    tokens and the override silently no-op'd (WR-01). Returns the scan root.
    """
    outputs = tmp_path / "aitoolkit_sidecar"
    _write_png(outputs / "my-job" / "20260630__000000600_3.jpg", (30, 50, 70))
    (outputs / "my-job" / "20260630__000000600_3.json").write_text(
        json.dumps({"prompt": "a serene lake"}), encoding="utf-8"
    )
    return outputs


@pytest.fixture
def structural_subfolder_folder(tmp_path: Path) -> Path:
    """Contradictory filename vs subfolder step for ONE physical file.

    Layout: ``root/a_lake/step_600/final_step_650.png``. The filename token says
    step=650 (parent ``step_600`` becomes its prompt); the subfolder walk says
    step=600 (``a_lake`` becomes its prompt). Pre-fix these prompt-derived keys
    diverged into two buckets → two phantom Samples (WR-05). Returns the scan root.
    """
    outputs = tmp_path / "structural_subfolder"
    _write_png(outputs / "a_lake" / "step_600" / "final_step_650.png", (40, 60, 90))
    return outputs


@pytest.fixture
def xss_prompt_index(tmp_path: Path) -> SampleIndex:
    """A hand-built index whose prompt dimension carries HTML metacharacters.

    Windows forbids ``< > "`` in directory names, so the malicious string lives
    only in ``dims["prompt"]``; the backing file is safely named. This is what
    drives the render-layer escaping assertion (Pitfall 4 / T-1-01).
    """
    img = tmp_path / "safe_prompt" / "step_100.png"
    _write_png(img, (50, 50, 50))
    return [
        Sample(
            id="safe_prompt/step_100.png",
            path=img,
            media_type="image",
            dims={"step": 100, "prompt": XSS_PROMPT},
        )
    ]
