"""The ONE Phase-1 grouping stub (Option A, deliberately naive).

Convention (documented in the CLI ``--help`` and README):
    ``<prompt>/step_<N>.<ext>``  — the immediate parent directory name is the
    prompt; the first integer matched in the file stem is the training step.

This is intentionally dumb: no nesting heuristics, no auto-detect, no confidence
scoring, no sidecar/template support. All of that is Phase 2's design space
(Pitfall 6). Files with no integer in the stem are skipped (documented).
"""
from __future__ import annotations

import re
from pathlib import Path

from sample_grid.core.model import Sample, SampleIndex
from sample_grid.util.paths import to_posix

_STEP_RE = re.compile(r"\d+")


class FilenameStubParser:
    """P1 placeholder parser: ``<prompt>/step_<N>.<ext>``. Extended in Phase 2."""

    def parse(self, files: list[Path]) -> SampleIndex:
        index: SampleIndex = []
        for file in files:
            file = Path(file)
            match = _STEP_RE.search(file.stem)
            if match is None:
                # No step integer in the stem — not part of the P1 convention.
                continue
            step = int(match.group())
            prompt = file.parent.name
            # Stable posix-relative id: "<prompt>/<filename>".
            rel_id = to_posix(Path(prompt) / file.name)
            index.append(
                Sample(
                    id=rel_id,
                    path=file,
                    media_type="image",
                    dims={"step": step, "prompt": prompt},
                )
            )
        return index
