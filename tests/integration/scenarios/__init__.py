"""
Scenario registry.  Import every scenario module here so collect_all_scenarios()
picks them up automatically.  Adding a new file = new scenarios in the test run.
"""
from __future__ import annotations

from .all_question_types import SCENARIOS as _AQT
from .edge_cases import SCENARIOS as _EC

_ALL: list = [*_AQT, *_EC]


def collect_all_scenarios():
    """Return every registered GameScenario for pytest.mark.parametrize."""
    return _ALL
