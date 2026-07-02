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
    fps: 24, // D-08 uniform-fps assumption; no probing (RESEARCH open Q locked)
    duration: 0, // common clip duration, learned from the first cell's metadata
    // clock: a performance.now()-based accumulator wrapped modulo duration. It is
    // purely virtual — no leader element — so it never consumes a WebMediaPlayer.
    clock: {
      origin: nowMs(),
      position: function () {
        if (!manager.duration) return 0; // duration unknown yet → phase 0
        return ((nowMs() - this.origin) / 1000) % manager.duration;
      }
    },
    track: function (cell) { this.untrack(cell); this.playing.push(cell); },
    untrack: function (cell) {
      var i = this.playing.indexOf(cell);
      if (i !== -1) this.playing.splice(i, 1);
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
    for (var i = 0; i < cells.length; i++) {
      var v = cells[i].video;
      if (v) {
        if (pos !== null) { try { v.currentTime = pos; } catch (e) {} } // snap-on-pause
        v.pause();
      }
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
    v.muted = true;
    v.src = this.el.getAttribute("data-src"); // #t=0.001 paints the poster frame
    v.load();
    this.attached = true;
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
    // Synced (D-06): jump to the shared master frame BEFORE play() so the cell
    // joins the comparison in-phase; the drift tick keeps it locked thereafter.
    if (isSynced()) {
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
      var cells = manager.playing.slice(); // snapshot: _markPaused mutates the set
      for (var i = 0; i < cells.length; i++) {
        var cv = cells[i].video;
        if (cv) {
          try { cv.currentTime = pos; } catch (e) {}
          cv.pause();
        }
        cells[i]._markPaused();
      }
    }
    v.pause();
    this._markPaused();
  };

  VideoCell.prototype.toggle = function () {
    if (this.playing) this.pause(); else this.play();
  };

  var videoCells = document.querySelectorAll("[data-video]");
  if (videoCells.length && "IntersectionObserver" in window) {
    // rootMargin pre-attaches just-off-screen cells so a scrolled-to cell already
    // shows its poster; threshold 0 fires as soon as any pixel enters/leaves.
    var observer = new IntersectionObserver(function (entries) {
      for (var i = 0; i < entries.length; i++) {
        var cell = entries[i].target.__cell;
        if (!cell) continue;
        if (entries[i].isIntersecting) cell.attach();
        else cell.detach();
      }
    }, { rootMargin: "300px 0px", threshold: 0 });

    videoCells.forEach(function (el) {
      var cell = new VideoCell(el);
      el.__cell = cell;
      observer.observe(el);

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
        if (ev.key === " " || ev.key === "Enter") {
          if (ev.key === " ") ev.preventDefault();
          cell.toggle();
        }
      });
    });
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
})();
