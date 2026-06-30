"""Shared pytest fixtures for the sample-html-generator suite.

The fixtures here build *real* on-disk sample folders that follow the documented
Phase-1 grouping convention (immediate parent dir = prompt, first integer in the
file stem = step): ``outputs/<prompt>/step_<N>.png``.
"""
from __future__ import annotations

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
