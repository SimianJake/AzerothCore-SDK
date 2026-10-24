# Publishing the Python SDK

For the server owner: how to build `azerothcore-sdk` as a pip package, host ("serve") it so other
people can `pip install` it, and what to hand them. The person installing it follows
[installation.md](installation.md).

The package is pure Python (`py3-none-any`), so one wheel works on Windows, Linux and macOS,
Python 3.11+. Its only dependencies are `grpcio` and `protobuf`, which pip pulls from PyPI.

## 1. Build

From a checkout, with the dev venv (`py -3.12 -m venv python\.venv`, then
`python\.venv\Scripts\pip install -e "python[dev]"`):

```powershell
.\scripts\build_sdk.ps1
```

or on any OS:

```bash
python -m pip install build twine
python -m build python --outdir dist
python -m twine check dist/*
```

Output in `dist\`:

| File | What it is |
|---|---|
| `azerothcore_sdk-<version>-py3-none-any.whl` | What consumers install. |
| `azerothcore_sdk-<version>.tar.gz` | Source distribution. Carries the generated stubs, so it builds without `proto\`. |

You no longer need to run `gen_proto.ps1` first: `python/setup.py` regenerates the gRPC stubs from
`proto\` on every build, with the `grpcio-tools` version pinned in `python/pyproject.toml`. That also
makes `pip install` from git or from a source folder work. (`gen_proto.ps1` still works for the
dev venv, and `package.ps1` still puts a wheel in the release zip.)

## 2. Versioning

The version has one source: `__version__` in `python/azerothcore_sdk/__init__.py`. The package
metadata reads it from there.

For each release:

1. Bump `__version__` (semantic versioning: patch for fixes, minor for new calls, major when
   existing code would break, for example an `acsdk.v2` proto package).
2. Commit and tag it: `git tag sdk-v1.0.0`.
3. Build (section 1) and publish (section 3).

An index will not take the same version twice. If you need to fix a release, bump the version.

When you bump `grpcio-tools` in `[build-system]`, raise the `grpcio` and `protobuf` minimums in
`[project] dependencies` too. The generated code refuses to import on older runtimes. With 1.84.0
those minimums are `grpcio>=1.84.0` and `protobuf>=7.35.1`.

## 3. Hosting options

Pick one. They are listed from least to most setup.

### A. Hand out the wheel file

Send the `.whl` file (or put it on a file share or a release page). Consumers run:

```bash
pip install azerothcore_sdk-1.0.0-py3-none-any.whl
pip install https://example.com/files/azerothcore_sdk-1.0.0-py3-none-any.whl   # pip accepts a URL too
```

Best for a handful of people. Upgrades are manual.

### B. Install straight from git

If consumers can read the repository (for example a private GitHub repo you added them to), there is
nothing to host:

```bash
pip install "azerothcore-sdk @ git+https://github.com/<owner>/<repo>.git@sdk-v1.0.0#subdirectory=python"
```

pip clones the tag, generates the stubs and installs. Consumers need git, and the first install
downloads `grpcio-tools` (a few MB) to build. In a `requirements.txt`, use the same line without
`pip install`.

### C. Serve your own package index (local network or a server)

This gives consumers a normal `pip install azerothcore-sdk` and `pip install -U` for upgrades.

**C1. Static index (no extra software).** `make_index.py` builds a PEP 503 "simple" index folder
that any static web server can serve:

```powershell
.\scripts\build_sdk.ps1 -Index C:\acsdk-index      # build and add this release to the index
python -m http.server 8080 --directory C:\acsdk-index
```

For a permanent host, point IIS, nginx, Caddy, an S3 bucket or GitHub Pages at the folder instead of
`http.server`. Each release is added next to the earlier ones, so old versions stay installable.

**C2. pypiserver (accepts uploads).** If you want to publish with `twine upload` from any machine:

```bash
pip install "pypiserver[passlib]"
htpasswd -c htpasswd.txt jacob        # or: python -c "from passlib.apache import HtpasswdFile as H; h=H('htpasswd.txt', new=True); h.set_password('jacob','<pw>'); h.save()"
pypi-server run -p 8080 -P htpasswd.txt -a update C:\acsdk-packages
twine upload --repository-url http://<host>:8080/ dist/*
```

With `-a update`, anyone can download and only accounts in `htpasswd.txt` can upload.

**Consumers of C1 or C2** run:

```bash
pip install --extra-index-url http://<host>:8080/simple/ azerothcore-sdk   # pypiserver
pip install --extra-index-url http://<host>:8080/ azerothcore-sdk          # static folder served at /
```

`--extra-index-url` keeps PyPI available for `grpcio` and `protobuf`. To avoid typing it, consumers
can set it once with `pip config set global.extra-index-url http://<host>:8080/simple/`. If the host
is plain `http` and not `localhost`, newer pip also needs `--trusted-host <host>`. Use HTTPS
(a reverse proxy such as Caddy) for anything beyond your own network.

