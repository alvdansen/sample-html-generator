"""Filename metadata extraction (META-01) + the retained Phase-1 stub.

``FilenameExtractor`` implements the ``Extractor`` contract
(``extract(files) -> {rel_id: {field: FieldValue}}``, source ``"filename"``). It
recognises three signal classes, in confidence order:

1. **Labeled tokens (HIGH):** a key adjacent to a number in the stem —
   ``step_600``, ``seed42``, ``cfg7`` — mapped by key. (RESEARCH §Disambiguation 1)
2. **ai-toolkit structural (HIGH):** a 9-digit zero-padded step followed by a
   trailing small sample index, ``{ts}__{step:09d}_{idx}`` — the step is the
   9-digit int; the index is surfaced honestly as an INTEGER prompt, with NO
   index→text resolution (A2).
3. **Prompt fallback (MEDIUM):** the immediate parent directory name, exactly the
   Phase-1 ``FilenameStubParser`` behavior.

Embedded PNG text chunks are NOT read (A4 — out of scope). ``FilenameStubParser``
is kept intact and importable (cli/main.py + tests/test_grid.py depend on it).
"""
from __future__ import annotations

import re
from pathlib import Path

from sample_grid.core.model import Sample, SampleIndex
from sample_grid.core.parse.base import FieldValue, rel_id_for
from sample_grid.core.scan import media_type_for
from sample_grid.util.paths import to_posix

_STEP_RE = re.compile(r"\d+")

# Labeled-token regex (compiled at module scope, linear — no nested quantifiers
# over .*, so it is ReDoS-safe on huge filenames, T-02-02). Case-insensitive.
# A left token-boundary lookbehind ``(?<![A-Za-z0-9])`` requires each alias to
# start a token, so ``sd`` cannot match mid-word and ``loss_d0``-style stems
# (bare ``d`` alias removed entirely, WR-03) yield no spurious seed.
_LABELED_RE = re.compile(
    r"(?<![A-Za-z0-9])(step|steps|seed|sd|cfg|idx|sample|epoch)[ _\-]?(\d+)",
    re.IGNORECASE,
)

# Key → dims field. ``idx``/``sample`` surface as the prompt sample index.
_KEY_FIELD = {
    "step": "step",
    "steps": "step",
    "epoch": "step",
    "seed": "seed",
    "sd": "seed",
    "cfg": "cfg",
    "idx": "prompt",
    "sample": "prompt",
}

# ai-toolkit structural recognizer: a 9-digit zero-padded step, then a trailing
# small sample index (bounded digit runs — linear, ReDoS-safe).
_AITOOLKIT_RE = re.compile(r"(?P<step>\d{9})_(?P<idx>\d+)")

_CONF_HIGH = 1.0
_CONF_MED = 0.5

_INT_FIELDS = {"step", "seed", "cfg"}


class FilenameExtractor:
    """META-01: per-field detection from the filename (source=``filename``).

    Constructed with the scanned ``root`` so its output is keyed by the shared,
    prompt-independent ``rel_id_for(file, root)`` token (not the detected prompt),
    guaranteeing it merges onto the same bucket every other extractor produces
    for the same physical file (closes WR-01 / WR-05).
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def extract(self, files: list[Path]) -> "dict[str, dict[str, FieldValue]]":
        out: dict[str, dict[str, FieldValue]] = {}
        for file in files:
            file = Path(file)
            stem = file.stem
            fields: dict[str, FieldValue] = {}

            # 1. Labeled tokens (HIGH) — first match per field wins.
            for m in _LABELED_RE.finditer(stem):
                field = _KEY_FIELD.get(m.group(1).lower())
                if field and field not in fields:
                    fields[field] = FieldValue(int(m.group(2)), "filename", _CONF_HIGH)

            # 2. ai-toolkit structural (HIGH) — 9-digit step + trailing index.
            ai = _AITOOLKIT_RE.search(stem)
            if ai:
                if "step" not in fields:
                    fields["step"] = FieldValue(
                        int(ai.group("step")), "filename", _CONF_HIGH
                    )
                if "prompt" not in fields:
                    # Prompt surfaced as the integer sample index (A2).
                    fields["prompt"] = FieldValue(
                        int(ai.group("idx")), "filename", _CONF_HIGH
                    )

            # 3. Prompt fallback (MEDIUM) — the immediate parent dir name.
            if "prompt" not in fields:
                fields["prompt"] = FieldValue(file.parent.name, "filename", _CONF_MED)

            # Merge key is the stable per-file token — NOT the detected prompt.
            # The prompt above flows on as an ordinary merged field (D-03).
            rel_id = rel_id_for(file, self.root)
            out[rel_id] = fields
        return out


class FilenameStubParser:
    """P1 placeholder parser: ``<prompt>/step_<N>.<ext>``.

    Retained verbatim as a working alias so ``cli/main.py`` and
    ``tests/test_grid.py`` imports keep resolving. The auto-detect path uses
    ``FilenameExtractor`` (above); this stub remains the naive single-strategy
    parser it always was.
    """

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
                    media_type=media_type_for(file),
                    dims={"step": step, "prompt": prompt},
                )
            )
        return index
