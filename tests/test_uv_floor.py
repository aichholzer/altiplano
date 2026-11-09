"""Every uv this repository runs has to satisfy the floor in `pyproject.toml`.

`tool.uv.required-version` makes a uv below the floor exit before its first command.
CI, the publish workflow, and the Docker build each pin a uv of their own, by hand,
and a pin left behind fails only when its job next runs. This test fails first, on
the commit that moves the floor or the pin.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _release(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _floor() -> tuple[int, ...]:
    """The `>=X.Y[.Z]` floor from `pyproject.toml`. Any other shape is a failure here."""
    pyproject = (ROOT / "pyproject.toml").read_text()
    match = re.search(r'^required-version\s*=\s*">=([0-9][0-9.]*)"\s*$', pyproject, re.MULTILINE)
    assert match, "pyproject.toml sets no `required-version = \">=X.Y\"` under [tool.uv]"
    return _release(match.group(1))


def _pins() -> dict[str, list[tuple[int, ...]]]:
    """Every uv version the repository pins, grouped by the file that pins it."""
    pins: dict[str, list[tuple[int, ...]]] = {}
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        text = workflow.read_text()
        # `version:` within the `with:` block that follows each `setup-uv` step.
        found = re.findall(r'astral-sh/setup-uv@[^\n]*\n(?:[^\n]*\n){0,3}?\s*version:\s*"([0-9.]+)"', text)
        pins[workflow.name] = [_release(v) for v in found]
    dockerfile = (ROOT / "Dockerfile").read_text()
    pins["Dockerfile"] = [_release(v) for v in re.findall(r"ghcr\.io/astral-sh/uv:([0-9.]+)", dockerfile)]
    return pins


def test_every_file_that_runs_uv_pins_one():
    pins = _pins()
    unpinned = sorted(name for name in ("ci.yml", "publish.yml", "Dockerfile") if not pins.get(name))
    assert unpinned == [], f"no uv pin found in {unpinned}"


def test_every_pinned_uv_satisfies_the_floor():
    floor = _floor()
    below = sorted(
        f"{name} pins {'.'.join(map(str, release))}"
        for name, releases in _pins().items()
        for release in releases
        if release < floor
    )
    assert below == [], f"below the >={'.'.join(map(str, floor))} floor: {below}"
