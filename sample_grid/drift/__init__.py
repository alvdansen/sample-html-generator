"""Composition-drift ("plasticity") metric over seed-locked training ladders.

Ported from the validated ``mima_ltx2/plasticity_gate/drift_metric.py``
reference implementation (validated 2026-07-12 on the mima2 T1 chain + prime
ladders). The metric layer (``metric.py``) is numerically identical to the
reference; ``collect.py`` generalizes the ladder/naming discovery; ``analyze.py``
adds the validated guardrails (high-motion cell exclusion, knee detection);
``report.py`` renders the dark-theme SVG report.
"""
