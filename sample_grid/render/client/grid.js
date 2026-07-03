/* Phase-1 client JS — theme/density toggle + sticky-shadow cue. Vanilla, no
   dependency, MUST run from file:// with no server. Phase 1 renders live=False,
   so there is deliberately no live-reload / no server wiring here (that arrives
   in Phase 4). The dark/comfortable defaults live in the CSS :root, so a
   JS-disabled page still renders correctly — this script only adds the toggle
   and the scroll-shadow polish. */
(function () {
  "use strict";
  var root = document.documentElement;
  var STORE = { theme: "sg-theme", density: "sg-density", sync: "sg-sync" };

  // Synced mode reads straight off the data-sync attribute the [data-set] loop
  // flips; anything other than "synced" is Independent (the resting default).
  var isSynced = function () { return root.getAttribute("data-sync") === "synced"; };

  // Restore the persisted theme/density (if any) on load.
  Object.keys(STORE).forEach(function (key) {
    var saved = null;
    try { saved = localStorage.getItem(STORE[key]); } catch (e) {}
    if (saved) root.setAttribute("data-" + key, saved);
  });

  // Reflect the active value on every segment of a group via aria-pressed.
  function syncPressed(key) {
    var current = root.getAttribute("data-" + key);
    var segs = document.querySelectorAll('[data-set^="' + key + ':"]');
    for (var i = 0; i < segs.length; i++) {
      var value = segs[i].getAttribute("data-set").split(":")[1];
      segs[i].setAttribute("aria-pressed", String(value === current));
    }
  }
  syncPressed("theme");
  syncPressed("density");
  syncPressed("sync");

  // Wire each [data-set="key:value"] button: set the attribute, persist, sync.
  document.querySelectorAll("[data-set]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var parts = btn.getAttribute("data-set").split(":");
      var key = parts[0], value = parts[1];
      root.setAttribute("data-" + key, value);
      try { localStorage.setItem(STORE[key], value); } catch (e) {}
      syncPressed(key);
    });
  });

  // Sticky-shadow cue: toggle .is-scrolled-x / .is-scrolled-y on the scroll
  // container once content scrolls under the headers. Only as a motion-safe
  // enhancement; the always-on hairline is the CSS-only fallback.
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: no-preference)").matches) {
    var scroller = document.querySelector(".grid-scroll");
    if (scroller) {
      var ticking = false;
      var update = function () {
        scroller.classList.toggle("is-scrolled-y", scroller.scrollTop > 0);
        scroller.classList.toggle("is-scrolled-x", scroller.scrollLeft > 0);
        ticking = false;
      };
      scroller.addEventListener("scroll", function () {
        if (!ticking) { ticking = true; requestAnimationFrame(update); }
      });
    }
  }

  // ── Video player lifecycle (Phase 3, MEDIA-01 / MEDIA-05) ──────────────────
  // IntersectionObserver-gated decoder lifecycle: attach `src` + paint the
  // first-frame poster on ENTER, DETACH `src` to free the WebMediaPlayer on
  // EXIT. Chromium hard-caps live players (75 desktop / 40 mobile) and blacks
  // out cells past the cap, so detaching on exit is MANDATORY for a real 10x8
  // grid. Per-cell click / Space-Enter toggles play-pause of THAT cell (D-03,
  // never all-playing); a rejected play() degrades to the poster + `data-blocked`
  // (D-11), never a dead cell. Freeze-on-frame (D-02): pause/scroll-away holds
  // the current frame, re-enter restores it (Independent), never the poster.
  // Still vanilla and file://-safe: no network calls, no live-reload, no server.
  var PLAY_CAP = 12;
  // Explicit ceiling on simultaneously-ATTACHED decoders (WR-01), independent of
  // viewport + rootMargin geometry. A tall/large desktop viewport — or 144px cells
  // + a 300px margin against the 40-player mobile cap — can otherwise hold more
  // attached decoders than the browser WebMediaPlayer cap and black out cells.
  // 30 sits comfortably under the 40 mobile cap and well above PLAY_CAP.
  var ATTACH_CAP = 30;
  var FORCE_REJECT = /(?:^|[?&])forceRejectPlay=1(?:&|$)/.test(location.search);

  // window.__players = count of live (attached) decoders — the M5 observable
  // that must stay bounded (well under the browser WebMediaPlayer cap).
  window.__players = 0;

  // Monotonic ms source for the virtual master clock (no hidden leader <video>).
  var nowMs = function () {
    return (window.performance && performance.now) ? performance.now() : Date.now();
  };

  // PlayerManager owns the currently-playing set + the concurrent-play cap, plus
  // the virtual master clock that drives Synced mode (MEDIA-03 / D-08). Because
  // the clips are uniform in length + fps, clock position in seconds maps 1:1 to
  // a frame index — so currentTime equality == frame-index equality across cells.
  var manager = {
    playing: [], // oldest first, so [0] is the eviction victim
    attached: [], // ordered set of attached (decoder-holding) cells, oldest first
    fps: 24, // D-08 uniform-fps assumption; no probing (RESEARCH open Q locked)
    duration: 0, // common clip duration, learned from the first cell's metadata
    // clock: a performance.now()-based accumulator wrapped modulo duration. It is
    // purely virtual — no leader element — so it never consumes a WebMediaPlayer.
    // The clock is PAUSABLE (WR-02): a virtual freeze must actually stop the
    // master timeline. If it kept advancing while the grid is frozen, a cell that
    // scrolls out and back would re-seek to a LATER master frame than its
    // untouched siblings (silent desync), and Pause all → Play visible would
    // resume at the advanced phase instead of the frozen frame (forward jump).
    // When paused, position() returns the frozen frame; resume() re-anchors origin
    // so the timeline continues from exactly where it froze.
    clock: {
      origin: nowMs(),
      paused: false,
      frozenPos: 0,
      position: function () {
        if (!manager.duration) return 0; // duration unknown yet → phase 0
        if (this.paused) return this.frozenPos; // frozen: hold the frame across the grid
        return ((nowMs() - this.origin) / 1000) % manager.duration;
      },
      pause: function () {
        if (this.paused) return;
        this.frozenPos = this.position(); // capture the live frame before freezing
        this.paused = true;
      },
      resume: function () {
        if (!this.paused) return;
        this.origin = nowMs() - this.frozenPos * 1000; // continue from the frozen frame
        this.paused = false;
      }
    },
    track: function (cell) { this.untrack(cell); this.playing.push(cell); },
    untrack: function (cell) {
      var i = this.playing.indexOf(cell);
      if (i !== -1) this.playing.splice(i, 1);
    },
    trackAttached: function (cell) { this.untrackAttached(cell); this.attached.push(cell); },
    untrackAttached: function (cell) {
      var i = this.attached.indexOf(cell);
      if (i !== -1) this.attached.splice(i, 1);
    },
    // evictOldest(): free ONE concurrent-play slot when the cap is hit. It must
    // NOT route through VideoCell.pause(), whose Synced branch pauses EVERY
    // playing cell (that pause-all cascade would freeze the whole synced
    // comparison the moment the grid exceeds PLAY_CAP — CR-01). Tear down exactly
    // the oldest cell via the single-cell _markPaused primitive.
    evictOldest: function () {
      if (!this.playing.length) return;
      var victim = this.playing[0];
      if (victim.video) victim.video.pause();
      victim._markPaused(); // pauses + untracks exactly one cell, no synced cascade
    }
  };

  // ── Global playback controls (MEDIA-04 / D-09) ─────────────────────────────
  // pauseAll(): calm the whole grid back to frozen comparison frames. In Synced
  // mode read the master position ONCE and snap EVERY playing cell to it before
  // pausing them all — the comparison freezes on one frame index across the grid
  // (D-02). In Independent mode each cell freezes on its OWN current frame. Reuses
  // the Plan-03 _markPaused helper so ▶ returns + aria-pressed flips per cell.
  manager.pauseAll = function () {
    if (!this.playing.length) return;
    var cells = this.playing.slice(); // snapshot — _markPaused mutates the set
    var pos = isSynced() ? this.clock.position() : null; // master frame, read ONCE
    if (pos !== null) this.clock.pause(); // WR-02: freeze the master timeline too
    for (var i = 0; i < cells.length; i++) {
      var v = cells[i].video;
      if (v) {
        if (pos !== null) { try { v.currentTime = pos; } catch (e) {} } // snap-on-pause
        v.pause();
      }
      if (pos !== null) cells[i].frozenAt = pos; // record the frozen frame for re-entry
      cells[i]._markPaused();
    }
  };

  // playVisible(): start ONLY the cells currently in the viewport — never all
  // cells (D-09), never more than the concurrent-play cap. Iterate [data-video]
  // in document order (top-left → bottom-right) and test each against the
  // viewport with getBoundingClientRect. A `budget` reserves cap slots
  // synchronously (per-cell play() resolves async), so we can never exceed
  // PLAY_CAP. In Synced mode each started cell seeks to the master frame first
  // (play() already does this); in Independent it starts from frame 0 / its
  // frozen frame. Off-screen cells stay torn down (Plan-02 teardown intact).
  manager.playVisible = function () {
    var els = document.querySelectorAll("[data-video]");
    var vh = window.innerHeight || document.documentElement.clientHeight;
    var vw = window.innerWidth || document.documentElement.clientWidth;
    var budget = PLAY_CAP - this.playing.length; // remaining concurrent-play slots
    for (var i = 0; i < els.length && budget > 0; i++) {
      var cell = els[i].__cell;
      if (!cell || cell.playing) continue;
      var r = els[i].getBoundingClientRect();
      var onscreen = r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
      if (!onscreen) continue; // NEVER start an off-screen cell
      cell.play();
      budget--; // reserve the slot now — play()'s track() lands on the next tick
    }
  };

  function VideoCell(el) {
    this.el = el;
    this.video = el.querySelector("video");
    this.playing = false;
    this.blocked = false;
    this.frozenAt = 0;
    this.attached = false;
  }

  // attach(): paint the first-frame poster via the #t=0.001 data-src fragment
  // and register a live decoder. Idempotent (guards on this.attached).
  VideoCell.prototype.attach = function () {
    var v = this.video;
    if (!v || this.attached) return;
    // WR-01: enforce the attached-decoder ceiling BEFORE registering a new decoder.
    // Detach the oldest attached-but-not-playing cell so window.__players can never
    // climb past ATTACH_CAP on a large viewport. Playing cells stay attached
    // (PLAY_CAP already bounds them well under ATTACH_CAP).
    while (manager.attached.length >= ATTACH_CAP) {
      var evictee = null;
      for (var ai = 0; ai < manager.attached.length; ai++) {
        if (!manager.attached[ai].playing) { evictee = manager.attached[ai]; break; }
      }
      if (!evictee) break; // every attached cell is playing — PLAY_CAP still bounds it
      evictee.detach();
    }
    v.muted = true;
    v.src = this.el.getAttribute("data-src"); // #t=0.001 paints the poster frame
    v.load();
    this.attached = true;
    manager.trackAttached(this);
    window.__players++;
    var self = this;
    // One-shot metadata handler: (1) learn the common clip duration for the
    // virtual master clock, then (2) position the cell on re-enter — the shared
    // master frame in Synced mode (Pitfall 4 re-sync on scroll-back), else the
    // remembered frame (D-02 Independent freeze). Never resets to the poster.
    var onMeta = function () {
      if (!manager.duration && isFinite(v.duration) && v.duration > 0) {
        manager.duration = v.duration;
      }
      if (isSynced()) {
        try { v.currentTime = manager.clock.position(); } catch (e) {}
      } else if (self.frozenAt > 0) {
        try { v.currentTime = self.frozenAt; } catch (e) {}
      }
      v.removeEventListener("loadedmetadata", onMeta);
    };
    v.addEventListener("loadedmetadata", onMeta);
  };

  // detach(): freeze the current frame, then release the WebMediaPlayer.
  VideoCell.prototype.detach = function () {
    var v = this.video;
    if (!v || !this.attached) return;
    this.frozenAt = v.currentTime || this.frozenAt; // D-02 freeze frame
    v.pause();
    v.removeAttribute("src");
    v.load(); // frees the decoder (WebMediaPlayer) — mandatory under the cap
    this.attached = false;
    this.playing = false;
    this.el.classList.remove("is-playing");
    this.el.setAttribute("aria-pressed", "false");
    manager.untrack(this);
    manager.untrackAttached(this);
    window.__players--;
  };

  // play(): a direct user click on a specific cell is always honored (evicting
  // the oldest if at the concurrent cap); re-assert muted every time so the
  // autoplay policy can never veto a muted, user-gestured start.
  VideoCell.prototype.play = function () {
    var v = this.video;
    if (!v) return;
    if (!this.attached) this.attach();
    if (!this.playing && manager.playing.length >= PLAY_CAP) manager.evictOldest();
    v.muted = true;
    // Synced (D-06): resume the frozen master timeline (WR-02) then jump to the
    // shared master frame BEFORE play() so the cell joins the comparison in-phase
    // and playback continues from the frozen frame, not an advanced phase; the
    // drift tick keeps it locked thereafter.
    if (isSynced()) {
      manager.clock.resume();
      try { v.currentTime = manager.clock.position(); } catch (e) {}
    }
    var self = this;
    var settle = function (ok) {
      self.playing = ok;
      self.blocked = !ok;
      if (ok) {
        self.el.classList.add("is-playing");
        self.el.setAttribute("aria-pressed", "true");
        self.el.removeAttribute("data-blocked");
        manager.track(self);
        if (isSynced()) self.startDriftTick();
      } else {
        // D-11: keep the poster + ▶ and mark the cell — never a black/dead cell.
        self.el.classList.remove("is-playing");
        self.el.setAttribute("aria-pressed", "false");
        self.el.setAttribute("data-blocked", "");
        manager.untrack(self);
      }
    };
    // Debug hook (manual protocol M1 / future automated): force the poster path.
    if (FORCE_REJECT) { settle(false); return; }
    var p = v.play();
    if (p && typeof p.then === "function") {
      p.then(function () { settle(true); }).catch(function () { settle(false); });
    } else {
      settle(true);
    }
  };

  // startDriftTick(): while this cell plays in Synced mode, re-lock it to the
  // master clock once per painted frame. Prefer requestVideoFrameCallback (fires
  // per decoded video frame); fall back to requestAnimationFrame where absent.
  // TOL = one frame (1/fps), so a correction only fires past a full frame of
  // drift — keeps every cell on the same frame index without thrashing.
  VideoCell.prototype.startDriftTick = function () {
    var self = this, v = this.video;
    if (!v) return;
    var TOL = 1 / (manager.fps || 24);
    var tick = function () {
      if (!isSynced() || !self.playing || v.paused) return; // stop the loop
      var target = manager.clock.position();
      if (Math.abs(v.currentTime - target) > TOL) {
        try { v.currentTime = target; } catch (e) {}
      }
      ("requestVideoFrameCallback" in v) ? v.requestVideoFrameCallback(tick) : requestAnimationFrame(tick);
    };
    ("requestVideoFrameCallback" in v) ? v.requestVideoFrameCallback(tick) : requestAnimationFrame(tick);
  };

  // _markPaused(): reflect the paused UI state for one cell (▶ reappears as CSS
  // drops .is-playing) and drop it from the playing set. currentTime is left
  // untouched here — freeze-on-frame (D-02).
  VideoCell.prototype._markPaused = function () {
    this.playing = false;
    this.el.classList.remove("is-playing");
    this.el.setAttribute("aria-pressed", "false");
    manager.untrack(this);
  };

  // pause(): freeze on the current frame — do NOT reset currentTime. The ▶
  // reappears (CSS drops .is-playing). In Synced mode a pause is frame-locked:
  // read the master position ONCE and snap EVERY playing cell to it before
  // pausing them all, so the comparison moment is the same frame index across
  // the grid (D-02 / D-06).
  VideoCell.prototype.pause = function () {
    var v = this.video;
    if (!v) return;
    if (isSynced() && manager.playing.length) {
      var pos = manager.clock.position();
      manager.clock.pause(); // WR-02: freeze the master timeline with the cells
      var cells = manager.playing.slice(); // snapshot: _markPaused mutates the set
      for (var i = 0; i < cells.length; i++) {
        var cv = cells[i].video;
        if (cv) {
          try { cv.currentTime = pos; } catch (e) {}
          cv.pause();
        }
        cells[i].frozenAt = pos; // record the frozen frame for re-entry
        cells[i]._markPaused();
      }
    }
    v.pause();
    this._markPaused();
  };

  VideoCell.prototype.toggle = function () {
    if (this.playing) this.pause(); else this.play();
  };

  // The decoder-lifecycle observer is created whenever IntersectionObserver is
  // available — NOT gated on there being video cells at load time — so a cell
  // that arrives LATER via a live patch (applyPatch → registerVideoCell) still
  // joins the SAME observer + master clock (D-08), even on a grid that started
  // empty. rootMargin pre-attaches just-off-screen cells so a scrolled-to cell
  // already shows its poster; threshold 0 fires as soon as any pixel crosses.
  var observer = ("IntersectionObserver" in window)
    ? new IntersectionObserver(function (entries) {
        for (var i = 0; i < entries.length; i++) {
          var cell = entries[i].target.__cell;
          if (!cell) continue;
          if (entries[i].isIntersecting) cell.attach();
          else cell.detach();
        }
      }, { rootMargin: "300px 0px", threshold: 0 })
    : null;

  // registerVideoCell(el): wire ONE [data-video] element into the runtime — a
  // VideoCell, the shared IntersectionObserver, and the click / Space-Enter
  // toggle handlers. Called from BOTH the load-time loop AND applyPatch (D-08)
  // so a live-patched video cell is indistinguishable from a load-time one and
  // attaches/plays through the very same lifecycle. Idempotent — a node already
  // carrying __cell is never double-wired.
  function registerVideoCell(el) {
    if (!el || el.__cell) return el && el.__cell;
    var cell = new VideoCell(el);
    el.__cell = cell;
    if (observer) observer.observe(el);

    // A click anywhere on the cell toggles play/pause of THAT cell — except on
    // the ⧉ pop-out anchor, whose native new-tab navigation must proceed
    // (stopPropagation keeps its click from ever toggling playback).
    var popout = el.querySelector(".cell__popout");
    if (popout) {
      popout.addEventListener("click", function (ev) { ev.stopPropagation(); });
    }
    el.addEventListener("click", function () { cell.toggle(); });

    // Keyboard parity: Space/Enter on the focused cell toggles the same;
    // preventDefault on Space so the page never scrolls under the gesture.
    el.addEventListener("keydown", function (ev) {
      // WR-03: keydown bubbles up from the focused ⧉ pop-out anchor. Let its
      // native Enter navigation proceed WITHOUT also toggling this cell's
      // playback (mirrors the pop-out's existing click stopPropagation guard).
      if (ev.target.closest && ev.target.closest(".cell__popout")) return;
      if (ev.key === " " || ev.key === "Enter") {
        if (ev.key === " ") ev.preventDefault();
        cell.toggle();
      }
    });
    return cell;
  }

  var videoCells = document.querySelectorAll("[data-video]");
  if (videoCells.length && observer) {
    videoCells.forEach(function (el) { registerVideoCell(el); });
  }

  // Wire the two global playback controls (MEDIA-04). These are plain ACTION
  // buttons — NOT [data-set] toggles — so each gets its own click handler. Guard
  // for null so a JS-partial / control-less page never throws. There is
  // deliberately NO whole-grid play-everything control (D-09 calm default).
  var pauseAllBtn = document.getElementById("pause-all");
  if (pauseAllBtn) {
    pauseAllBtn.addEventListener("click", function () { manager.pauseAll(); });
  }
  var playVisibleBtn = document.getElementById("play-visible");
  if (playVisibleBtn) {
    playVisibleBtn.addEventListener("click", function () { manager.playVisible(); });
  }

  // ── Row/column crosshair (comparison scan aid) ─────────────────────────────
  // On hover/focus of a cell, light the shared row + column and their two sticky
  // headers (data-r / data-c coordinates from the template) so the eye can trace
  // one prompt down its steps / one step across its prompts. HIGHLIGHT-ONLY —
  // grid.css never dims or filters media (dimming would fight the comparison).
  // ONE delegated listener on the grid, not a handler per cell. Pointer-gated to
  // real (hover: hover) devices so a tap never sticks the grid highlighted;
  // keyboard focus lights it for everyone. State is instant; the transition is
  // motion-gated in CSS. No network, no server wiring — file://-safe like the rest.
  var grid = document.querySelector(".grid");
  if (grid) {
    var finePointer = window.matchMedia &&
      window.matchMedia("(hover: hover) and (pointer: fine)").matches;
    var lit = [];
    var clearHl = function () {
      for (var i = 0; i < lit.length; i++) lit[i].classList.remove("is-hl");
      lit = [];
    };
    var lightHl = function (r, c) {
      clearHl();
      // Cells carry both data-r and data-c; a header carries one. This selector
      // gathers the whole row, the whole column, and the two axis headers.
      var members = grid.querySelectorAll('[data-r="' + r + '"],[data-c="' + c + '"]');
      for (var i = 0; i < members.length; i++) {
        members[i].classList.add("is-hl");
        lit.push(members[i]);
      }
    };
    var lightFrom = function (target) {
      var el = target && target.closest ? target.closest("[data-r],[data-c]") : null;
      if (!el) return;
      var r = el.getAttribute("data-r");
      var c = el.getAttribute("data-c");
      // A header supplies only one axis; fill the other with a sentinel that
      // matches nothing, so hovering a header lights only its own row/column.
      lightHl(r === null ? " " : r, c === null ? " " : c);
    };
    if (finePointer) {
      grid.addEventListener("pointerover", function (ev) { lightFrom(ev.target); });
      grid.addEventListener("pointerleave", clearHl);
    }
    grid.addEventListener("focusin", function (ev) { lightFrom(ev.target); });
    grid.addEventListener("focusout", function (ev) {
      if (!grid.contains(ev.relatedTarget)) clearHl(); // clear only when focus exits the grid
    });
  }

  // ── Live watch (Phase 4 — RUN-04, D-01/D-02/D-07/D-08) ─────────────────────
  // In-place DOM patching from server-rendered HTML. This entire module is INERT
  // unless window.LIVE_ENDPOINT is injected ({% if live %} in grid.html.j2): the
  // frozen/build artifact never sets it, so no EventSource ever opens — the page
  // stays file://-safe with no dead controls (locked by the offline-safety
  // tests). The client ONLY parses JSON and mutates the DOM; every scrap of
  // cell/header markup is server-rendered (cell.j2 macros, autoescaped) and
  // inserted verbatim — never constructed here, so applyPatch adds no injection
  // surface beyond the already-escaped fragment (T-4-02).

  // nodeFrom(html): materialize one server-rendered fragment into a live node
  // via a <template> (parses the exact markup the full page shipped). Returns the
  // first element, skipping the macro's leading indentation text nodes.
  function nodeFrom(html) {
    var t = document.createElement("template");
    t.innerHTML = html;
    return t.content.firstElementChild;
  }

  // renumberAttr(gridEl, attr, from): bump every existing data-r / data-c index
  // >= from by one — an ATTRIBUTE-ONLY update on a mid-order insert so shifted
  // rows/columns keep their exact DOM nodes (a currently-playing <video> keeps
  // currentTime and its master-clock lock; D-07/D-08). MUST run BEFORE the new
  // nodes (which already carry the target index) are inserted, so they are never
  // double-incremented. The common append-at-end case matches nothing → no-op.
  function renumberAttr(gridEl, attr, from) {
    var nodes = gridEl.querySelectorAll("[" + attr + "]");
    for (var i = 0; i < nodes.length; i++) {
      var v = parseInt(nodes[i].getAttribute(attr), 10);
      if (!isNaN(v) && v >= from) nodes[i].setAttribute(attr, String(v + 1));
    }
  }

  // markNew(el): the transient per-cell arrival ring (D-02). Adds .is-new (a
  // short accent OUTLINE ring, motion-gated in grid.css — no new box, no layout
  // shift) and clears it after ~1.5s. A light in-viewport touch that never moves
  // the view; off-screen progress the ring can't show is surfaced by the pill.
  function markNew(el) {
    if (!el || !el.classList) return;
    el.classList.add("is-new");
    setTimeout(function () { el.classList.remove("is-new"); }, 1500);
  }

  // liveCue: the "+N new steps" pill controller, installed only by the guarded
  // live module below. Null on the frozen artifact (applyPatch never runs there
  // anyway), so the insert_row hook is a safe no-op.
  var liveCue = null;

  // applyPatch(p): mutate the CSS-grid DOM in place from ONE canonical patch
  // envelope (the exact shape 04-03 broadcasts over SSE). NEVER location.reload,
  // NEVER constructs cell markup, NEVER re-src's a playing cell. Field-name
  // contract: replace_cell carries `html`; insert_row/insert_col carry
  // `header_html` + `cells`.
  function applyPatch(p) {
    var gridEl = document.querySelector(".grid");
    if (!gridEl || !p || !p.op) return;

    if (p.op === "replace_cell") {
      // The slot already exists in the dense lattice → an in-place node swap:
      // no insertion, no reflow, no scroll change (D-07 backfill-in-place). The
      // [data-r][data-c] selector matches a CELL only (both attrs), never a
      // single-axis header.
      var target = gridEl.querySelector('[data-r="' + p.r + '"][data-c="' + p.c + '"]');
      if (!target) return;
      var node = nodeFrom(p.html);
      if (!node) return;
      target.replaceWith(node);
      if (node.matches && node.matches("[data-video]")) registerVideoCell(node);
      markNew(node);
      return;
    }

    if (p.op === "insert_row") {
      // New step. Common case: the new step is the largest → rowRef is null →
      // append below the fold, no existing cell moves (D-01 stay-put). Mid-order:
      // insert before the row currently at p.index and renumber shifted rows
      // attribute-only (no node recreation → playing videos survive).
      var rowRef = gridEl.querySelector('.row-header[data-r="' + p.index + '"]');
      renumberAttr(gridEl, "data-r", p.index);
      var header = nodeFrom(p.header_html);
      if (header) gridEl.insertBefore(header, rowRef); // rowRef null → appendChild
      var cells = p.cells || [];
      for (var i = 0; i < cells.length; i++) {
        var cnode = nodeFrom(cells[i]);
        if (!cnode) continue;
        // Repeated insertBefore(_, rowRef) preserves header→cell0→cell1… order.
        gridEl.insertBefore(cnode, rowRef);
        if (cnode.matches && cnode.matches("[data-video]")) registerVideoCell(cnode);
        markNew(cnode);
      }
      // Surface a below-the-fold new step in the "+N" pill (no-op when the live
      // cue is absent — the frozen artifact never reaches this path anyway).
      if (liveCue) liveCue.notifyRow(header);
      return;
    }

    if (p.op === "insert_col") {
      // New prompt (the rarest, fiddliest op). Grow the track, then insert the
      // header + EXACTLY ONE cell into EVERY row atomically so auto-placement
      // keeps 1 + n_cols children per row. References are captured under the OLD
      // indexing FIRST (node identity is stable across the attribute-only
      // renumber), then existing data-c >= index is bumped, then the new nodes
      // (which already carry data-c = index) are inserted before the saved refs.
      var cidx = p.index;
      var entries = p.cells || [];
      var headerRef = gridEl.querySelector('.col-header[data-c="' + cidx + '"]');
      if (!headerRef) headerRef = gridEl.querySelector(".row-header"); // append after last header
      var refs = [];
      for (var j = 0; j < entries.length; j++) {
        var r = entries[j].r;
        var ref = gridEl.querySelector('[data-r="' + r + '"][data-c="' + cidx + '"]');
        if (!ref) ref = gridEl.querySelector('.row-header[data-r="' + (r + 1) + '"]');
        refs.push(ref || null); // null → append at grid end (the last row)
      }
      renumberAttr(gridEl, "data-c", cidx);
      root.style.setProperty("--n-cols", p.n_cols); // gain one grid-template-columns track
      var chead = nodeFrom(p.header_html);
      if (chead) gridEl.insertBefore(chead, headerRef);
      for (var k = 0; k < entries.length; k++) {
        var enode = nodeFrom(entries[k].html);
        if (!enode) continue;
        gridEl.insertBefore(enode, refs[k]);
        if (enode.matches && enode.matches("[data-video]")) registerVideoCell(enode);
        markNew(enode);
      }
      return;
    }
  }

  // Guarded live channel: opens the SSE stream and injects the D-02 cue elements
  // ONLY when the server injected the endpoint. Absent (build/freeze) → none of
  // this runs: nothing connects, and NO pill / Live indicator is ever added to
  // the DOM, so the frozen artifact carries no dead controls (T-4-05).
  var LIVE_ENDPOINT = window.LIVE_ENDPOINT || null;
  if (LIVE_ENDPOINT) {
    var bar = document.querySelector(".toggle-bar");

    // Quiet "Live" indicator — neutral --text-muted, no accent, no motion. It
    // reflects the SSE channel: "Live" while open, "Live · reconnecting…" on an
    // error while the browser auto-reconnects (~3s), back to "Live" on the next
    // open/message (Research A5: the next re-scan self-heals — no replay code).
    var liveStatus = document.createElement("span");
    liveStatus.className = "live-status";
    liveStatus.textContent = "Live";
    if (bar) bar.appendChild(liveStatus);

    // "+N new steps →" pill — surfaces BELOW-THE-FOLD arrivals the per-cell ring
    // can't. Hidden at N=0; a click scrolls to the newest row (opt-in jump that
    // honors D-01 stay-put — the view NEVER auto-scrolls) and clears the count;
    // it also clears when the newest row scrolls into view on its own.
    var pill = document.createElement("button");
    pill.type = "button";
    pill.className = "new-pill";
    pill.hidden = true;
    if (bar) bar.appendChild(pill);

    var pillCount = 0;
    var newestRow = null; // most-recent below-the-fold insert_row header (jump target)

    var renderPill = function () {
      if (pillCount <= 0) { pill.hidden = true; pill.textContent = ""; return; }
      var noun = pillCount === 1 ? "step" : "steps";
      // The count rides in an accent chip (tabular-nums so the digit doesn't
      // jitter as N climbs); the pill body itself stays neutral secondary.
      pill.textContent = "";
      var chip = document.createElement("span");
      chip.className = "new-pill__chip";
      chip.textContent = "+" + pillCount;
      pill.appendChild(chip);
      pill.appendChild(document.createTextNode(" new " + noun + " →"));
      pill.setAttribute("aria-label", "+" + pillCount + " new " + noun + " — jump to newest");
      pill.hidden = false;
    };

    var clearPill = function () {
      pillCount = 0;
      newestRow = null;
      renderPill();
    };

    pill.addEventListener("click", function () {
      // Opt-in jump — the ONLY place the view ever moves for an arrival.
      if (newestRow && newestRow.scrollIntoView) {
        newestRow.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      clearPill();
    });

    // When the newest row scrolls into view on its own, the pill has done its
    // job → clear it (we never auto-scrolled to get there).
    var rowVis = ("IntersectionObserver" in window)
      ? new IntersectionObserver(function (entries) {
          for (var i = 0; i < entries.length; i++) {
            if (entries[i].isIntersecting && entries[i].target === newestRow) clearPill();
          }
        }, { threshold: 0 })
      : null;

    liveCue = {
      // notifyRow(headerEl): count an arrival ONLY when it lands below the fold —
      // an in-viewport new row already shows its ring, so counting it would
      // double-cue. Never moves the view.
      notifyRow: function (headerEl) {
        if (!headerEl || !headerEl.getBoundingClientRect) return;
        var r = headerEl.getBoundingClientRect();
        var vh = window.innerHeight || document.documentElement.clientHeight;
        if (r.top >= 0 && r.top < vh) return; // visible → ring already surfaced it
        pillCount++;
        newestRow = headerEl;
        renderPill();
        if (rowVis) { rowVis.disconnect(); rowVis.observe(headerEl); }
      }
    };

    var es = new EventSource(LIVE_ENDPOINT);
    es.onopen = function () { liveStatus.textContent = "Live"; };
    es.onmessage = function (e) {
      liveStatus.textContent = "Live";
      try { applyPatch(JSON.parse(e.data)); } catch (err) {}
    };
    es.onerror = function () { liveStatus.textContent = "Live · reconnecting…"; };
  }
})();
