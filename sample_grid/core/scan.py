"""Folder walk / media discovery — the only component that touches the disk.

The scanner walks the user-supplied root (confined, no ``..`` escapes), keeps
only files whose suffix is in the media allowlist (MEDIA-02 images + MEDIA-01
video, live in P3), and returns a deterministically sorted list of paths.
"""
from __future__ import annotations

from pathlib import Path

from sample_grid.util.paths import confine

# MEDIA-02 image allowlist (lowercase).
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})

# MEDIA-01 video allowlist (lowercase, web-native only — transcoding is out of
# scope). Video is a first-class media type from Phase 3 onward.
VIDEO_EXTENSIONS = frozenset({".mp4", ".webm"})

# The full media allowlist the Scanner walks — images ∪ video.
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# META-03 sidecar allowlist (lowercase). Surfaced by ``scan_sidecars`` to the
# parser layer ONLY — deliberately DISJOINT from ``MEDIA_EXTENSIONS`` so a
# sidecar file can never enter the media ``SampleIndex`` (Pitfall 6).
SIDECAR_EXTENSIONS = frozenset({".json", ".csv", ".jsonl", ".txt", ".caption"})


def media_type_for(path: "str | Path") -> str:
    """Classify a media path by suffix — the SINGLE source of truth (MEDIA-01).

    Returns ``"video"`` for a ``.mp4``/``.webm`` suffix (case-insensitive), else
    ``"image"``. Do not inline the suffix check anywhere else — every producer of
    a ``Sample.media_type`` routes through this one helper so the classification
    can never drift between the scan, filename, and auto-detect parsers.
    """
    return "video" if Path(path).suffix.lower() in VIDEO_EXTENSIONS else "image"


class Scanner:
    """Walks a root folder and returns candidate image files."""

    def __init__(self, allowed_extensions: "frozenset[str] | None" = None) -> None:
        self.allowed_extensions = allowed_extensions or MEDIA_EXTENSIONS

    def scan(self, root: Path) -> list[Path]:
        """Return image files under ``root``, deterministically sorted.

        Raises ``ValueError`` if ``root`` does not exist or is not a directory.
        Walking is confined to ``root`` — symlinks/paths escaping it are rejected.
        """
        root = Path(root)
        if not root.is_dir():
            raise ValueError(f"not a directory: {root!r}")

        root_resolved = root.resolve()
        found: list[Path] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in self.allowed_extensions:
                continue
            # Root-confinement: reject anything resolving outside the root (V12).
            try:
                confine(root_resolved, path)
            except ValueError:
                continue
            found.append(path)

        # Deterministic order: posix-normalized path string.
        return sorted(found, key=lambda p: p.as_posix())

    def scan_sidecars(self, root: Path) -> list[Path]:
        """Return sidecar files under ``root``, deterministically sorted.

        Mirrors ``scan`` (same walk, same root-confinement, same posix sort) but
        with the ``SIDECAR_EXTENSIONS`` allowlist — ``.json/.csv/.jsonl/.txt/
        .caption``. These files feed the sidecar parser only; because the two
        allowlists are disjoint, sidecars never join the media ``SampleIndex``
        (META-03 / Pitfall 6). Walking stays confined to ``root`` (V12).
        """
        root = Path(root)
        if not root.is_dir():
            raise ValueError(f"not a directory: {root!r}")

        root_resolved = root.resolve()
        found: list[Path] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SIDECAR_EXTENSIONS:
                continue
            # Root-confinement: reject anything resolving outside the root (V12).
            try:
                confine(root_resolved, path)
            except ValueError:
                continue
            found.append(path)

        # Deterministic order: posix-normalized path string.
        return sorted(found, key=lambda p: p.as_posix())
