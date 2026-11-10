"""The `ci` gate job in `ci.yml` has to depend on every other job there.

The main branch ruleset requires one check, `ci`, and nothing else. A job left out of
the gate's `needs` still runs on every pull request, and its failure blocks nothing.
"""

from pathlib import Path

import yaml

CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def _jobs() -> dict:
    return yaml.safe_load(CI.read_text())["jobs"]


def test_the_gate_reports_as_ci_and_always_runs():
    gate = _jobs()["gate"]
    assert gate["name"] == "ci", "the ruleset requires a check named `ci`"
    # A gate skipped because an upstream job failed never reports a conclusion, and a
    # required check with no conclusion leaves the pull request waiting forever.
    assert gate["if"] == "always()"


def test_the_gate_needs_every_other_job():
    jobs = _jobs()
    needs = jobs["gate"]["needs"]
    needs = [needs] if isinstance(needs, str) else needs
    assert sorted(needs) == sorted(set(jobs) - {"gate"})
