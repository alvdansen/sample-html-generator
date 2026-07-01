"""Render-layer correctness: distinct placeholders, the resolver seam, and the
prompt-XSS / posix-src security controls.

The renderer is pure, so these tests feed it hand-built ``GridModel`` instances
(no disk, no parser) and assert on the emitted HTML string. They lock in:
  * missing vs broken cells differ on class AND glyph (D-09/D-10);
  * ``render`` is resolver-agnostic — identical DOM under two resolvers, differing
    only in ``src``/``href`` values (the P4 Served / P5 Inline swap proof);
  * prompt text is HTML-escaped across body/title/alt (Pitfall 4 / T-1-01);
  * every emitted ``src`` uses forward slashes only (Pitfall 5 / T-1-03).
"""
from __future__ import annotations

import re
from pathlib import Path

from sample_grid.core.grid import build_grid
from sample_grid.core.model import (
    Cell,
    CellState,
    GridConfig,
    GridModel,
    Sample,
)
from sample_grid.render.renderer import render
from sample_grid.render.resolver import RelativeResolver


class FakeResolver:
    """A resolver that returns a constant sentinel URL for every sample.

    Used to prove ``render`` never depends on *how* a URL is produced — only the
    ``src``/``href`` values may differ between this and ``RelativeResolver``.
    """

    def url(self, s: Sample) -> str:
        return "SENTINEL_URL"


def _sample(rel_id: str, *, step: int, prompt: str) -> Sample:
    return Sample(
        id=rel_id,
        path=Path(rel_id),
        media_type="image",
        dims={"step": step, "prompt": prompt},
    )


# Strip every src/href VALUE so two renders can be compared for DOM structure.
_SRC_HREF = re.compile(r'\b(src|href)="[^"]*"')
_SRC = re.compile(r'\bsrc="([^"]*)"')


def _normalize_urls(html: str) -> str:
    return _SRC_HREF.sub(r'\1="@@"', html)


def _css_rule(html: str, selector: str) -> str:
    """Return the body of the first top-of-line CSS rule for ``selector``.

    Anchored to start-of-line (MULTILINE) so the main ``.col-header { … }`` block
    is matched and the later compound rules (e.g. ``.is-scrolled-y .col-header``)
    are not.
    """
    m = re.search(
        r"^" + re.escape(selector) + r"\s*\{([^}]*)\}", html, re.MULTILINE
    )
    assert m, f"no CSS rule for {selector!r} in rendered output"
    return m.group(1)


def test_sticky_headers_present() -> None:
    """GRID-03: the inlined CSS pins column headers to the top, row headers to
    the left, and the corner to BOTH — layered corner(30) > headers(20)."""
    grid = GridModel(
        row_values=[100],
        col_values=["p1"],
        cells=[[Cell(CellState.MISSING)]],
        cell_ar=(16, 9),
    )
    html = render(grid, RelativeResolver())

    col = _css_rule(html, ".col-header")
    assert "position: sticky" in col and "top: 0" in col

    row = _css_rule(html, ".row-header")
    assert "position: sticky" in row and "left: 0" in row

    corner = _css_rule(html, ".corner")
    assert "position: sticky" in corner
    assert "top: 0" in corner and "left: 0" in corner

    # Z-index layering: corner above the header axes, toggle bar above the grid.
    assert "--z-header: 20" in html
    assert "--z-corner: 30" in html
    assert "--z-toggle: 40" in html


def test_prompt_truncation_title() -> None:
    """GRID-04: a long prompt truncates to one line (ellipsis class) while the
    FULL text is exposed in both ``title`` and ``aria-label`` on the header."""
    long_prompt = "a sweeping cinematic establishing shot of " * 4
    grid = GridModel(
        row_values=[100],
        col_values=[long_prompt],
        cells=[[Cell(CellState.MISSING)]],
        cell_ar=(16, 9),
    )
    html = render(grid, RelativeResolver())

    # The header carries the truncation class (which owns the ellipsis CSS)...
    assert "col-header" in html
    assert "text-overflow: ellipsis" in _css_rule(html, ".col-header")
    # ...and the full prompt is available, unclipped, on hover + to a SR.
    assert f'title="{long_prompt}"' in html
    assert f'aria-label="{long_prompt}"' in html


def test_missing_vs_broken_distinct() -> None:
    """D-09/D-10: missing and broken cells differ on class AND glyph, and neither
    is a clickable media cell (no <a>, no <img>)."""
    grid = GridModel(
        row_values=[100],
        col_values=["p1", "p2"],
        cells=[[
            Cell(CellState.BROKEN, sample=_sample("p1/bad.png", step=100, prompt="p1")),
            Cell(CellState.MISSING),
        ]],
        cell_ar=(16, 9),
    )
    html = render(grid, RelativeResolver())

    assert "cell--missing" in html
    assert "cell--broken" in html
    assert "—" in html  # em dash — (missing glyph)
    assert "⚠" in html  # warning ⚠ (broken glyph)
    # A placeholder is never a clickable / media cell.
    assert "<img" not in html
    assert "<a " not in html
    # The broken cell surfaces the offending filename for hover diagnosis.
    assert "bad.png" in html


