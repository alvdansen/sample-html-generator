<!-- GSD:project-start source:PROJECT.md -->
## Project

**Sample HTML Generator — Model Comparison Grid Builder**

A video-first comparison grid builder for evaluating generative model samples. You point it at a training/inference output folder; it auto-detects the per-sample metadata and renders an HTML grid — by default **training-steps × prompts**, to watch a single LoRA/checkpoint evolve over a run, with axes configurable for model/seed/checkpoint comparisons. Cells render **video (.mp4/.webm) or images**. Built for ML practitioners who run seed-locked, non-cherry-picked ablation evals and currently rebuild this grid by hand every training run.

**Core Value:** Point it at a folder of model samples and get a correct, comparable grid — live during training and frozen for sharing — without rebuilding the tool from scratch each time.

### Constraints

- **Distribution**: Must work as a GitHub clone-and-run repo against arbitrary local folders, AND as a Hugging Face Space demo — Why: dual audience (the user's own workflow + public release).
- **Media**: Must handle video (.mp4/.webm) as first-class cells, not just images — Why: the user's primary work is video LoRA training.
- **Live refresh**: Watch mode requires a running local process (server) to push updates to the browser — Why: a static HTML file can't auto-refresh on new samples.
- **Portability of export**: The frozen standalone `.html` must be openable with no server — Why: it's the shareable artifact (README, Space, archive).
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## TL;DR Recommendation
- **Shared core** (used by every mode): a metadata-detection module + a **Jinja2** template/render module + a "build" function that emits the grid HTML. This is the heart of the tool.
- **Local live server**: **FastAPI + Uvicorn** serving the rendered page and a **Server-Sent Events (SSE)** endpoint, with **watchfiles** watching the output folder and pushing browser refreshes.
- **Freeze/export**: render the same Jinja2 templates with media inlined as **base64 data URIs** (single-file, default) or **relative-asset bundle** (folder, for large video sets).
- **HF Space**: a **Static SDK Space** that serves the freeze/export output. No Python runtime on the Space — same templates, same build code, zero cold start, free.
- **Packaging**: **uv + pyproject.toml**, with a `pip install` fallback and a console-script entry point.
## Recommended Stack
### Core Technologies
| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| **Python** | 3.11+ | Runtime | 3.11/3.12 are the stable baseline; `watchfiles`, `fastapi`, `uv` all ship wheels. Require ≥3.11 for `tomllib` + better asyncio. |
| **FastAPI** | 0.138.2 | Local web server (serve page + SSE reload endpoint + static media) | Async-native, built-in `StaticFiles`, native streaming responses for SSE, trivial Jinja2 integration. The server is tiny — FastAPI gives clean DX with negligible weight. |
| **Uvicorn** | 0.49.0 | ASGI server that runs FastAPI | Standard ASGI server; `uvicorn app:api` one-liner. Use `--reload` only for *your* dev, not for the user (the tool does its own watching). |
| **watchfiles** | 1.2.0 | Watch the model-output folder; trigger grid rebuild + browser refresh | Rust/Notify-backed OS filesystem events (not polling), **async API** that drops straight into the FastAPI event loop, debouncing built in. Faster and simpler to wire to SSE than watchdog. |
| **Jinja2** | 3.1.6 | Template the grid HTML for **both** the live page and the frozen export | Single source of truth for grid markup. Renders server-side page and the standalone file from the *same* templates. f-strings become unmaintainable for nested `steps × prompts` grids; a JS framework breaks the no-build, single-file export goal. |
### Supporting Libraries
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| **Typer** | 0.20.x | CLI (`watch`, `build`, `freeze` subcommands, flags) | Built by the FastAPI author, sits on Click, type-hint driven. Clean way to expose `watch`/`build`/`freeze`. (Plain **Click** is the equivalent fallback.) |
| **sse-starlette** | 3.x | Helper for the SSE endpoint | Optional convenience over hand-rolling a `StreamingResponse`. Gives `EventSourceResponse` with keep-alive pings + clean disconnect handling. Skip it if you prefer zero extra deps — raw Starlette streaming works. |
| **Pillow** | 11.x | Read image dimensions / generate poster thumbnails | Only if you want cell aspect-ratios or image poster frames. Not required for raw display. |
| **(stdlib) base64, mimetypes, pathlib, json, csv** | — | Inline media for the frozen file; parse sidecar metadata | No third-party dep needed for the export encoder or the JSON/CSV/filename metadata parsers. |
| **(vanilla JS, no library)** | — | Hover-to-play, sync-loop, scrub, SSE reload listener | A few dozen lines of plain JS: `IntersectionObserver` for hover/visible-play, `<video muted loop preload="metadata">`, and an `EventSource` that reloads on SSE message. No React/Svelte/Vue. |
### Development Tools
| Tool | Purpose | Notes |
|------|---------|-------|
| **uv** | 0.11.26 | Dependency + venv + run manager | The 2025/2026 standard. `uv sync` then `uv run grid watch ./outputs`. Make this the documented clone-and-run path; keep a `pip install -e .` fallback in the README. |
| **ruff** | latest | Lint + format | One tool replaces flake8 + black + isort. |
| **HF Hub Static Space** | — | Hosted demo | `sdk: static` in `README.md` front-matter; commit `index.html` + small sample `assets/`. Optionally run a build step at deploy. |
## Installation
# Clone-and-run (recommended path, documented for users)
# pip fallback (no uv)
## The Live-Reload Mechanism (decision detail)
## Standalone HTML Export with VIDEO (decision detail)
| Mode | Output | Best for | Mechanism |
|------|--------|----------|-----------|
| **Single-file (default)** | One `.html` | README embeds, email, dropping into a Space, true portability | Each video/image inlined as `data:video/mp4;base64,...` |
| **Asset bundle** | `index.html` + `assets/` folder | Large video sets, the HF Space demo | Relative-path `<video src="assets/...">` |
- Chromium / Firefox: **512 MB per data URL** (Firefox raised 256KB → 32MB in 97, → 512MB in 136).
- Safari/WebKit: **2 GB per data URL**.
- Base64 inflates payload by **~33%**.
- **iOS Safari is unreliable** with base64 `<video>` (can fail silently) — a known gotcha.
- Base64 is *per-resource* under the limit, but **total page weight** is the real constraint: 30 cells × 5 MB ≈ 150 MB → ~200 MB single file = slow to parse/load. Add a configurable **size threshold** (e.g. warn/auto-switch to asset-bundle mode above ~50–100 MB total).
- Default `freeze` = single-file base64 (matches "self-contained standalone `.html`" requirement literally). **[Decision 2026-07-03 — superseded for Phase 5]** ROADMAP Phase 5 SC3 reverses this polarity: the **folder bundle is the default** and single-file base64 is the **opt-in `--inline` flag** (with a size guardrail, images/tiny grids only). Rationale: video-heavy base64 pages blow the ~50–100 MB page-weight ceiling and are unreliable on iOS Safari, so the safe default for this video-first tool is the relative-asset folder bundle. This decision-log entry keeps the original recommendation for history while recording that the shipped default is folder-bundle.
- `freeze --assets external` = `index.html` + `assets/` bundle; still `file://`-openable, far lighter, and the correct format to commit to the **HF Static Space** (keep sample videos small to stay under the Space LFS limit).
- Relative-path `<video>` plays fine from `file://`; base64 always plays from `file://`. (Note: `fetch()`/XHR from `file://` is blocked in Chrome — so the grid data must be inlined into the page, not loaded via JS fetch.)
## Hugging Face Space: which SDK (decision detail)
| SDK | Fit | Verdict |
|-----|-----|---------|
| **Static** | Serves plain HTML (or a built `npm run build` output). Exactly what the tool's `freeze`/`build` already produces. | **Use this.** Zero Python runtime → no cold start, free, instant. Share the *same* Jinja2 build code; the Space hosts the bundled-sample build output. |
| **Docker** | Could run the full FastAPI server to show the live UI. | Overkill. Adds container build + cold starts for no gain — live watch is impossible on a hosted Space (it can't see a user's local folder). Only consider if you want the server UI itself demoed interactively. |
| **Gradio** | Would require rebuilding the grid inside `gr.Gallery`/`gr.HTML` components. | **Avoid.** Diverges the Space from the local tool, constrains the custom video grid layout, and adds a heavyweight runtime. The Gallery component gives little layout control for a true `steps × prompts` matrix. |
## Local Tool vs HF Space: SAME vs DIVERGE
| Component | Local tool | HF Space | Shared? |
|-----------|-----------|----------|---------|
| Metadata auto-detect (filename/subfolder/sidecar) | Yes | Yes (run at build) | **SAME** |
| Jinja2 grid templates + CSS + vanilla JS | Yes | Yes | **SAME** |
| `build`/`freeze` exporter | Yes | Yes (produces the committed artifact) | **SAME** |
| FastAPI + Uvicorn server | Yes | No | **DIVERGE** (local only) |
| watchfiles folder watcher | Yes | No (can't see local folders) | **DIVERGE** (local only) |
| SSE live-reload endpoint + `EventSource` | Yes | No (static page, no reload listener) | **DIVERGE** (local only) |
| Runtime | Python process | None (static files) | **DIVERGE** |
## Alternatives Considered
| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| FastAPI + Uvicorn | **Starlette only** (no FastAPI) | If you want the absolute minimum dependency surface — Starlette has `StaticFiles` + streaming SSE and is all the server actually needs. FastAPI adds DX/structure for ~no cost; pick Starlette if you value minimalism over ergonomics. |
| FastAPI + SSE | **Flask + SSE** | Only if the team is WSGI-locked. Flask SSE needs streaming generators + threaded mode and doesn't pair as cleanly with async watchfiles. No reason to choose it greenfield. |
| watchfiles | **watchdog 6.0.0** | If you need a sync/callback model, broader OS-quirk coverage, or already depend on it. Mature and reliable, just more wiring for an async server. |
| SSE | **WebSocket** | Only if you later add browser→server control (e.g. UI sends "pin this cell" or filter commands back to the server). Until then SSE is simpler. |
| Single-file base64 export | **Asset-bundle export** | Large video sets, HF Space hosting, or when total single-file size exceeds ~50–100 MB. |
| Jinja2 | **f-strings** | Only for trivial one-off snippets. Not for the grid — nested-loop HTML in f-strings is unmaintainable and escaping-prone. |
## What NOT to Use
| Avoid | Why | Use Instead |
|-------|-----|-------------|
| **Gradio** | Constrained components (`gr.Gallery` gives little layout control for a real `steps × prompts` matrix), heavy runtime, and forces the Space to diverge from the local tool. The one thing it'd enable (interactive hosted UI) doesn't include live watch anyway. | Custom Jinja2 grid + Static Space |
| **A JS framework (React/Svelte/Vue) for the grid** | Adds a build step, breaks the "single self-contained `.html`, clone-and-run, no Node" goals, and is unnecessary for a static grid. | Jinja2 server-render + a few dozen lines of vanilla JS |
| **f-strings for the grid HTML** | Unreadable nested loops, manual escaping, easy to produce broken markup. | Jinja2 templates |
| **uvicorn `--reload` as the user-facing watch** | That reloads *Python code*, not the content folder; it's a dev-of-the-tool feature, not the product's watch mode. | watchfiles watching the **output folder** + SSE |
| **Polling-based file watching (manual `os.scandir` loop)** | CPU waste, latency, missed events on large dirs. | watchfiles (OS events) |
| **Committing large videos to the Space repo** | Spaces hit a git-LFS storage cap (commonly ~1 GB default; 50 GB hard per-file). Big sample videos blow it. | Keep Space sample clips small/short; use the asset-bundle export with compressed samples |
| **Embedding video via `fetch()` from `file://`** | Chrome blocks XHR/fetch from `file://`, so a frozen page that lazy-loads cells via JS will break offline. | Inline all data into the page (base64 or relative `<video src>`) — no runtime fetch |
## Stack Patterns by Variant
- FastAPI + Uvicorn + watchfiles `awatch` + SSE; Jinja2 page rendered with `live=True`.
- Browser holds an `EventSource`; new sample lands → rebuild → SSE ping → reload/cell-swap.
- No server. Run detect → render Jinja2 with `live=False` → write file(s).
- Default single-file base64; `--assets external` for large/video-heavy sets.
- Run the asset-bundle build with bundled small samples → commit `index.html` + `assets/` → `sdk: static`.
- Identical templates/JS as local, minus the SSE wiring.
## Version Compatibility
| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| fastapi 0.138.2 | starlette 1.3.1, uvicorn 0.49.0 | FastAPI pins a Starlette range; install FastAPI and let it resolve Starlette rather than pinning Starlette yourself. |
| watchfiles 1.2.0 | Python 3.11–3.13, asyncio loop | `awatch` is async-native; no thread bridge needed under Uvicorn. |
| jinja2 3.1.6 | fastapi (Jinja2Templates) | FastAPI's `Jinja2Templates` wraps this directly; same templates reused by the offline builder. |
| sse-starlette 3.x | starlette/fastapi current | Optional; provides `EventSourceResponse`. Drop-in over raw `StreamingResponse`. |
| uv 0.11.26 | pyproject.toml (PEP 621) | `uv sync` / `uv run`; also reads the same `[project]` table pip uses, so the pip fallback stays valid. |
## Sources
- **PyPI JSON API** (live, 2026-06-30) — exact current versions: fastapi 0.138.2, uvicorn 0.49.0, starlette 1.3.1, jinja2 3.1.6, watchdog 6.0.0, watchfiles 1.2.0, gradio 6.19.0, uv 0.11.26. **HIGH**
- **MDN — data: URLs** (developer.mozilla.org/en-US/docs/Web/URI/Reference/Schemes/data) — per-data-URL size limits (Chromium/FF 512MB, Safari 2GB), ~33% base64 inflation. **HIGH**
- **HF Hub docs — Spaces SDKs (Gradio / Static / Docker)** (huggingface.co/docs/hub/spaces-overview, /spaces-sdks-docker) — Static supports plain HTML + optional build step; Docker for custom servers; Gradio component-bound. **HIGH**
- **HF Hub docs — Storage limits** (huggingface.co/docs/hub/storage-limits) — repo/LFS caps (Spaces commonly ~1GB default, 50GB hard per-file). **MEDIUM** (default Space cap reported via forums; treat as "keep samples small").
- **watchfiles docs + GitHub** (watchfiles.helpmanual.io, github.com/samuelcolvin/watchfiles) — Rust/Notify OS events, async `awatch`, debouncing, watchgod→watchfiles rename to avoid watchdog confusion. **HIGH**
- **SSE vs WebSocket for live-reload** (blog.ngzhian.com WS→SSE migration; ably.com, websocket.org comparisons) — SSE is the right one-way live-reload primitive. **HIGH**
- **Gradio docs — Gallery / Controlling Layout / Custom HTML** (gradio.app/docs/gradio/gallery, /guides/controlling-layout) — Gallery/layout customization is limited; `gr.HTML` escape hatch exists but defeats the purpose. **MEDIUM-HIGH**
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
