"""Wave-0 freeze tests: the folder-bundle standalone-export slice (EXPORT-01/02).

These lock the ``grid freeze <folder>`` contract BEFORE the command exists, so the
whole file is RED until the ``freeze`` subcommand lands (Task 2). Freeze is a
*composition* of the proven ``build`` path: ``live=False`` + ``RelativeResolver``
+ asset-copy = a self-contained ``grid-output/index.html`` + relative ``assets/``
bundle that opens from ``file://`` with NO server.

Two analog styles are reused verbatim:
  * Typer ``CliRunner`` e2e (mirrors ``test_cli.py::test_build_writes_html``);
  * pure ``render`` / offline-safety asserts (mirrors
    ``test_render.py::test_live_flag_gates_eventsource`` / ``test_src_posix_separators``).

Fixtures (``dense_sample_folder``, ``mixed_media_folder``) are reused as-is from
``conftest.py`` — no new fixtures are added.
"""
from __future__ import annotations

import re
from pathlib import Path

from typer.testing import CliRunner

runner = CliRunner()

# Strip every src VALUE the same way test_render.py does, so freeze can assert the
# forward-slash / ``./assets/`` shape of every emitted media reference.
_SRC = re.compile(r'\bsrc="([^"]*)"')

# The ONLY live-only markup the render seam injects (grid.html.j2 line 106). Freeze
# renders with live=False, so this exact injection must be ABSENT from the artifact.
_LIVE_INJECTION = 'window.LIVE_ENDPOINT = "/events"'


def _freeze(folder: Path, out: Path):
    """Invoke ``grid freeze <folder> -o <out> --no-open`` and return the result."""
    from sample_grid.cli.main import app

    return runner.invoke(
        app, ["freeze", str(folder), "-o", str(out), "--no-open"]
    )


def test_freeze_writes_folder_bundle(dense_sample_folder: Path, tmp_path: Path) -> None:
    """EXPORT-01: ``grid freeze`` on a dense folder writes a self-contained bundle.

    The dense fixture is 2 prompts x 3 steps = 6 populated coordinates, so the
    frozen page must carry exactly 6 ``<img`` tags and the ``assets/`` directory
    must hold the copied sample files on disk (the offline media the page points at).
    """
    out = tmp_path / "out"
    result = _freeze(dense_sample_folder, out)

    assert result.exit_code == 0, result.output

    index_html = out / "grid-output" / "index.html"
    assert index_html.exists(), f"expected {index_html} to be written"

    assets = out / "grid-output" / "assets"
    assert assets.is_dir(), "expected a relative assets/ bundle on disk"
    copied = [p for p in assets.rglob("*") if p.is_file()]
    assert len(copied) == 6, f"expected 6 copied sample files, found {len(copied)}"

    html = index_html.read_text(encoding="utf-8")
    assert html.count("<img") == 6, f"expected 6 <img tags, found {html.count('<img')}"


def test_freeze_video_relative_src(mixed_media_folder: Path, tmp_path: Path) -> None:
    """EXPORT-01 (video): .mp4/.webm freeze with a relative ``./assets/`` poster src.

    The mixed fixture carries an mp4 + a webm, so the frozen page must have video
    cells whose lazy ``data-src`` is a forward-slash ``./assets/...#t=0.001`` poster
    reference — never a backslash path that would fail to load from ``file://``.
    """
    out = tmp_path / "out"
    result = _freeze(mixed_media_folder, out)

    assert result.exit_code == 0, result.output

    html = (out / "grid-output" / "index.html").read_text(encoding="utf-8")

    data_srcs = re.findall(r'data-src="([^"]*)"', html)
    assert data_srcs, "expected at least one video data-src poster reference"
    assert any(s.startswith("./assets/") for s in data_srcs)
    assert all(s.endswith("#t=0.001") for s in data_srcs)

    # No Windows backslash may leak into any media reference.
    assert all("\\" not in s for s in _SRC.findall(html))
    assert all("\\" not in s for s in data_srcs)