A caution on `--extra-index-url`: pip picks the highest version across all indexes. If someone
else ever registers `azerothcore-sdk` on PyPI, they could shadow yours. Pinning the version
(`azerothcore-sdk==1.0.0`) limits that risk. Claiming the name on PyPI (option D) removes it.

### D. PyPI (public)

Use this when anyone should be able to `pip install azerothcore-sdk` with no extra flags.

1. Create accounts on <https://test.pypi.org> and <https://pypi.org>, turn on 2FA, and create an
   API token for each.
2. Do a trial run on TestPyPI:
   ```bash
   twine upload --repository testpypi dist/*
   pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ azerothcore-sdk
   ```
3. Publish for real: `twine upload dist/*` (user `__token__`, password the API token).

Before publishing publicly:

- Add a license. There is none yet, and without one others have no right to use the code. Put a
  `LICENSE` file in `python/` and set `license = "MIT"` (or your choice) in `pyproject.toml`.
  AzerothCore itself is GPL-2.0. The SDK talks to it over the network, so the client is not bound
  by the GPL, but `mod-acsdk` (linked into worldserver) is.
- Add `[project.urls]` (repository, docs) so the PyPI page links somewhere.
- PyPI does not allow re-uploading or reusing a deleted version.

A GitHub Actions workflow with PyPI "trusted publishing" can replace the token later. This setup
does not need it.

## 4. What consumers need

Send them [installation.md](installation.md), `SDK_GUIDE.md`, the `.whl` file (or your index
address) and, if you like, the `examples` folder. Separately and privately, give them:

- the **server address**, and how to reach it. The SDK listens on `127.0.0.1:50051` by default, so
  only the server PC can connect, and the connection is not encrypted. For other machines use an
  **SSH tunnel** (`ssh -N -L 50051:127.0.0.1:50051 user@game-server`, with no config change), or a
  **VPN** such as Tailscale with `ACSDK.BindAddress` set to the VPN address. Use
  `ACSDK.BindAddress = 0.0.0.0` only on a trusted LAN with the firewall limited to known hosts, and
  never expose port 50051 to the internet;
- the **token** (`ACSDK.AuthToken` from `configs\modules\mod_acsdk.conf`). It is shared by everyone,
  so anyone who has it can drive every headless character;
- the **character names** they may drive. These must be on accounts whose names start with
  `ACSDK.HeadlessAccountPrefix` (default `acsdkhc`), and the accounts must be player-level, not GM.

## 5. Compatibility

The SDK speaks the `acsdk.v1` protocol, so it connects to any `mod-acsdk` build that serves
`acsdk.v1`. A call added in a newer SDK can fail against an older server build, so ship the wheel
and the server from the same commit when you can (`package.ps1` does that). `await ac.server.info()`
reports the server's `api_version` and `sdk_version` if you need to check.
