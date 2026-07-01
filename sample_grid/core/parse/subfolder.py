"""Subfolder-structure metadata extraction (META-02, lowest precedence).

``SubfolderExtractor`` implements the ``Extractor`` contract
(``extract(files) -> {rel_id: {field: FieldValue}}``, source ``"subfolder"``). It
formalizes the Phase-1 ``file.parent.name -> prompt`` behavior and additionally
walks structural ``step_<N>`` / ``seed_<N>`` path segments: starting at the
immediate parent it consumes consecutive structural segments (recording their
step/seed), stopping at the first NON-structural segment, which becomes the
prompt. It emits ``source="subfolder"`` — the lowest precedence in the merge
(D-03), so filename/sidecar values override it on disagreement.

Media stays image-only (video is Phase 3); no embedded PNG chunks are read (A4).
"""
from __future__ import annotations

import re
from pathlib import Path

from sample_grid.core.parse.base import FieldValue
from sample_grid.util.paths import to_posix

# Full-segment structural recognizers (compiled at module scope, linear —
# ReDoS-safe). A directory named ``step_500`` / ``steps-500`` → step; a
# directory named ``seed_7`` / ``sd7`` / ``d42`` → seed.
_STEP_SEG = re.compile(r"(?:step|steps)[ _\-]?(\d+)", re.IGNORECASE)
_SEED_SEG = re.compile(r"(?:seed|sd|d)[ _\-]?(\d+)", re.IGNORECASE)

_CONF_STEP = 0.6
_CONF_PROMPT = 0.4


class SubfolderExtractor:
    """META-02: per-field detection from the folder structure (source=``subfolder``)."""

    def extract(self, files: list[Path]) -> "dict[str, dict[str, FieldValue]]":
        out: dict[str, dict[str, FieldValue]] = {}
        for file in files:
            file = Path(file)
            fields: dict[str, FieldValue] = {}
            prompt: str | None = None

            # Walk from the immediate parent upward, consuming structural
            # segments and stopping at the first non-structural one (the prompt).
            for parent in (file.parent, *file.parent.parents):
                name = parent.name
                if not name:  # reached an anonymous/drive root
                    break
                sm = _STEP_SEG.fullmatch(name)
                if sm:
                    if "step" not in fields:
                        fields["step"] = FieldValue(
                            int(sm.group(1)), "subfolder", _CONF_STEP
                        )
                    continue
                dm = _SEED_SEG.fullmatch(name)
                if dm:
                    if "seed" not in fields:
                        fields["seed"] = FieldValue(
                            int(dm.group(1)), "subfolder", _CONF_STEP
                        )
                    continue
                prompt = name
                break

            if prompt is not None:
                fields["prompt"] = FieldValue(prompt, "subfolder", _CONF_PROMPT)

            rel_prompt = prompt if prompt is not None else file.parent.name
            rel_id = to_posix(Path(rel_prompt) / file.name)
            out[rel_id] = fields
        return out
