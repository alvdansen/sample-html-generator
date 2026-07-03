"""Live-watch server-side services (Phase 4).

This package holds the net-new server-side pieces of live-watch mode:

* ``diff`` — turns a rebuilt :class:`~sample_grid.core.model.GridModel` into a
  minimal, structural-first-ordered list of ``Patch`` objects (RUN-04).
* ``watcher`` — an ``awatch`` loop guarded by a wait-for-stable-size settle gate
  that admits only fully-written files (RUN-03 / D-03).

Both are pure/async Python with no server dependency, so they are unit-testable
in isolation and define the ``Patch`` envelope contract the server (04-03) and
client (04-04) consume.
"""