def test_freeze_page_offline_safe(dense_sample_folder: Path, tmp_path: Path) -> None:
    """EXPORT-01: the frozen page opens with NO server surface.

    live=False strips the LIVE_ENDPOINT injection and the ServedResolver ``/media/``
    routes; the inert ``if (LIVE_ENDPOINT)`` guard still ships (it never fires).
    """
    out = tmp_path / "out"
    result = _freeze(dense_sample_folder, out)
    assert result.exit_code == 0, result.output

    html = (out / "grid-output" / "index.html").read_text(encoding="utf-8")

    assert _LIVE_INJECTION not in html          # no server injection
    assert "/media/" not in html                # no served-route surface
    assert "if (LIVE_ENDPOINT)" in html         # inert guard present, never fires


def test_freeze_matches_live_minus_wiring(
    dense_sample_folder: Path, tmp_path: Path
) -> None:
    """EXPORT-02: the frozen page is the live page minus exactly the live wiring.

    The frozen artifact is byte-identical to a ``render(grid, RelativeResolver(),
    live=False)`` of the same grid; the live variant differs by EXACTLY the
    LIVE_ENDPOINT injection (shared cell.j2 macro → identical cell markup either
    way). Mirrors ``test_live_flag_gates_eventsource``.
    """
    from sample_grid.cli.main import _auto_parse
    from sample_grid.core.grid import build_grid
    from sample_grid.core.model import GridConfig
    from sample_grid.render.renderer import render
    from sample_grid.render.resolver import RelativeResolver

    out = tmp_path / "out"
    result = _freeze(dense_sample_folder, out)
    assert result.exit_code == 0, result.output
    frozen_html = (out / "grid-output" / "index.html").read_text(encoding="utf-8")

    index, _report = _auto_parse(dense_sample_folder)
    grid = build_grid(index, GridConfig())
    static_html = render(grid, RelativeResolver(), live=False, cell_size_px=240)
    live_html = render(grid, RelativeResolver(), live=True, cell_size_px=240)

    # Freeze IS the live=False render seam — no freeze-specific rendering.
    assert frozen_html == static_html

    # The live page differs by EXACTLY the LIVE_ENDPOINT injection: strip it and
    # the two renders are identical (same shared cell markup, live-only script gone).
    assert _LIVE_INJECTION in live_html
    assert _LIVE_INJECTION not in static_html
    assert live_html.replace(
        '<script>' + _LIVE_INJECTION + ';</script>', ''
    ) == static_html


def test_relative_src_is_posix(dense_sample_folder: Path, tmp_path: Path) -> None:
    """EXPORT-01 (x-platform): every emitted src is a forward-slash ``./assets/`` ref."""
    out = tmp_path / "out"
    result = _freeze(dense_sample_folder, out)
    assert result.exit_code == 0, result.output

    html = (out / "grid-output" / "index.html").read_text(encoding="utf-8")
    srcs = _SRC.findall(html)
    assert srcs, "expected at least one src attribute"
    assert all("\\" not in s for s in srcs)
    assert any(s.startswith("./assets/") for s in srcs)


# ---------------------------------------------------------------------------
# Wave-0 inline-mode + guardrail tests (Plan 02 / SC-3). RED until Task 2 lands
# ``InlineResolver`` + the ``--inline`` / ``--max-inline-mb`` guardrail. The
# folder bundle stays the DEFAULT; ``--inline`` is an opt-in single-file base64
# mode limited to images / tiny grids — video or oversized totals degrade back
# to the folder bundle with a printed warning (never base64 video).
# ---------------------------------------------------------------------------


def _freeze_args(folder: Path, out: Path, *extra: str):
    """Invoke ``grid freeze <folder> -o <out> --no-open`` plus ``extra`` flags."""
    from sample_grid.cli.main import app

    return runner.invoke(
        app, ["freeze", str(folder), "-o", str(out), "--no-open", *extra]
    )


