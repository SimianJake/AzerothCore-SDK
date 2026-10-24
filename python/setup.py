"""Build hook: regenerate the gRPC stubs in azerothcore_sdk/_generated from the .proto files.

Runs on every build (wheel, sdist, editable, pip install from git) so the stubs never go stale and no
PowerShell step is needed. Protos are read from ../proto (repo checkout). An sdist carries the
generated stubs instead, so building a wheel from it needs no protos. All metadata is in pyproject.toml.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from setuptools import setup

HERE = Path(__file__).resolve().parent
PROTO_ROOT = HERE.parent / "proto"
OUT = HERE / "azerothcore_sdk" / "_generated"


def generate_stubs() -> None:
    protos = sorted(p.relative_to(PROTO_ROOT).as_posix() for p in PROTO_ROOT.rglob("*.proto"))
    if not protos:
        if not (OUT / "acsdk").is_dir():
            sys.exit(f"no .proto files in {PROTO_ROOT} and no pre-generated stubs in {OUT}")
        return  # building from an sdist: stubs are already in the tree

    from grpc_tools import protoc  # build requirement (pyproject.toml [build-system])

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    well_known = Path(protoc.__file__).resolve().parent / "_proto"
    args = ["protoc", f"-I{PROTO_ROOT}", f"-I{well_known}",
            f"--python_out={OUT}", f"--pyi_out={OUT}", f"--grpc_python_out={OUT}", *protos]
    if protoc.main(args) != 0:
        sys.exit("protoc failed")
    # protoc emits absolute imports ("from acsdk.v1 import ..."); make the packages importable.
    for d in [OUT, *(p for p in OUT.rglob("*") if p.is_dir())]:
        (d / "__init__.py").touch()


generate_stubs()
setup()
