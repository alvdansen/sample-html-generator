"""Auto-detect picker + per-field precedence merge + source extractors.

Task 1 drives ``merge_fields`` / ``AutoDetectParser`` directly with hand-built
per-source ``FieldValue`` maps (inline stub extractors satisfying the
``Extractor`` Protocol — no on-disk parsing needed). Task 2 adds the real
``FilenameExtractor`` / ``SubfolderExtractor`` tests further down.
"""
from __future__ import annotations

from pathlib import Path

from sample_grid.core.parse.base import (
    SOURCE_PRECEDENCE,
    AutoDetectParser,
    DetectionReport,
    Extractor,
    FieldValue,
    merge_fields,
)


def test_precedence_merge() -> None:
    """Same value from both sources merges once (no conflict); different values
    resolve to the higher-precedence source (filename > subfolder, D-03)."""
    report = DetectionReport()
    agree = {
        "filename": {"step": FieldValue(600, "filename", 1.0)},
        "subfolder": {"step": FieldValue(600, "subfolder", 0.4)},
    }
    merged = merge_fields(agree, report)
    assert merged["step"] == 600
    assert report.conflicts == []

    report2 = DetectionReport()
    disagree = {
        "filename": {"step": FieldValue(600, "filename", 1.0)},
        "subfolder": {"step": FieldValue(500, "subfolder", 0.4)},
    }
    merged2 = merge_fields(disagree, report2)
    # filename outranks subfolder — its value wins.
    assert merged2["step"] == 600
    assert SOURCE_PRECEDENCE["filename"] > SOURCE_PRECEDENCE["subfolder"]


def test_conflict_report() -> None:
    """Differing values across sources append a (field, [(source, value)]) entry
    to DetectionReport.conflicts (D-04)."""
    report = DetectionReport()
    per_source = {
        "filename": {"step": FieldValue(600, "filename", 1.0)},
        "subfolder": {"step": FieldValue(500, "subfolder", 0.4)},
    }
    merge_fields(per_source, report)

    assert len(report.conflicts) == 1
    fieldname, candidates = report.conflicts[0]
    assert fieldname == "step"
    assert ("filename", 600) in candidates
    assert ("subfolder", 500) in candidates


class _StubExtractor:
    """Inline Extractor (Protocol-conformant) for the picker skip test."""

    def extract(self, files: list[Path]) -> dict[str, dict[str, FieldValue]]:
        out: dict[str, dict[str, FieldValue]] = {}
        for f in files:
            if "step_600" in Path(f).name:
                out["a_lake/step_600.png"] = {
                    "step": FieldValue(600, "filename", 1.0),
                    "prompt": FieldValue("a_lake", "subfolder", 0.4),
                }
        return out


def test_skip_unclassifiable(tmp_path: Path) -> None:
    """A file no extractor classifies is excluded from the index and appended to
    DetectionReport.skipped; parse() returns (SampleIndex, DetectionReport)."""
    good = tmp_path / "a_lake" / "step_600.png"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_bytes(b"\x89PNG stub")
    bad = tmp_path / "a_lake" / "notes.png"
    bad.write_bytes(b"\x89PNG stub")

    # The stub satisfies the runtime-checkable Extractor Protocol.
    assert isinstance(_StubExtractor(), Extractor)

    index, report = AutoDetectParser([_StubExtractor()]).parse([good, bad])

    assert len(index) == 1
    assert index[0].dims["step"] == 600
    assert index[0].dims["prompt"] == "a_lake"
    assert index[0].path == good
    # The no-integer file is skipped and counted (D-05).
    assert report.n_files == 2
    assert len(report.skipped) == 1


# ---------------------------------------------------------------------------
# Task 2: real filename / subfolder extractors
# ---------------------------------------------------------------------------


