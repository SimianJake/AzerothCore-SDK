# Shared paths for the SDK scripts. Dot-source this: . "$PSScriptRoot\env.ps1"

$RepoRoot = Split-Path -Parent $PSScriptRoot

# Tools installed by winget may not be on PATH in an already-open shell.
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
