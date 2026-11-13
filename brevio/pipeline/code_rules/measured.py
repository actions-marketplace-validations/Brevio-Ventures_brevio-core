"""The per-rule figures of the last judged precision run, for the rule pages and ``brevio-core explain``.

Generated from ``precision.json`` by the precision run; do not edit by hand. A test fails when this
file and that one disagree.
"""
RUN: dict[str, str] = {"corpus": "ts-react-oss", "date": "2026-11-06"}
RULES: dict[str, dict[str, int | float | None]] = {
    "R2.8": {"hits": 57, "judged": 20, "real": 20, "precision": 1.0},
    "R5.4": {"hits": 78, "judged": 20, "real": 15, "precision": 0.75},
    "R6.3": {"hits": 135, "judged": 20, "real": 6, "precision": 0.3},
    "R9.1": {"hits": 987, "judged": 20, "real": 20, "precision": 1.0},
    "R9.2": {"hits": 334, "judged": 20, "real": 20, "precision": 1.0},
    "R13.1": {"hits": 0, "judged": 0, "real": 0, "precision": None},
    "R18.1": {"hits": 8, "judged": 8, "real": 7, "precision": 0.875},
    "R18.5": {"hits": 7, "judged": 7, "real": 6, "precision": 0.8571},
}
