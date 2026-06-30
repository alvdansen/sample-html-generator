"""The parser seam (P2 swap point).

Phase 1 ships one concrete strategy (``FilenameStubParser``). Phase 2 adds
subfolder / sidecar / template strategies plus an auto-detect picker behind this
same ``Parser`` Protocol, emitting the same ``Sample`` shape into the same
``SampleIndex``. Nothing downstream of the index knows which parser produced it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from sample_grid.core.model import SampleIndex


@runtime_checkable
class Parser(Protocol):
    """Turns a list of discovered files into a SampleIndex."""

    def parse(self, files: list[Path]) -> SampleIndex: ...