def test_filename_extract(aitoolkit_style_folder: Path) -> None:
    """META-01: labeled tokens (step_600_seed42) and ai-toolkit structural names
    (9-digit zero-padded step + trailing sample index) → per-field FieldValues,
    source="filename", prompt surfaced as the integer index for ai-toolkit (A2)."""
    from sample_grid.core.parse.filename import FilenameExtractor
    from sample_grid.core.scan import Scanner

    # Labeled tokens: step_600_seed42 under a prompt folder.
    labeled = aitoolkit_style_folder.parent / "labeled"
    (labeled / "a_lake").mkdir(parents=True, exist_ok=True)
    f = labeled / "a_lake" / "step_600_seed42.png"
    f.write_bytes(b"x")

    out = FilenameExtractor().extract([f])
    (fields,) = out.values()
    assert fields["step"].value == 600
    assert fields["step"].source == "filename"
    assert fields["seed"].value == 42
    assert fields["seed"].source == "filename"
    assert fields["prompt"].value == "a_lake"

    # ai-toolkit: 20260630__000000600_3.jpg → step=600, prompt=index 3 (A2).
    ai_files = Scanner().scan(aitoolkit_style_folder)
    ai_out = FilenameExtractor().extract(ai_files)
    sample_fields = next(iter(ai_out.values()))
    assert sample_fields["step"].value == 600
    # prompt surfaced as the integer sample index — no index→text resolution.
    assert sample_fields["prompt"].value == 3


def test_subfolder_extract(tmp_path: Path) -> None:
    """META-02: parent dir → prompt (source=subfolder); a deeper step_<N> path
    segment yields step at source=subfolder (lowest precedence)."""
    from sample_grid.core.parse.subfolder import SubfolderExtractor

    flat = tmp_path / "a_lake" / "whatever.png"
    flat.parent.mkdir(parents=True, exist_ok=True)
    flat.write_bytes(b"x")

    deep = tmp_path / "a_city" / "step_500" / "x.png"
    deep.parent.mkdir(parents=True, exist_ok=True)
    deep.write_bytes(b"x")

    out = SubfolderExtractor().extract([flat, deep])

    flat_key = next(k for k in out if k.endswith("whatever.png"))
    assert out[flat_key]["prompt"].value == "a_lake"
    assert out[flat_key]["prompt"].source == "subfolder"

    deep_key = next(k for k in out if k.endswith("x.png"))
    assert out[deep_key]["step"].value == 500
    assert out[deep_key]["step"].source == "subfolder"
    assert out[deep_key]["prompt"].value == "a_city"


# ---------------------------------------------------------------------------
# Task 1: Scanner.scan_sidecars — surface sidecars WITHOUT polluting the media
# index (META-03 blocker / Pitfall 6).
# ---------------------------------------------------------------------------

_SIDECAR_SUFFIXES = {".json", ".csv", ".jsonl", ".txt", ".caption"}


def _seed_sidecar_folder(tmp_path: Path) -> Path:
    """A single folder holding image media alongside all three sidecar shapes."""
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "img_0.png").write_bytes(b"\x89PNG stub")
    (folder / "img_1.png").write_bytes(b"\x89PNG stub")
    (folder / "meta.json").write_text("{}", encoding="utf-8")
    (folder / "metadata.csv").write_text("file_name\n", encoding="utf-8")
    (folder / "cap.txt").write_text("a prompt", encoding="utf-8")
    return folder


def test_scan_sidecars(tmp_path: Path) -> None:
    """``scan_sidecars`` surfaces .json/.csv/.txt sidecars, deterministically
    posix-sorted, without needing the media allowlist."""
    from sample_grid.core.scan import Scanner

    _seed_sidecar_folder(tmp_path)
    sidecars = Scanner().scan_sidecars(tmp_path)

    assert sorted(p.name for p in sidecars) == ["cap.txt", "meta.json", "metadata.csv"]
    # Deterministic posix-normalized order (mirrors Scanner.scan).
    posix = [p.as_posix() for p in sidecars]
    assert posix == sorted(posix)


