# Generates Python gRPC stubs from proto\ into python\azerothcore_sdk\_generated (never hand-edit).
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\env.ps1"

$python = Join-Path $RepoRoot "python\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Create the venv first: py -3.12 -m venv python\.venv; python\.venv\Scripts\pip install -e python[dev]" }

$protoRoot = Join-Path $RepoRoot "proto"
$outDir    = Join-Path $RepoRoot "python\azerothcore_sdk\_generated"

if (Test-Path $outDir) { Remove-Item -Recurse -Force $outDir }
New-Item -ItemType Directory -Force $outDir | Out-Null

# Run from the proto root with relative paths: the repo path contains a space, which protoc's
# argument handling on Windows PowerShell 5.1 does not survive.
Push-Location $protoRoot
try {
    [string[]] $protos = Get-ChildItem -Recurse -Filter *.proto . | Resolve-Path -Relative | ForEach-Object { $_ -replace '^\.\\', '' -replace '\\', '/' }
    $outRel = Resolve-Path -Relative $outDir
    & $python -m grpc_tools.protoc "-I." "--python_out=$outRel" "--pyi_out=$outRel" "--grpc_python_out=$outRel" $protos
    if ($LASTEXITCODE -ne 0) { throw "protoc failed" }
} finally {
    Pop-Location
}

# protoc emits absolute imports ("from acsdk.v1 import ..."); make the packages importable.
Get-ChildItem -Recurse -Directory $outDir | ForEach-Object { New-Item -ItemType File -Force (Join-Path $_.FullName "__init__.py") | Out-Null }
New-Item -ItemType File -Force (Join-Path $outDir "__init__.py") | Out-Null

Write-Host "Generated stubs in $outDir"