def test_inline_resolver_data_uri(dense_sample_folder: Path) -> None:
    """SC-3 (unit): ``InlineResolver.url`` returns a round-tripping data: URI.

    Point a ``Sample`` at a real fixture PNG; the resolver must emit a
    ``data:image/png;base64,<payload>`` URI whose base64 payload decodes back to
    the exact on-disk file bytes (base64 sidesteps paths entirely — no ``\\`` leak).
    """
    import base64

    from sample_grid.core.model import Sample
    from sample_grid.render.resolver import InlineResolver

    png = dense_sample_folder / "a serene lake" / "step_200.png"
    sample = Sample(
        id="a serene lake/step_200.png",
        path=png,
        media_type="image",
        dims={"step": 200, "prompt": "a serene lake"},
    )

    uri = InlineResolver().url(sample)

    assert uri.startswith("data:image/png;base64,")
    payload = uri.split(",", 1)[1]
    assert base64.b64decode(payload) == png.read_bytes()


def test_freeze_inline_images_only(dense_sample_folder: Path, tmp_path: Path) -> None:
    """SC-3: ``freeze --inline`` on an image grid emits data: URIs and NO assets/.

    Single-file base64 inlines every cell straight into ``src`` and copies nothing,
    so the frozen page must carry ``data:image/`` URIs and there must be NO relative
    ``assets/`` directory on disk (the whole point of the single-file mode).
    """
    out = tmp_path / "out"
    result = _freeze_args(dense_sample_folder, out, "--inline")

    assert result.exit_code == 0, result.output

    html = (out / "grid-output" / "index.html").read_text(encoding="utf-8")
    assert 'src="data:image/' in html, "expected inlined base64 image data: URIs"

    assert not (out / "grid-output" / "assets").exists(), (
        "inline mode must copy no assets/ bundle"
    )


def test_freeze_inline_refuses_video(mixed_media_folder: Path, tmp_path: Path) -> None:
    """SC-3 (media guardrail): ``--inline`` on a video grid degrades to the bundle.

    base64 video is unreliable (iOS Safari, ~33% inflation, ``#t=`` fragments) so
    the guardrail fires on ANY video cell: NO ``data:video/`` payload is emitted, a
    relative ``./assets/`` folder bundle is written INSTEAD, and a warning mentioning
    video + the folder-bundle fallback is printed.
    """
    out = tmp_path / "out"
    result = _freeze_args(mixed_media_folder, out, "--inline")

    assert result.exit_code == 0, result.output

    html = (out / "grid-output" / "index.html").read_text(encoding="utf-8")
    assert "data:video/" not in html, "must NEVER inline base64 video"

    assert (out / "grid-output" / "assets").is_dir(), (
        "video grid must degrade to the folder bundle"
    )
    assert "./assets/" in html, "degraded page must reference the relative bundle"

    warning = result.output.lower()
    assert "video" in warning
    assert "folder bundle" in warning


def test_freeze_inline_refuses_oversized(
    dense_sample_folder: Path, tmp_path: Path
) -> None:
    """SC-3 (size guardrail): ``--inline`` over ``--max-inline-mb`` degrades to bundle.

    Exercises the SIZE trigger independently of the media trigger — an images-only
    grid with a tiny ``--max-inline-mb`` override must fire the guardrail on total
    bytes (not media type): NO ``data:image/`` payload, a relative ``./assets/``
    bundle written INSTEAD, and a warning mentioning size / --max-inline-mb + the
    folder-bundle fallback. Closes the untested size-threshold branch of SC-3.
    """
    out = tmp_path / "out"
    # The 6 dense fixture PNGs total ~600 bytes; 0.0001 MB (~105 bytes) forces the
    # size branch to trip without any video cell involved.
    result = _freeze_args(dense_sample_folder, out, "--inline", "--max-inline-mb", "0.0001")

    assert result.exit_code == 0, result.output

    html = (out / "grid-output" / "index.html").read_text(encoding="utf-8")
    assert "data:image/" not in html, "oversized grid must NOT inline base64"

    assert (out / "grid-output" / "assets").is_dir(), (
        "oversized grid must degrade to the folder bundle"
    )
    assert "./assets/" in html, "degraded page must reference the relative bundle"

    warning = result.output.lower()
    assert ("size" in warning) or ("max-inline-mb" in warning)
    assert "folder bundle" in warning