def test_renderer_resolver_agnostic() -> None:
    """Seam proof (P4/P5): two resolvers yield byte-identical DOM apart from
    the src/href attribute values."""
    grid = GridModel(
        row_values=[100],
        col_values=["p1", "p2"],
        cells=[[
            Cell(CellState.POPULATED, sample=_sample("p1/img.png", step=100, prompt="p1")),
            Cell(CellState.MISSING),
        ]],
        cell_ar=(16, 9),
    )

    relative = render(grid, RelativeResolver())
    fake = render(grid, FakeResolver())

    # The raw outputs differ only because the URL values differ...
    assert relative != fake
    # ...and become identical once those values are normalized away.
    assert _normalize_urls(relative) == _normalize_urls(fake)


def test_prompt_html_escaped(xss_prompt_index) -> None:
    """Pitfall 4 / T-1-01: HTML metacharacters in a prompt render as literal text
    in body, title, and alt — never as live markup."""
    grid = build_grid(xss_prompt_index, GridConfig())
    html = render(grid, RelativeResolver())

    assert "&lt;b&gt;" in html  # <b> escaped
    assert "&amp;" in html      # & escaped
    assert "&#34;" in html      # " escaped (markupsafe emits &#34;, not &quot;)
    assert "<b>" not in html    # no raw injected element anywhere


def test_seed_variance_marker() -> None:
    """D-09: a GridModel with seed_varies=True renders an in-page seed-variance
    banner + a per-cell alternate badge on multi-seed cells; seed_varies=False
    renders no banner. Any dynamic seed value flows through the autoescaped
    ``{{ }}`` path (never string-concatenated in Python)."""
    # A metacharacter alternate-seed value proves the badge is autoescaped.
    evil_seed = '<x>&"'
    populated = Cell(
        CellState.POPULATED,
        sample=_sample("p1/img.png", step=100, prompt="p1"),
        has_alternates=True,
        alternate_seeds=[evil_seed, 7],
    )
    grid = GridModel(
        row_values=[100],
        col_values=["p1"],
        cells=[[populated]],
        cell_ar=(16, 9),
        seed_varies=True,
    )
    html = render(grid, RelativeResolver())

    # The banner marker is present (stable machine hook + human-readable text).
    assert "data-seed-variance" in html
    assert "Seeds vary" in html
    # The populated multi-seed cell carries an alternate badge.
    assert "Alternate seeds" in html
    # The metacharacter seed value is HTML-escaped, never live markup.
    assert "&lt;x&gt;" in html
    assert "<x>" not in html

    # seed_varies=False → no banner marker at all.
    grid_off = GridModel(
        row_values=[100],
        col_values=["p1"],
        cells=[[Cell(CellState.MISSING)]],
        cell_ar=(16, 9),
        seed_varies=False,
    )
    html_off = render(grid_off, RelativeResolver())
    assert "data-seed-variance" not in html_off
    assert "Seeds vary" not in html_off


def test_toggle_js_inlined_offline_safe() -> None:
    """The theme/density toggle JS is inlined verbatim (localStorage + data-set
    wiring intact) AND the artifact carries NO server wiring — proving it is a
    self-contained, file://-openable P1 page (live=False, no EventSource)."""
    grid = GridModel(
        row_values=[100],
        col_values=["p1"],
        cells=[[Cell(CellState.MISSING)]],
        cell_ar=(16, 9),
    )
    html = render(grid, RelativeResolver())

    # The inlined script must be present and functional (not entity-mangled).
    assert "localStorage" in html
    assert "data-set" in html
    assert "addEventListener" in html
    # A trusted static <script> must survive autoescape verbatim — the JS uses
    # `scrollTop > 0`, which would be broken as `&gt;` inside a <script>.
    assert "scrollTop > 0" in html
    assert "&gt; 0" not in html
    # No server / live-reload surface leaks into the Phase-1 artifact.
    assert "EventSource" not in html
    assert "LIVE_ENDPOINT" not in html


def test_click_to_open_anchor() -> None:
    """D-12: every POPULATED cell is an <a target=_blank rel=noopener noreferrer>
    (click-to-open native), while missing/broken placeholders are NOT anchors."""
    grid = GridModel(
        row_values=[100],
        col_values=["p1", "p2", "p3"],
        cells=[[
            Cell(CellState.POPULATED, sample=_sample("p1/img.png", step=100, prompt="p1")),
            Cell(CellState.MISSING),
            Cell(CellState.BROKEN, sample=_sample("p3/bad.png", step=100, prompt="p3")),
        ]],
        cell_ar=(16, 9),
    )
    html = render(grid, RelativeResolver())

    anchors = re.findall(r"<a\b[^>]*>", html)
    # Exactly the one populated cell is an anchor.
    assert len(anchors) == 1
    a = anchors[0]
    assert 'target="_blank"' in a
    assert 'rel="noopener noreferrer"' in a
    # The placeholders render but never as clickable media.
    assert "cell--missing" in html and "cell--broken" in html


def test_src_posix_separators() -> None:
    """Pitfall 5 / T-1-03: every emitted src uses forward slashes only."""
    grid = GridModel(
        row_values=[100],
        col_values=["p1"],
        cells=[[
            Cell(CellState.POPULATED, sample=_sample("p1/img.png", step=100, prompt="p1")),
        ]],
        cell_ar=(16, 9),
    )
    html = render(grid, RelativeResolver())

    srcs = _SRC.findall(html)
    assert srcs, "expected at least one src attribute"
    assert all("\\" not in s for s in srcs)
    assert any(s.startswith("./assets/") for s in srcs)
