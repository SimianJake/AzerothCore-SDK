"""The proto/ copy here must match the mod-acsdk module that owns the protos (proto/README.md).

Needs no server. Looks for the module next to this repository (../mod-acsdk) or at ACSDK_MODULE_DIR; skipped if
neither exists.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MODULE = Path(os.environ.get("ACSDK_MODULE_DIR", REPO.parent / "mod-acsdk"))


def _protos(root: Path) -> dict[str, str]:
    base = root / "proto"
    # Line endings depend on the checkout (core.autocrlf), not on the API.
    return {p.relative_to(base).as_posix(): p.read_text(encoding="utf-8").replace("\r\n", "\n")
            for p in sorted(base.rglob("*.proto"))}


@pytest.mark.skipif(not (MODULE / "proto").is_dir(), reason=f"mod-acsdk not found at {MODULE} (set ACSDK_MODULE_DIR)")
def test_pinned_protos_match_the_module() -> None:
    ours, theirs = _protos(REPO), _protos(MODULE)
    assert sorted(ours) == sorted(theirs), "proto file sets differ; copy mod-acsdk's proto/ folder here"
    differing = [name for name in ours if ours[name] != theirs[name]]
    assert not differing, f"these protos differ from {MODULE / 'proto'}: {differing}; copy mod-acsdk's proto/ folder here"
