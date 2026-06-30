"""Path helpers — forward-slash normalization and root-confinement.

Two cross-cutting jobs:
  * ``to_posix`` normalizes any path to forward slashes at the HTML/URL boundary
    so a Windows-authored run never leaks ``src="assets\\step.png"`` (Pitfall 5/9).
  * ``confine`` keeps filesystem walking inside the user-supplied root and rejects
    ``..`` traversal escapes (ASVS V12 — the habit P4's served ``/media`` needs).
All path handling uses ``pathlib``; never string-concatenate paths.
"""
from __future__ import annotations

from pathlib import Path, PurePath


def to_posix(path: "str | PurePath") -> str:
    """Return ``path`` with forward slashes, regardless of authoring OS."""
    return PurePath(path).as_posix()


def confine(root: Path, candidate: Path) -> Path:
    """Resolve ``candidate`` and assert it stays within ``root``.

    Raises ``ValueError`` if the resolved candidate escapes the resolved root
    (e.g. via ``..`` traversal). Returns the resolved candidate on success.
    """
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    if candidate_resolved != root_resolved and root_resolved not in candidate_resolved.parents:
        raise ValueError(f"path {candidate!r} escapes root {root!r}")
    return candidate_resolved
