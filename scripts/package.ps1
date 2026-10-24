# Builds a release package: Python wheel + server binaries + config templates + protos/examples/docs.
#   .\scripts\package.ps1 -Name Beta-Build-ac-sdk
# Output: $BuildRoot\release\<Name>\ and $BuildRoot\release\<Name>.zip
# Never packages live configs (*.conf hold the ACSDK.AuthToken and DB credentials), only *.conf.dist.
param(
    [string] $Name = "Beta-Build-ac-sdk",
    [ValidateSet("RelWithDebInfo", "Debug", "Release")] [string] $Config = "RelWithDebInfo",
    [switch] $IncludeWorldserverPdb   # worldserver.pdb is ~770 MB; leave it out unless debugging crashes
)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\env.ps1"

$bin = Join-Path $AcBuild "bin\$Config"
$out = Join-Path $BuildRoot "release\$Name"
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force "$out\server\configs\modules", "$out\python" | Out-Null

# --- server binaries (mod-acsdk is linked into worldserver.exe) ---
$exes = "worldserver.exe", "authserver.exe", "dbimport.exe", "map_extractor.exe", "vmap4_extractor.exe", "vmap4_assembler.exe", "mmaps_generator.exe"
foreach ($f in $exes + @("libmysql.dll", "libssl-3-x64.dll", "libcrypto-3-x64.dll", "legacy.dll", "mmaps-config.yaml")) {
    Copy-Item (Join-Path $bin $f) "$out\server\" -ErrorAction Stop
}
foreach ($pdb in "authserver.pdb", "dbimport.pdb") { Copy-Item (Join-Path $bin $pdb) "$out\server\" }
if ($IncludeWorldserverPdb) { Copy-Item (Join-Path $bin "worldserver.pdb") "$out\server\" }
Copy-Item "$bin\configs\*.conf.dist" "$out\server\configs\"
Copy-Item "$bin\configs\modules\*.conf.dist" "$out\server\configs\modules\"

# --- Python SDK wheel (generated stubs included) ---
& "$PSScriptRoot\gen_proto.ps1" | Out-Null
$python = Join-Path $RepoRoot "python\.venv\Scripts\python.exe"
& $python -m pip wheel (Join-Path $RepoRoot "python") --no-deps --no-build-isolation -q -w "$out\python"
if ($LASTEXITCODE -ne 0) { throw "wheel build failed" }

# --- sources a controller author needs ---
foreach ($d in "proto", "examples", "docs") { Copy-Item -Recurse (Join-Path $RepoRoot $d) "$out\$d" }
Copy-Item (Join-Path $RepoRoot "PLAN.md") $out

# --- build info ---
$sdkCommit = (git -C $RepoRoot rev-parse HEAD).Trim()
$sdkDirty = if (git -C $RepoRoot status --porcelain) { " (with uncommitted changes)" } else { "" }
$acCommit = (git -C $AcSource rev-parse HEAD).Trim()
$wheel = (Get-ChildItem "$out\python\*.whl").Name
@"
$Name
Built:              $(Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz")
SDK commit:         $sdkCommit$sdkDirty
AzerothCore commit: $acCommit
Server config:      $Config (Visual Studio 2026, x64, vcpkg x64-windows-static-md)
Python wheel:       $wheel

Install: see docs\SDK_GUIDE.md (Python) and docs\windows-build.md (server).
Not included: map data (dbc/maps/vmaps/mmaps), MySQL, live *.conf files$(if (-not $IncludeWorldserverPdb) { ", worldserver.pdb (~770 MB)" }).
"@ | Set-Content -Encoding utf8 "$out\BUILD_INFO.txt"

$zip = "$out.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Compress-Archive -Path $out -DestinationPath $zip
Write-Host "Package: $out"
Write-Host "Zip:     $zip ($([math]::Round((Get-Item $zip).Length / 1MB, 1)) MB)"
