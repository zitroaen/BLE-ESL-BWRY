"""Check that every link between the documents points somewhere.

The documentation is split across a short README and the files under
docs/, which means links cross directories - and a moved section leaves
behind a link that still looks fine in a diff. This is cheap to check and
was worth having the first time a section moved.

Run from the repository root:

    python scripts/check_links.py
"""

from __future__ import annotations

import pathlib
import re
import sys

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
EXTERNAL = ("http://", "https://", "mailto:")

FILES = (
    [pathlib.Path("README.md")]
    + sorted(pathlib.Path("docs").glob("*.md"))
    + [pathlib.Path("examples/README.md")]
)


def _anchors(path: pathlib.Path) -> set[str]:
    """Return the slugs GitHub generates for a file's headings."""
    found = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            slug = re.sub(r"[^\w\s-]", "", line.lstrip("#").strip().lower())
            found.add(slug.replace(" ", "-"))
    return found


def main() -> int:
    """Report every link that does not resolve."""
    broken: list[str] = []

    for source in FILES:
        for target in LINK.findall(source.read_text(encoding="utf-8")):
            if target.startswith(EXTERNAL):
                continue
            path_part, _, anchor = target.partition("#")
            destination = source
            if path_part:
                destination = (source.parent / path_part).resolve()
                if not destination.exists():
                    broken.append(f"{source}: {target} - no such file")
                    continue
            if (
                anchor
                and destination.suffix == ".md"
                and anchor not in _anchors(destination)
            ):
                broken.append(f"{source}: {target} - no such heading")

    for line in broken:
        print(line, file=sys.stderr)
    if broken:
        print(f"\n{len(broken)} broken link(s)", file=sys.stderr)
        return 1
    print(f"{len(FILES)} documents: every link resolves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
