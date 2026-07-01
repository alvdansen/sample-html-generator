"""The parser seam (P2 swap point) + the auto-detect picker.

Phase 1 shipped one concrete strategy (``FilenameStubParser``). Phase 2 models
detection as **per-field extractors with provenance**: every source (filename /
subfolder in this plan; sidecar in 02-03; template in 02-04) implements ONE named
contract — the ``Extractor`` Protocol — returning ``{rel_id: {field: FieldValue}}``.
The ``AutoDetectParser`` picker runs the extractors, merges their fields by a fixed
precedence (``sidecar > filename > subfolder``, D-03), counts source disagreements
(D-04) and unclassifiable files (D-05), and emits the exact same ``Sample`` shape
into the unchanged ``SampleIndex``. Nothing downstream of the index knows which
extractor produced a field.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from sample_grid.core.model import Sample, SampleIndex
from sample_grid.core.scan import media_type_for
from sample_grid.util.paths import to_posix


def rel_id_for(file: "str | Path", root: "str | Path") -> str:
    """The single stable per-file merge key every extractor keys on.

    Returns ``to_posix(Path(file).relative_to(root))`` — the file's POSIX path
    relative to the scan root. This token does NOT depend on any detected prompt,
    so every extractor (filename / subfolder / sidecar / template) derives the
    IDENTICAL key for the same physical file. Because the buckets always agree,
    the prompt becomes an ordinary merged field arbitrated by ``SOURCE_PRECEDENCE``
    (D-03) rather than a component of the key — the previously drift-prone contract
    is now un-driftable (closes CR-01 / WR-01 / WR-05).

    Defensive fallback: a file outside ``root`` (``relative_to`` raises
    ``ValueError``) falls back to ``to_posix(Path(file))`` so the key is always
    derived deterministically and never raises — the same guard TemplateParser
    already used at template.py.

    For flat two-segment ``<prompt>/<file>`` layouts pointed at their own folder,
    this yields the identical ``<prompt>/<file>`` key the old prompt-derived
    derivation produced, so pre-existing Samples are unchanged (no regression).
    """
    file = Path(file)
    try:
        return to_posix(file.relative_to(root))
    except ValueError:
        return to_posix(file)


@runtime_checkable
class Parser(Protocol):
    """Turns a list of discovered files into a SampleIndex (the Phase-1 seam)."""

    def parse(self, files: list[Path]) -> SampleIndex: ...


@runtime_checkable
class Extractor(Protocol):
    """The single per-source call-contract every detection source implements.

    ``extract`` maps a list of discovered files to a nested dict keyed first by
    the stable per-file ``rel_id`` — the file's POSIX path relative to the scan
    root, produced by the shared ``rel_id_for(file, root)`` helper — then by field
    name (``step`` / ``prompt`` / ``seed`` / ``model`` / ``checkpoint`` / ``cfg``)
    to a ``FieldValue`` carrying the value, its ``source`` string, and a
    confidence. The key is NOT prompt-derived: because every extractor computes it
    identically from ``rel_id_for``, all sources land in the same merge bucket and
    the prompt is arbitrated as an ordinary field by precedence (D-03). Declared
    here — not as prose — so every detection source plugs in against one named
    signature without editing this module.
    """

    def extract(self, files: list[Path]) -> "dict[str, dict[str, FieldValue]]": ...


@dataclass(frozen=True)
class FieldValue:
    """One extracted field: its value, the source that produced it, a confidence.

    Frozen (mirrors ``Sample``) so it is hashable and safe to share. ``confidence``
    informs the ``detect`` report and what counts as unclassifiable (D-05) — it is
    NOT used for precedence, which is fixed by ``SOURCE_PRECEDENCE`` (D-03).
    """

    value: object
    source: str          # "template" | "sidecar" | "filename" | "subfolder"
    confidence: float    # 0..1 — informs detect report (D-05), NOT precedence


@dataclass
class DetectionReport:
    """Structured dry-run payload the picker returns alongside the SampleIndex.

    Pure data — ``build`` discards it (D-02 CLI-silent); ``detect`` formats it to
    stdout (D-01). Mutable accumulators use ``field(default_factory=list)``.
    """

    n_files: int = 0
    skipped: list = field(default_factory=list)            # D-05 unclassifiable
    conflicts: list = field(default_factory=list)          # D-04 source disagreements
    multi_seed_coords: list = field(default_factory=list)  # D-09 (filled downstream)


# Fixed, documented precedence (D-03). ``template`` and ``sidecar`` keys are
# included now so Plans 02-03/02-04 need no edit here — the ordering among the
# auto-detect sources (filename > subfolder) is preserved.
SOURCE_PRECEDENCE = {"template": 4, "sidecar": 3, "filename": 2, "subfolder": 1}

# The fields the merge considers, in a stable order. ``model``/``checkpoint``/
# ``cfg`` parse cleanly but stay unmapped to axes until AXIS-01 (D-07).
_MERGE_FIELDS = ("step", "prompt", "seed", "model", "checkpoint", "cfg")


def merge_fields(
    per_source: "dict[str, dict[str, FieldValue]]", report: DetectionReport
) -> dict:
    """Resolve one file's per-source FieldValues into final ``dims`` by precedence.

    ``per_source`` is ``{source_name: {field: FieldValue}}``. For each field, the
    highest-precedence source wins (D-03); when sources disagree on the value, the
    disagreement is recorded on ``report.conflicts`` as ``(field, [(source, value)])``
    (D-04) — the higher-precedence value is still used.
    """
    final: dict = {}
    for fieldname in _MERGE_FIELDS:
        candidates = [
            source_fields[fieldname]
            for source_fields in per_source.values()
            if fieldname in source_fields
        ]
        if not candidates:
            continue
        winner = max(candidates, key=lambda fv: SOURCE_PRECEDENCE[fv.source])
        if len({c.value for c in candidates}) > 1:
            report.conflicts.append(
                (fieldname, [(c.source, c.value) for c in candidates])
            )
        final[fieldname] = winner.value
    return final


def _resolve_path(rel_id: str, files: list[Path], root: "str | Path") -> "Path | None":
    """Map a ``rel_id`` back to the on-disk file that produced it.

    ``rel_id`` is the file's stable POSIX-relative-to-root token produced by
    ``rel_id_for``. Resolution is the exact inverse of that same helper: the file
    whose ``rel_id_for(f, root)`` equals ``rel_id`` is THE file that produced the
    key — unambiguous by construction. A basename/parent-name heuristic is not
    sufficient: two files can share both their basename AND their immediate parent
    directory name while differing higher up the tree (``lake/2023/step_600.png``
    vs ``ocean/2023/step_600.png``), which are distinct ``rel_id``s but collide on
    ``name``+``parent.name``. Matching on the full relative token cannot mis-resolve.
    """
    for f in files:
        if rel_id_for(f, root) == rel_id:
            return f
    return None


class AutoDetectParser:
    """Runs a list of ``Extractor``s, merges by precedence, returns (index, report).

    A file is *classifiable* only when the merge yields at least ``step`` and
    ``prompt`` — the two axes the Phase-2 grid needs. Everything else is skipped
    and counted (D-05). The returned index is sorted by ``Sample.id`` for
    deterministic downstream ordering.
    """

    def __init__(self, extractors: "list[Extractor]", root: "str | Path") -> None:
        self.extractors = list(extractors)
        # The scan root — the single source of truth for inverting ``rel_id_for``
        # back to the on-disk file (see ``_resolve_path``). Required so path
        # resolution keys on the SAME full-relative token the extractors keyed on,
        # never a lossy basename+parent heuristic that can mis-resolve nested files.
        self.root = Path(root)

    def parse(self, files: list[Path]) -> "tuple[SampleIndex, DetectionReport]":
        files = [Path(f) for f in files]
        report = DetectionReport(n_files=len(files))

        # Assemble, per rel_id, the {source: {field: FieldValue}} map the merge
        # consumes. Each FieldValue carries its own source string.
        per_rel: dict[str, dict[str, dict[str, FieldValue]]] = {}
        for extractor in self.extractors:
            for rel_id, field_map in extractor.extract(files).items():
                bucket = per_rel.setdefault(rel_id, {})
                for fieldname, fv in field_map.items():
                    bucket.setdefault(fv.source, {})[fieldname] = fv

        index: SampleIndex = []
        classified: set = set()
        for rel_id, per_source in per_rel.items():
            merged = merge_fields(per_source, report)
            if "step" in merged and "prompt" in merged:
                path = _resolve_path(rel_id, files, self.root)
                index.append(
                    Sample(
                        id=rel_id,
                        # Classify from rel_id (always carries the true suffix);
                        # ``path`` may be None for a template-only Sample.
                        media_type=media_type_for(Path(rel_id)),
                        path=path if path is not None else Path(rel_id),
                        dims=merged,
                    )
                )
                if path is not None:
                    classified.add(path)

        # Any input file that never became a classifiable Sample is skipped (D-05).
        for f in files:
            if f not in classified:
                report.skipped.append(to_posix(f))

        index.sort(key=lambda s: s.id)
        return index, report