def test_sidecar_never_a_cell(tmp_path: Path) -> None:
    """The media scan (Scanner.scan) excludes every sidecar file — sidecars never
    enter the media SampleIndex (Pitfall 6)."""
    from sample_grid.core.scan import Scanner

    _seed_sidecar_folder(tmp_path)
    media = Scanner().scan(tmp_path)

    # Only image media, no sidecar suffix leaks in.
    assert media, "expected the image media to still be discovered"
    assert all(p.suffix.lower() not in _SIDECAR_SUFFIXES for p in media)
    assert any(p.name == "img_0.png" for p in media)


# ---------------------------------------------------------------------------
# Task 2: SidecarExtractor — three association shapes + aliases + graceful skip
# (META-03 / D-03 highest precedence).
# ---------------------------------------------------------------------------


def _sidecar_extractor(folder: Path):
    """Build a SidecarExtractor over a folder's scanned sidecars (root-confined)."""
    from sample_grid.core.parse.sidecar import SidecarExtractor
    from sample_grid.core.scan import Scanner

    return SidecarExtractor(Scanner().scan_sidecars(folder), root=folder)


def test_sidecar_json(sidecar_json_folder: Path) -> None:
    """A per-file ``<stem>.json`` with case-varied alias keys → sidecar FieldValues."""
    from sample_grid.core.scan import Scanner

    media = Scanner().scan(sidecar_json_folder)
    out = _sidecar_extractor(sidecar_json_folder).extract(media)

    (fields,) = out.values()
    assert fields["step"].value == 800          # "Steps" alias, case-insensitive
    assert fields["step"].source == "sidecar"
    assert fields["seed"].value == 42           # "noise_seed" alias
    assert fields["prompt"].value == "a serene lake"  # "positive_prompt" alias
    assert fields["prompt"].source == "sidecar"


def test_sidecar_csv_comma(sidecar_csv_folder: Path) -> None:
    """A ``metadata.csv`` row keyed by ``file_name`` with a comma-containing prompt
    survives INTACT (proves csv.DictReader, not line.split(',') — Pitfall 4)."""
    from sample_grid.core.scan import Scanner

    media = Scanner().scan(sidecar_csv_folder)
    out = _sidecar_extractor(sidecar_csv_folder).extract(media)

    key0 = next(k for k in out if k.endswith("img_0.png"))
    assert out[key0]["prompt"].value == "a lake, at dusk, cinematic"
    assert out[key0]["step"].value == 500
    assert out[key0]["seed"].value == 42
    assert out[key0]["prompt"].source == "sidecar"


def test_sidecar_per_folder(per_folder_meta_folder: Path) -> None:
    """A folder-level ``meta.json`` applies its dims to EVERY media file in it."""
    from sample_grid.core.scan import Scanner

    media = Scanner().scan(per_folder_meta_folder)
    out = _sidecar_extractor(per_folder_meta_folder).extract(media)

    assert len(out) == 2  # both frames picked up the folder sidecar
    for fields in out.values():
        assert fields["step"].value == 1200  # "global_step" alias
        assert fields["seed"].value == 7
        assert fields["prompt"].value == "a serene lake"
        assert fields["step"].source == "sidecar"


def test_sidecar_caption(caption_txt_folder: Path) -> None:
    """A per-file ``<stem>.txt`` whose whole contents are the prompt → prompt field."""
    from sample_grid.core.scan import Scanner

    media = Scanner().scan(caption_txt_folder)
    out = _sidecar_extractor(caption_txt_folder).extract(media)

    (fields,) = out.values()
    assert fields["prompt"].value == "a lone figure on a snowy ridge, wide shot"
    assert fields["prompt"].source == "sidecar"


def test_sidecar_malformed_skipped(malformed_sidecar_folder: Path) -> None:
    """A corrupt JSON sidecar is skipped and counted — extract() never raises (D-05)."""
    from sample_grid.core.scan import Scanner

    media = Scanner().scan(malformed_sidecar_folder)
    ext = _sidecar_extractor(malformed_sidecar_folder)

    out = ext.extract(media)  # must not raise

    # The broken sidecar produced no dims for its media file.
    assert all("step" not in f for f in out.values())
    # ...and it was recorded as skipped rather than silently swallowed.
    assert any("broken_1" in s for s in ext.skipped)
