"""The per-rule figures of the last judged precision runs, one set for each corpus, for the rule pages and
``brevio-core explain``.

Generated from the ``precision.json`` of each corpus by the precision run; do not edit by hand. A test fails when
this file and those disagree. ``low`` and ``high`` are the interval of ``real`` out of ``judged``.
"""
CORPORA: dict[str, dict[str, str | None]] = {
    "ts-react-oss": {"language": "typescript", "date": "2026-11-06", "scanner": "d11bdcf0ad03", "reviewer": None, "interval": "Wilson score interval, 95%"},
}
FIGURES: dict[str, dict[str, dict[str, int | float | None]]] = {
    "R2.8": {
        "ts-react-oss": {"hits": 57, "judged": 20, "real": 20, "precision": 1.0, "low": 0.8389, "high": 1.0},
    },
    "R5.4": {
        "ts-react-oss": {"hits": 78, "judged": 20, "real": 15, "precision": 0.75, "low": 0.5313, "high": 0.8881},
    },
    "R6.3": {
        "ts-react-oss": {"hits": 135, "judged": 20, "real": 6, "precision": 0.3, "low": 0.1455, "high": 0.519},
    },
    "R9.1": {
        "ts-react-oss": {"hits": 987, "judged": 20, "real": 20, "precision": 1.0, "low": 0.8389, "high": 1.0},
    },
    "R9.2": {
        "ts-react-oss": {"hits": 334, "judged": 20, "real": 20, "precision": 1.0, "low": 0.8389, "high": 1.0},
    },
    "R13.1": {
        "ts-react-oss": {"hits": 0, "judged": 0, "real": 0, "precision": None, "low": None, "high": None},
    },
    "R18.1": {
        "ts-react-oss": {"hits": 8, "judged": 8, "real": 7, "precision": 0.875, "low": 0.5291, "high": 0.9776},
    },
    "R18.5": {
        "ts-react-oss": {"hits": 7, "judged": 7, "real": 6, "precision": 0.8571, "low": 0.4869, "high": 0.9743},
    },
}
