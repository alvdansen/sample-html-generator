"""Template-override metadata extraction (META-04, the explicit escape hatch).

When auto-detect guesses a grouping wrong, ``--template`` states the layout
explicitly. ``compile_template`` turns a template DSL string into a single, linear
``re`` pattern; ``TemplateParser`` implements the pinned ``Extractor`` contract
(``extract(files) -> {rel_id: {field: FieldValue}}``, source ``"template"``) by
matching that pattern against each media file's RELATIVE POSIX path (D-08) —
never the basename. Captured fields carry ``source="template"`` and win at
precedence 4 (``SOURCE_PRECEDENCE``), so a supplied template wins for the fields
it names and auto-detect (incl. sidecar) fills only the gaps (A1).

Noise grammar (RESEARCH § Pattern C):
  * Non-placeholder segments are ``re.escape``d and must match literally — the
    literal separators are what disambiguate adjacent numeric fields and absorb
    known junk (``step_``, ``_seed``, ``.mp4``).
  * ``{step}``/``{seed}``/``{cfg}`` → numeric ``(?P<name>\\d+)`` (values coerced
    to ``int``).
  * ``{prompt}``/``{model}``/``{checkpoint}`` and any unknown field → non-greedy,
    path-segment-bounded ``(?P<name>[^/]+?)``. Unknown fields are captured but
    stay unmapped to axes and must NOT raise (D-07).
  * ``{*}`` → non-capturing ``.*?`` explicit ignore token — absorbs dates /
    sample indices the user does not care about.
  * ``re.fullmatch`` anchored with ``\\Z``; a non-match is skipped and counted
    (Pitfall 6 / D-05), never force-grouped.

Patterns stay linear — no nested quantifiers over ``.*`` — so a hostile
``--template`` run over many/long paths cannot trigger catastrophic backtracking
(ReDoS, T-02-06). No ``eval``/``exec``: the template compiles via ``re`` only.
"""
from __future__ import annotations

import re
from pathlib import Path

from sample_grid.core.parse.base import FieldValue
from sample_grid.util.paths import to_posix

# Placeholder grammar: ``{name}`` (a field) or the ``{*}`` ignore token.
_FIELD_RE = re.compile(r"\{(\w+|\*)\}")

# Fields compiled as bare digit runs; their captured values coerce to ``int``.
_NUMERIC = {"step", "seed", "cfg"}

# An explicit user override — captured fields are authoritative by construction.
_CONF_HIGH = 1.0


def compile_template(tpl: str) -> "re.Pattern[str]":
    """Compile a ``--template`` DSL string to a single anchored regex (Pattern C).

    Walks ``tpl`` emitting ``re.escape``d literal segments between placeholders,
    ``.*?`` for the ``{*}`` ignore token, ``(?P<name>\\d+)`` for numeric fields
    (``step``/``seed``/``cfg``), and a non-greedy path-segment-bounded
    ``(?P<name>[^/]+?)`` for every other (incl. unknown, D-07) field. Anchors the
    whole pattern with ``\\Z`` and returns the compiled ``re.Pattern`` to match
    via ``re.fullmatch``. No nested quantifiers over ``.*`` → ReDoS-safe.
    """
    out: list[str] = []
    last = 0
    for m in _FIELD_RE.finditer(tpl):
        out.append(re.escape(tpl[last : m.start()]))  # literal segment
        name = m.group(1)
        if name == "*":
            out.append(r".*?")
        elif name in _NUMERIC:
            out.append(rf"(?P<{name}>\d+)")
        else:
            out.append(rf"(?P<{name}>[^/]+?)")
        last = m.end()
    out.append(re.escape(tpl[last:]))  # trailing literal (e.g. the extension)
    return re.compile("".join(out) + r"\Z")


class TemplateParser:
    """META-04 override implementing the ``Extractor`` contract (source=template).

    Compiles ``template`` once and matches it (``re.fullmatch``) against each media
    file's ``relative_to(root).as_posix()`` path (D-08). On a match it emits one
    ``FieldValue(value, "template", 1.0)`` per captured group — numeric fields
    coerced to ``int`` — keyed by that same relative-posix ``rel_id`` the other
    extractors use, so template fields MERGE onto the same file and win at
    precedence 4 (A1). A file whose path does not fullmatch is skipped and recorded
    on ``self.skipped`` (the pinned contract carries no report), never grouped.
    """

    def __init__(self, template: str, root: Path) -> None:
        self.template = template
        self.root = Path(root)
        self.pattern = compile_template(template)
        # Non-matching files (the pinned extract() contract carries no report).
        self.skipped: list[str] = []

    def extract(self, files: list[Path]) -> "dict[str, dict[str, FieldValue]]":
        out: dict[str, dict[str, FieldValue]] = {}
        for file in files:
            file = Path(file)
            try:
                rel = to_posix(file.relative_to(self.root))
            except ValueError:
                # File outside root — fall back to its posix form so the match
                # still runs deterministically rather than raising.
                rel = to_posix(file)

            match = self.pattern.fullmatch(rel)
            if match is None:
                # Fail loud, not silent: a non-match is skipped + counted (D-05).
                self.skipped.append(rel)
                continue

            fields: dict[str, FieldValue] = {}
            for name, value in match.groupdict().items():
                if value is None:
                    continue
                coerced = int(value) if name in _NUMERIC else value
                fields[name] = FieldValue(coerced, "template", _CONF_HIGH)
            out[rel] = fields
        return out
