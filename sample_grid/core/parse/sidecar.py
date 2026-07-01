"""Sidecar-file metadata extraction (META-03, highest-precedence source).

``SidecarExtractor`` implements the ``Extractor`` contract
(``extract(files) -> {rel_id: {field: FieldValue}}``, source ``"sidecar"``) —
explicit trainer-written metadata that wins over filename/subfolder inference
(D-03). It resolves the three real-world association shapes (RESEARCH Pattern B),
most-specific first, stopping at the first hit:

1. **Per-file:** ``<stem>.json`` / ``<stem>.txt`` / ``<stem>.caption`` next to the
   media (kohya caption convention; generic per-sample JSON).
2. **CSV / JSONL by ``file_name``:** a ``metadata.csv`` / ``metadata.jsonl`` in the
   media's folder whose ``file_name`` column matches the media's relative name —
   the HuggingFace ``imagefolder`` convention. Parsed with ``csv.DictReader``
   (NEVER ``line.split(',')``, Pitfall 4) so a comma-containing prompt survives.
3. **Per-folder:** ``meta.json`` / ``metadata.json`` in the media's folder,
   applying its dims to every media file in that folder.

Raw keys map to fields via case-insensitive alias tables (RESEARCH § Sidecar
Conventions). ``epoch`` is treated as step-like only when no ``step`` key is
present (A5). Every read is preceded by ``confine`` (root-confinement, T-02-04)
and wrapped in ``try/except Exception → skip + count`` (never raise, D-05 /
T-02-05); ``json.loads`` / ``csv.DictReader`` only — never ``eval``/``exec``
(Security V5). Embedded PNG metadata chunks are NOT read (A4 — out of scope).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from sample_grid.core.parse.base import FieldValue
from sample_grid.util.paths import confine, to_posix

# Explicit trainer metadata is high-confidence by construction.
_CONF_HIGH = 1.0

# Per-file sidecar suffixes (structured first, then caption).
_PERFILE_SUFFIXES = (".json", ".txt", ".caption")

# Case-insensitive alias tables (RESEARCH § Sidecar Conventions). ``epoch`` is
# kept separate: step-like only when no explicit step key exists (A5).
_STEP_KEYS = ("step", "steps", "global_step", "iteration", "iter")
_EPOCH_KEYS = ("epoch",)
_SEED_KEYS = ("seed", "noise_seed", "d")
_PROMPT_KEYS = ("prompt", "positive", "positive_prompt", "text", "caption", "p")
_CFG_KEYS = ("cfg", "cfg_scale", "guidance_scale", "l")
_MODEL_KEYS = ("model", "ckpt", "checkpoint", "base_model")


def load_csv_sidecar(csv_path: Path) -> "dict[str, dict]":
    """Return ``{file_name: row}`` from a ``metadata.csv`` (HF imagefolder convention).

    Uses ``csv.DictReader`` — NEVER ``line.split(',')`` — so a comma-containing
    prompt column is preserved intact (Pitfall 4).
    """
    rows: dict[str, dict] = {}
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = row.get("file_name") or row.get("filename")
            if name:
                rows[name] = row
    return rows


def _first(low: "dict[str, object]", keys: "tuple[str, ...]") -> object:
    """First present value among ``keys`` in the lowercased dict, else ``None``."""
    for k in keys:
        if k in low:
            return low[k]
    return None


def _to_int(value: object) -> "int | None":
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_float(value: object) -> "float | None":
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


class SidecarExtractor:
    """META-03: per-field detection from sidecar files (source=``sidecar``).

    Constructed with the sidecar file list (from ``Scanner.scan_sidecars``) and
    the scanned ``root`` used for confinement. ``extract`` associates each media
    file to at most one sidecar and returns its fields keyed by the media's stable
    posix ``rel_id`` (``"<parent_dir>/<file>"``) so the picker merges sidecar
    fields onto the same file the filename/subfolder extractors produced.
    """

    def __init__(self, sidecar_files: "list[Path]", root: Path) -> None:
        self.sidecars = [Path(p) for p in sidecar_files]
        self._root_resolved = Path(root).resolve()
        self.skipped: list[str] = []
        self._csv_cache: dict[Path, dict] = {}
        self._json_cache: dict[Path, "dict | None"] = {}

    # -- public contract ----------------------------------------------------

    def extract(self, files: "list[Path]") -> "dict[str, dict[str, FieldValue]]":
        out: dict[str, dict[str, FieldValue]] = {}
        for media in files:
            media = Path(media)
            raw = self._associate(media)
            if not raw:
                continue
            fields = self._map_fields(raw)
            if not fields:
                continue
            rel_id = to_posix(Path(media.parent.name) / media.name)
            out[rel_id] = fields
        return out

    # -- association (Pattern B: per-file → csv/jsonl → per-folder) ----------

    def _associate(self, media: Path) -> "dict | None":
        for shape in (self._per_file, self._csv_jsonl, self._per_folder):
            raw = shape(media)
            if raw:
                return raw
        return None

    def _per_file(self, media: Path) -> "dict | None":
        candidates: dict[str, Path] = {}
        for s in self.sidecars:
            if (
                s.parent == media.parent
                and s.stem == media.stem
                and s.suffix.lower() in _PERFILE_SUFFIXES
            ):
                candidates[s.suffix.lower()] = s
        for suf in _PERFILE_SUFFIXES:
            if suf in candidates:
                return self._read_perfile(candidates[suf], suf)
        return None

    def _csv_jsonl(self, media: Path) -> "dict | None":
        for s in self.sidecars:
            if s.parent != media.parent:
                continue
            name = s.name.lower()
            if name == "metadata.csv":
                hit = self._match_row(self._load_csv(s), media, s)
            elif name == "metadata.jsonl":
                hit = self._match_row(self._load_jsonl(s), media, s)
            else:
                continue
            if hit is not None:
                return hit
        return None

    def _per_folder(self, media: Path) -> "dict | None":
        for s in self.sidecars:
            if s.parent != media.parent:
                continue
            if s.name.lower() in ("meta.json", "metadata.json"):
                data = self._load_folder_json(s)
                if data is not None:
                    return data
        return None

    # -- guarded reads (confine + try/except → skip; never raise) -----------

    def _read_perfile(self, path: Path, suffix: str) -> "dict | None":
        try:
            confine(self._root_resolved, path)
            if suffix == ".json":
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                return data if isinstance(data, dict) else None
            # .txt / .caption — the ENTIRE contents are the prompt.
            text = path.read_text(encoding="utf-8").strip()
            return {"prompt": text} if text else None
        except Exception:
            self._skip(path)
            return None

    def _load_csv(self, path: Path) -> "dict[str, dict]":
        if path in self._csv_cache:
            return self._csv_cache[path]
        rows: dict[str, dict] = {}
        try:
            confine(self._root_resolved, path)
            rows = load_csv_sidecar(path)
        except Exception:
            self._skip(path)
            rows = {}
        self._csv_cache[path] = rows
        return rows

    def _load_jsonl(self, path: Path) -> "dict[str, dict]":
        if path in self._csv_cache:
            return self._csv_cache[path]
        rows: dict[str, dict] = {}
        try:
            confine(self._root_resolved, path)
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    if isinstance(row, dict):
                        name = row.get("file_name") or row.get("filename")
                        if name:
                            rows[name] = row
        except Exception:
            self._skip(path)
            rows = {}
        self._csv_cache[path] = rows
        return rows

    def _load_folder_json(self, path: Path) -> "dict | None":
        if path in self._json_cache:
            return self._json_cache[path]
        data: "dict | None" = None
        try:
            confine(self._root_resolved, path)
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            data = loaded if isinstance(loaded, dict) else None
        except Exception:
            self._skip(path)
            data = None
        self._json_cache[path] = data
        return data

    def _match_row(
        self, rows: "dict[str, dict]", media: Path, sidecar: Path
    ) -> "dict | None":
        if not rows:
            return None
        keys = {media.name}
        try:
            keys.add(to_posix(media.relative_to(sidecar.parent)))
        except ValueError:
            pass
        for k in keys:
            if k in rows:
                return rows[k]
        return None

    # -- key → field mapping (case-insensitive aliases) ---------------------

    def _map_fields(self, raw: "dict") -> "dict[str, FieldValue]":
        low = {str(k).lower(): v for k, v in raw.items()}
        fields: dict[str, FieldValue] = {}

        # step — explicit step keys first, epoch only as a fallback (A5).
        step_val = _first(low, _STEP_KEYS)
        if step_val is None:
            step_val = _first(low, _EPOCH_KEYS)
        if step_val is not None:
            iv = _to_int(step_val)
            if iv is not None:
                fields["step"] = FieldValue(iv, "sidecar", _CONF_HIGH)

        seed_val = _first(low, _SEED_KEYS)
        if seed_val is not None:
            iv = _to_int(seed_val)
            if iv is not None:
                fields["seed"] = FieldValue(iv, "sidecar", _CONF_HIGH)

        prompt_val = _first(low, _PROMPT_KEYS)
        if prompt_val is not None and str(prompt_val) != "":
            fields["prompt"] = FieldValue(str(prompt_val), "sidecar", _CONF_HIGH)

        cfg_val = _first(low, _CFG_KEYS)
        if cfg_val is not None:
            fv = _to_float(cfg_val)
            if fv is not None:
                fields["cfg"] = FieldValue(fv, "sidecar", _CONF_HIGH)

        model_val = _first(low, _MODEL_KEYS)
        if model_val is not None and str(model_val) != "":
            fields["model"] = FieldValue(str(model_val), "sidecar", _CONF_HIGH)

        return fields

    def _skip(self, path: Path) -> None:
        self.skipped.append(to_posix(path))
