# Builds the pip-installable Python SDK (wheel + sdist) into dist\, and optionally adds it to a static
# pip index folder you can serve (see docs\PUBLISHING.md).
#   .\scripts\build_sdk.ps1                      # dist\azerothcore_sdk-<version>-py3-none-any.whl + .tar.gz
#   .\scripts\build_sdk.ps1 -Index C:\acsdk-index # also copy into a PEP 503 index (serve that folder)
# The version comes from python\azerothcore_sdk\__init__.py (__version__). Stubs are regenerated from proto\.
param(
    [string] $OutDir = "",
    [string] $Index = ""
)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\env.ps1"

$python = Join-Path $RepoRoot "python\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Create the venv first: py -3.12 -m venv python\.venv; python\.venv\Scripts\pip install -e python[dev]" }
if (-not $OutDir) { $OutDir = Join-Path $RepoRoot "dist" }

& $python -m pip install -q "build>=1.2" "twine>=5"
if ($LASTEXITCODE -ne 0) { throw "could not install build/twine" }

# python -m build makes the sdist, then builds the wheel from it (so the sdist is proven installable).
& $python -m build (Join-Path $RepoRoot "python") --outdir $OutDir
if ($LASTEXITCODE -ne 0) { throw "build failed" }
& $python -m twine check (Join-Path $OutDir "*")
if ($LASTEXITCODE -ne 0) { throw "twine check failed" }

if ($Index) {
    & $python (Join-Path $PSScriptRoot "make_index.py") $OutDir $Index
    if ($LASTEXITCODE -ne 0) { throw "index update failed" }
}
Get-ChildItem $OutDir | Format-Table Name, Length
