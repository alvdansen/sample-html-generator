"""Folder walk / media discovery — the only component that touches the disk.

The scanner walks the user-supplied root (confined, no ``..`` escapes), keeps
only files whose suffix is in the image allowlist (MEDIA-02 — video arrives in
P3), and returns a deterministically sorted list of paths.
"""
from __future__ import annotations

from pathlib import Path

from sample_grid.util.paths import confine

# MEDIA-02 image allowlist (lowercase). Video extensions land in Phase 3.
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class Scanner:
    """Walks a root folder and returns candidate image files."""

    def __init__(self, allowed_extensions: "frozenset[str] | None" = None) -> None:
        self.allowed_extensions = allowed_extensions or IMAGE_EXTENSIONS

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
