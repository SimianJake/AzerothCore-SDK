"""Build a static pip package index (PEP 503 "simple" layout) from a folder of wheels/sdists.

    python scripts/make_index.py <dist-folder> <index-folder>

Copies every *.whl / *.tar.gz into <index-folder>/<project>/ and writes the index.html pages, adding to
what is already there, so each release can be dropped into the same index. Serve <index-folder> with any
static web server (python -m http.server, IIS, nginx, S3, GitHub Pages) and install with
    pip install --index-url http://<host>/simple/ azerothcore-sdk
where <index-folder> is served at /simple/. Uses only the standard library.
"""

from __future__ import annotations

import hashlib
import html
import re
import shutil
import sys
from pathlib import Path


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def project_of(filename: str) -> str:
    # wheel: name-version-...whl; sdist: name-version.tar.gz (name never contains "-" once built)
    return normalize(filename.split("-", 1)[0])


def page(title: str, links: list[tuple[str, str]]) -> str:
    rows = "\n".join(f'    <a href="{html.escape(href)}">{html.escape(text)}</a><br>' for href, text in links)
    return (f"<!DOCTYPE html>\n<html>\n  <head><meta name=\"pypi:repository-version\" content=\"1.0\">"
            f"<title>{html.escape(title)}</title></head>\n  <body>\n{rows}\n  </body>\n</html>\n")


def main(dist: Path, index: Path) -> None:
    files = sorted([*dist.glob("*.whl"), *dist.glob("*.tar.gz")])
    if not files:
        sys.exit(f"no .whl or .tar.gz files in {dist}")
    for f in files:
        target = index / project_of(f.name)
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target / f.name)

    projects = sorted(p for p in index.iterdir() if p.is_dir())
    for proj in projects:
        links = []
        for f in sorted([*proj.glob("*.whl"), *proj.glob("*.tar.gz")]):
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            links.append((f"{f.name}#sha256={digest}", f.name))
        (proj / "index.html").write_text(page(f"Links for {proj.name}", links), encoding="utf-8")
    (index / "index.html").write_text(page("Simple index", [(f"{p.name}/", p.name) for p in projects]),
                                      encoding="utf-8")
    print(f"index: {index} ({', '.join(p.name for p in projects)})")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
