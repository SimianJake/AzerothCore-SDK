# Protos (copy)

These `.proto` files define the gRPC API served by the [mod-acsdk](https://github.com/SimianJake/mod-acsdk) AzerothCore module, which owns
them. This is a copy of mod-acsdk 1.1.0's `proto/`, used to generate the Python stubs. When the module's API
changes, copy its `proto/` folder here and release a matching SDK version. `tests/test_proto_pinned.py` checks
the copy against a mod-acsdk checkout next to this repository (or `ACSDK_MODULE_DIR`).

Comments that mention `docs/...` refer to the mod-acsdk repository.
