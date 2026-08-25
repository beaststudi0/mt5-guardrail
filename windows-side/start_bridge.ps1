<#
.SYNOPSIS
    Starts the MT5 bridge server.

.DESCRIPTION
    Run this in its own window and leave it alone. Pressing Ctrl+C in this
    window stops the server - use a second terminal to test it.

    Prefer PowerShell 7:   pwsh -ExecutionPolicy Bypass -File start_bridge.ps1
    Windows PowerShell 5.1 misparses UTF-8 files without a BOM on non-English
    locales, which is why this script is deliberately pure ASCII.
#>

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$envFile = Join-Path $root ".env"

if (-Not (Test-Path $envFile)) {
    Write-Host "Missing .env. Copy .env.example to .env and fill it in." -ForegroundColor Red
    exit 1
}

# The app also reads .env directly via pydantic-settings; exporting here as
# well means `python main.py` and this script behave identically.
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }

    $parts = $line -split "=", 2
    if ($parts.Length -eq 2) {
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        # pydantic-settings strips surrounding quotes when it reads .env
        # itself; strip them here too, or the exported variable (which wins
        # over the file) would keep the quotes and the two paths would
        # disagree about e.g. the password.
        if ($value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

$python = Join-Path $root "venv\Scripts\python.exe"
if (-Not (Test-Path $python)) {
    Write-Host "No venv found. Falling back to the python on PATH." -ForegroundColor Yellow
    $python = "python"
}

Write-Host "Starting MT5 bridge. Leave this window open." -ForegroundColor Green
& $python (Join-Path $root "main.py")
