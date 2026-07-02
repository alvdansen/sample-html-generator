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


def _sample(
    rel_id: str, *, step: int, prompt: str, media_type: str = "image"
) -> Sample:
    return Sample(
        id=rel_id,
        path=Path(rel_id),
        media_type=media_type,
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


def test_sync_toggle_present() -> None:
    """MEDIA-03/D-06/D-07: the Sync toggle renders in the control bar with both
    segments (Independent default · Synced) using the same data-set="key:value"
    pattern as theme/density, a persistent ``SYNCED`` badge for the D-07
    visual-obviousness indicator, and ``<html data-sync="independent">`` so the
    grid defaults to low-friction Independent playback."""
    grid = GridModel(
        row_values=[100],
        col_values=["p1"],
        cells=[[Cell(CellState.MISSING)]],
        cell_ar=(16, 9),
    )
    html = render(grid, RelativeResolver())

    # Both sync segments render with the reused data-set toggle pattern.
    assert 'data-set="sync:independent"' in html
    assert 'data-set="sync:synced"' in html
    assert html.count('data-set="sync:') == 2
    # The Sync label + the D-07 SYNCED badge are present.
    assert ">Sync<" in html
    assert "sync-badge" in html
    assert "SYNCED" in html
    # Default is Independent (baked into <html>).
    assert 'data-sync="independent"' in html


def test_global_controls_present() -> None:
    """MEDIA-04/D-09: the control bar carries a PROMINENT Pause-all plus a
    SECONDARY Play-visible plain action button (exact copy), and NO whole-grid
    play-everything control. Pause-all is the weightier ``--primary`` variant."""
    grid = GridModel(
        row_values=[100],
        col_values=["p1"],
        cells=[[Cell(CellState.MISSING)]],
        cell_ar=(16, 9),
    )
    html = render(grid, RelativeResolver())

    # Both action controls render with stable ids + exact UI-SPEC copy.
    assert 'id="pause-all"' in html
    assert 'id="play-visible"' in html
    assert "Pause all" in html
    assert "Play visible" in html
    # They are .control-btn action buttons (not data-set toggles).
    assert 'class="control-btn' in html
    assert 'data-set="pause' not in html
    assert 'data-set="play' not in html
    # Pause-all is the prominent (weightier) primary control.
    assert "control-btn--primary" in html
    # D-09: NO whole-grid play-everything control exists.
    assert 'id="play-all"' not in html
    assert "Play all" not in html


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


# ── Video cells (Phase 3 / MEDIA-01 · MEDIA-05) ────────────────────────────


def _video_grid() -> GridModel:
    """A one-cell grid whose single POPULATED cell is a VIDEO sample."""
    return GridModel(
        row_values=[100],
        col_values=["p1"],
        cells=[[
            Cell(
                CellState.POPULATED,
                sample=_sample("p1/clip.mp4", step=100, prompt="p1", media_type="video"),
            ),
        ]],
        cell_ar=(16, 9),
    )


def test_video_cell_render_contract() -> None:
    """MEDIA-01/D-01/D-10: a POPULATED video Sample renders as a
    ``<div class="cell cell--video">`` carrying a lazy ``data-src`` first-frame
    poster fragment (``#t=0.001``), and a ``<video muted playsinline loop
    preload="none">`` with NO eager ``src`` — the Plan-02 lazy-load contract."""
    html = render(_video_grid(), RelativeResolver())

    assert 'class="cell cell--video"' in html
    # Poster is the video's OWN first frame via the #t= media fragment on data-src.
    m = re.search(r'data-src="([^"]*)"', html)
    assert m, "expected a data-src on the video cell"
    assert m.group(1).endswith("#t=0.001")

    # The <video> element carries the lifecycle attrs and NO eager src.
    vm = re.search(r"<video\b[^>]*>", html)
    assert vm, "expected a <video> element"
    vtag = vm.group(0)
    assert "muted" in vtag
    assert "playsinline" in vtag
    assert "loop" in vtag
    assert 'preload="none"' in vtag
    assert 'src="' not in vtag  # client attaches src on first play (Plan 02)


def test_video_play_overlay_and_popout() -> None:
    """D-04/D-05: a resting video cell carries a ▶ play overlay and a ⧉ pop-out
    ``<a target=_blank rel=noopener noreferrer>`` opening the CLEAN clip url (no
    ``#t=`` fragment)."""
    html = render(_video_grid(), RelativeResolver())

    assert 'class="cell__play"' in html
    assert "▶" in html

    pm = re.search(r'<a\b[^>]*class="cell__popout"[^>]*>', html)
    assert pm, "expected a cell__popout anchor"
    a = pm.group(0)
    assert 'target="_blank"' in a
    assert 'rel="noopener noreferrer"' in a
    hm = re.search(r'href="([^"]*)"', a)
    assert hm, "pop-out must carry an href"
    assert "#t=" not in hm.group(1)  # pop-out opens the raw clip, not the poster


def test_video_prompt_html_escaped(xss_video_index) -> None:
    """T-3-01: HTML metacharacters in a VIDEO sample's prompt render as literal
    text (autoescape holds on the video branch exactly as on the image branch)."""
    grid = build_grid(xss_video_index, GridConfig())
    html = render(grid, RelativeResolver())

    assert "&lt;b&gt;" in html
    assert "&amp;" in html
    assert "&#34;" in html
    assert "<b>" not in html  # never a raw injected element


def test_mixed_grid_image_cell_unchanged() -> None:
    """MEDIA-05: in a mixed grid the image cell stays a plain ``<a><img>`` with
    NO pop-out and NO play overlay; only the video cell carries those."""
    grid = GridModel(
        row_values=[100],
        col_values=["p1", "p2"],
        cells=[[
            Cell(
                CellState.POPULATED,
                sample=_sample("p1/pic.png", step=100, prompt="p1", media_type="image"),
            ),
            Cell(
                CellState.POPULATED,
                sample=_sample("p2/clip.mp4", step=100, prompt="p2", media_type="video"),
            ),
        ]],
        cell_ar=(16, 9),
    )
    html = render(grid, RelativeResolver())

    # The image cell is still a plain anchor wrapping an <img>.
    assert '<a class="cell"' in html
    assert "<img" in html
    # Exactly one video cell, and the pop-out / play overlay belong to it alone.
    assert 'class="cell cell--video"' in html
    assert html.count('class="cell__popout"') == 1
    assert html.count('class="cell__play"') == 1


def test_player_js_inlined_and_offline_safe() -> None:
    """MEDIA-01/MEDIA-05: the Plan-02 player module is inlined verbatim into the
    artifact — the runtime hooks used by the manual M1/M5 protocols
    (``IntersectionObserver`` lazy-load, the ``window.__players`` decoder counter,
    the ``data-blocked`` poster-fallback marker, and the ``forceRejectPlay`` debug
    hook) are all present — WHILE the page stays ``file://``-safe with no server
    surface (no ``EventSource``, no ``fetch(``). Mirrors
    ``test_toggle_js_inlined_offline_safe`` for the video player."""
    html = render(_video_grid(), RelativeResolver())

    # The player lifecycle is shipped in the inlined JS (observable runtime hooks).
    assert "IntersectionObserver" in html
    assert "__players" in html
    assert "data-blocked" in html
    assert "forceRejectPlay" in html

    # Regression tie to the Plan-01 lazy-load markup the player consumes.
    assert "data-video" in html
    assert 'preload="none"' in html

    # No server / live-reload / fetch surface leaks into the artifact.
    assert "EventSource" not in html
    assert "fetch(" not in html


# ── Phase 4 render-layer foundations (RUN-02 / RUN-04) ──────────────────────


def test_served_resolver_url() -> None:
    """RUN-02: ServedResolver maps a Sample id to a ``/media/<url-encoded posix
    id>`` URL — forward slashes preserved, each segment url-encoded (spaces →
    %20) via ``quote(s.id, safe="/")``. This is the P4 served-resolver seam
    swapped behind the same AssetResolver Protocol as RelativeResolver."""
    from sample_grid.render.resolver import ServedResolver

    s = _sample("a lake/step_600.mp4", step=600, prompt="a lake", media_type="video")
    assert ServedResolver().url(s) == "/media/a%20lake/step_600.mp4"


def test_live_flag_gates_eventsource() -> None:
    """RUN-02: render(live=True) injects the LIVE_ENDPOINT constant that the live
    layer's EventSource wiring keys on; render(live=False) emits no live wiring
    (the Phase-1/3 file://-safe artifact stays server-free)."""
    grid = GridModel(
        row_values=[100],
        col_values=["p1"],
        cells=[[Cell(CellState.MISSING)]],
        cell_ar=(16, 9),
    )
    live_html = render(grid, RelativeResolver(), live=True)
    static_html = render(grid, RelativeResolver(), live=False)

    assert "LIVE_ENDPOINT" in live_html
    assert "LIVE_ENDPOINT" not in static_html


def test_cell_fragment_matches_full_render() -> None:
    """RUN-04 anti-drift: a single cell rendered in isolation via
    render_cell_fragment is byte-identical (as a contiguous substring) to that
    same cell inside the full-page render — proving both draw from the one shared
    cell.j2 macro so a live-patched cell can never drift from a full render."""
    from sample_grid.render.renderer import render_cell_fragment

    grid = GridModel(
        row_values=[100],
        col_values=["p1", "p2"],
        cells=[[
            Cell(
                CellState.POPULATED,
                sample=_sample("p1/clip.mp4", step=100, prompt="p1", media_type="video"),
            ),
            Cell(
                CellState.POPULATED,
                sample=_sample("p2/pic.png", step=100, prompt="p2", media_type="image"),
            ),
        ]],
        cell_ar=(16, 9),
    )
    full = render(grid, RelativeResolver())
    resolver = RelativeResolver()

    # POPULATED video cell at (0, 0).
    video_item = {"prompt": "p1", "cell": grid.cells[0][0]}
    video_frag = render_cell_fragment(video_item, 0, 0, 100, "p1", resolver)
    assert video_frag.strip() in full

    # POPULATED image cell at (0, 1).
    image_item = {"prompt": "p2", "cell": grid.cells[0][1]}
    image_frag = render_cell_fragment(image_item, 0, 1, 100, "p2", resolver)
    assert image_frag.strip() in full
