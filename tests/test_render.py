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
    assert "&quot;" in html     # " escaped (attribute context)
    assert "<b>" not in html    # no raw injected element anywhere


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
