"""Check services.yaml and the translations the way hassfest does.

hassfest only runs in CI, inside a Docker image, so a services.yaml
mistake used to surface as a red build minutes after the push. These are
the rules it applied to this integration, reimplemented against the
Home Assistant package that is already installed for the tests:

  * a service target must not carry a `device` key at all - a device
    picker belongs in `fields` as a device selector
  * every `selector:` must be a selector Home Assistant actually knows
  * every service and every field needs a name and description in
    strings.json and in each translation

Run from the repository root:

    python scripts/check_services.py
"""

from __future__ import annotations

import json
import pathlib
import sys

import voluptuous as vol
import yaml
from homeassistant.helpers import selector

BASE = pathlib.Path("custom_components/esl_zhsunyco")
STRING_FILES = ("strings.json", "translations/en.json", "translations/de.json")


def _check_target(name: str, target: dict) -> list[str]:
    """A device filter on a target is rejected by hassfest outright."""
    if "device" in target:
        return [
            f"{name}.target has a device key; hassfest rejects device filters "
            f"on targets, put a device selector in fields instead"
        ]
    try:
        selector.TargetSelector.CONFIG_SCHEMA(target)
    except vol.Invalid as err:
        return [f"{name}.target: {err}"]
    return []


def _check_translations(services: dict, path: pathlib.Path) -> list[str]:
    """Every service and field must be named in this translation file."""
    errors: list[str] = []
    entries = json.loads(path.read_text()).get("services", {})

    for name, spec in services.items():
        spec = spec or {}
        entry = entries.get(name)
        if entry is None:
            errors.append(f"{path.name}: no entry for service {name}")
            continue
        for key in ("name", "description"):
            if key not in entry:
                errors.append(f"{path.name}: {name} has no {key}")
        translated = entry.get("fields") or {}
        for field in spec.get("fields") or {}:
            if field not in translated:
                errors.append(f"{path.name}: {name}.fields has no {field}")
        for field in translated:
            if field not in (spec.get("fields") or {}):
                errors.append(f"{path.name}: {name}.fields has stale {field}")

    for name in entries:
        if name not in services:
            errors.append(f"{path.name}: {name} is translated but not defined")
    return errors


def main() -> int:
    """Report every problem at once rather than the first one."""
    services = yaml.safe_load((BASE / "services.yaml").read_text())
    errors: list[str] = []

    for name, spec in services.items():
        spec = spec or {}
        if "target" in spec:
            errors += _check_target(name, spec["target"])
        for field, config in (spec.get("fields") or {}).items():
            if "selector" not in config:
                continue
            try:
                selector.selector(config["selector"])
            except vol.Invalid as err:
                errors.append(f"{name}.{field} selector: {err}")

    for name in STRING_FILES:
        errors += _check_translations(services, BASE / name)

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"services.yaml and {len(STRING_FILES)} translation files: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
