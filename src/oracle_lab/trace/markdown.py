"""The summarized trace a rater sees, as Markdown for the judge.

Rendered from saved trace JSON (`judge/trace.json`), so the judge and the review
page show the same steps with the same numbers.
"""

from __future__ import annotations


def trace_markdown(trace: dict) -> str:
    """One line per event (step, action type, description, and its tags), then each
    generated test file's versions with the pytest runs between them."""
    raise NotImplementedError
