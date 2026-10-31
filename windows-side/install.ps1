<#
.SYNOPSIS
    Installs the MT5 bridge: virtual environment, pip packages, .env.

.DESCRIPTION
    Normally started by install.cmd in the repository root, which has already
    copied the files, locked the folder permissions and checked that this
    session is elevated. You can also run it by hand from an elevated prompt:

        powershell -ExecutionPolicy Bypass -File install.ps1 -Target C:\mt5-guardrail -UserSid S-1-5-21-...

    What it does NOT do: it never asks for or stores MT5_LOGIN / MT5_PASSWORD /
    MT5_SERVER, never prints the generated API key, and never touches the
    guardrail settings (demo-only, confirm token, lot and order ceilings).

    Pure ASCII on purpose: Windows PowerShell 5.1 misparses UTF-8 without a BOM
    on non-English locales.
#>
[CmdletBinding()]
param(
    [string]$Target = '',
    [string]$UserSid = '',
    [switch]$Dev,
    [switch]$Test,
    [switch]$Wsl,
    [switch]$Yes,
    [switch]$AllowSdist,
    [switch]$Force,
    [switch]$SkipEnv,
    [string]$Features = '',
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$script:ReqPattern = '^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?(\s*(==|>=|<=|~=|!=|>|<)\s*[0-9][A-Za-z0-9.*+!_-]*(\s*,\s*(==|>=|<=|~=|!=|>|<)\s*[0-9][A-Za-z0-9.*+!_-]*)*)?$'
$script:MinPython = [version]'3.11'
$script:SecureTmp = $null
$script:SavedTemp = $null

function Write-Step([string]$Text) { Write-Host ('==> ' + $Text) -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host ('    ' + $Text) -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host ('WARN: ' + $Text) -ForegroundColor Yellow }
function Stop-Install([string]$Text, [int]$Code = 1) {
    Write-Host ('ERROR: ' + $Text) -ForegroundColor Red
    throw (New-Object System.Exception ('PG_ABORT:' + $Code))
}

# ------------------------------------------------------------- private temp dir
# Everything this script starts (python -m venv, pip, pytest) writes temporary files to %TEMP%. As
# Administrator that is still the user's own folder, which non-elevated programs can write to - a way
# to slip files into an elevated process - and it leaves Administrator-owned files behind that break
# the user's own pytest later. So point TEMP/TMP at a folder only Administrators/SYSTEM can use.
# $AlsoGrantSid is only for the unit tests, which run without Administrator rights and must still use the folder.
function Enter-SecureTemp([string]$AlsoGrantSid = '') {
    $dir = Join-Path $env:ProgramData ('mt5-guardrail-tmp-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $grants = @('*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F')
    if ($AlsoGrantSid -ne '') { $grants += ('*' + $AlsoGrantSid + ':(OI)(CI)F') }
    & icacls.exe $dir /inheritance:r /grant:r @grants | Out-Null
    if ($LASTEXITCODE -ne 0) { Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue; Stop-Install 'Could not create a private temp folder.' }
    $script:SavedTemp = @{ TEMP = $env:TEMP; TMP = $env:TMP }
    $script:SecureTmp = $dir
    $env:TEMP = $dir
    $env:TMP = $dir
    return $dir
}

function Exit-SecureTemp {
    if ($null -ne $script:SavedTemp) {
        $env:TEMP = $script:SavedTemp.TEMP
        $env:TMP = $script:SavedTemp.TMP
        $script:SavedTemp = $null
    }
    if ($null -ne $script:SecureTmp -and (Test-Path -LiteralPath $script:SecureTmp)) {
        Remove-Item -LiteralPath $script:SecureTmp -Recurse -Force -ErrorAction SilentlyContinue
    }
    $script:SecureTmp = $null
}

# ------------------------------------------------------------------ policy gate
function Get-PolicyHash([string]$Text) {
    $canon = ($Text -replace "`r", '').TrimEnd("`n")
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($canon))) -replace '-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

# install.cmd shows POLICY.md and writes .policy-accepted.txt. Without a record
# for exactly this POLICY.md, nothing is installed.
function Assert-PolicyAccepted([string]$Target) {
    $record = Join-Path $Target '.policy-accepted.txt'
    $policy = Join-Path $Target 'POLICY.md'
    if (-not (Test-Path -LiteralPath $record)) { Stop-Install 'No policy acceptance record. Run install.cmd: it shows POLICY.md and asks you to accept it.' 5 }
    if (-not (Test-Path -LiteralPath $policy)) { Stop-Install 'POLICY.md is missing from the install folder.' 5 }
    $line = Get-Content -LiteralPath $record | Where-Object { $_ -like 'policy_sha256=*' } | Select-Object -First 1
    if ($null -eq $line) { Stop-Install 'The acceptance record is incomplete.' 5 }
    if ($line.Substring('policy_sha256='.Length).Trim() -ne (Get-PolicyHash ([IO.File]::ReadAllText($policy)))) {
        Stop-Install 'The policy you accepted differs from POLICY.md in the install folder. Run install.cmd again from a current copy and accept again.' 5
    }
}

# ------------------------------------------------------- requirement parsing
# Only plain "name[extras] <op> version" lines may reach pip. Anything else
# (--index-url, --extra-index-url, URLs, -e, other -r files) is rejected so a
# tampered requirements file cannot redirect pip to another package index.
function ConvertTo-RequirementSpec([string]$Line) {
    $text = ($Line -replace '\s+#.*$', '').Trim()
    if ($text -eq '' -or $text.StartsWith('#')) { return $null }
    if ($text -eq '-r requirements.txt') { return $null }   # dev file includes the core file; core is installed anyway
    if ($text -notmatch $script:ReqPattern) { throw ('Unsupported line in a requirements file: ' + $text) }
    return $text
}

function Get-RequirementSpecs([string]$Path) {
    $specs = New-Object System.Collections.Generic.List[string]
    foreach ($line in (Get-Content -LiteralPath $Path)) {
        $s = ConvertTo-RequirementSpec $line
        if ($null -ne $s) { $specs.Add($s) }
    }
    return $specs.ToArray()   # callers wrap in @() so 0 or 1 spec stays an array
}

# The core package whose wheel decides which Python can be used.
function Get-ProbeSpec([string]$RequirementsFile) {
    $hit = @(@(Get-RequirementSpecs $RequirementsFile) | Where-Object { $_ -like 'MetaTrader5*' })
    if ($hit.Count -gt 0) { return [string]$hit[0] }
    return ''
}

# Groups come from the "# -- Title ----" headers of requirements-features.txt,
# so the repository file stays the single source of truth.
function Get-FeatureGroups([string]$Path) {
    $groups = New-Object System.Collections.Generic.List[object]
    $current = $null
    foreach ($line in (Get-Content -LiteralPath $Path)) {
        if ($line -match '^#\s*--\s*(?<t>.+?)\s*-{2,}\s*$') {
            $current = @{ Key = ($groups.Count + 1); Title = $Matches['t']; Specs = (New-Object System.Collections.Generic.List[string]) }
            $groups.Add($current)
            continue
        }
        $s = ConvertTo-RequirementSpec $line
        if ($null -eq $s) { continue }
        if ($null -eq $current) { throw 'requirements-features.txt has packages before its first group header.' }
        $current.Specs.Add($s)
    }
    # Callers wrap the call in @() so zero or one group still behaves as an array.
    return $groups.ToArray() | Where-Object { $_.Specs.Count -gt 0 }
}

function Resolve-Selection([string]$Text, $Groups) {
    $result = @{ Dev = $false; Picked = (New-Object System.Collections.Generic.List[int]) }
    $t = $Text.Trim().ToUpperInvariant()
    if ($t -eq '' -or $t -eq 'NONE') { return $result }
    if ($t -eq 'ALL') { foreach ($g in $Groups) { $result.Picked.Add([int]$g.Key) }; return $result }
    foreach ($part in ($t -split ',')) {
        $p = $part.Trim()
        if ($p -eq 'D') { $result.Dev = $true; continue }
        $n = 0
        if (-not ([int]::TryParse($p, [ref]$n)) -or $n -lt 1 -or $n -gt $Groups.Count) { return $null }
        if (-not $result.Picked.Contains($n)) { $result.Picked.Add($n) }
    }
    return $result
}

function Read-Selection($Groups, [bool]$DevAlready) {
    Write-Host ''
    Write-Host 'Core packages (MetaTrader5, fastapi, uvicorn, pydantic...) are always installed.'
    Write-Host 'Optional extras - type numbers separated by commas, "all", or just Enter for none:'
    if (-not $DevAlready) { Write-Host '  D   Dev tools      pytest, pytest-cov, httpx, ruff, mypy' }
    foreach ($g in $Groups) {
        Write-Host ('  {0}   {1}   {2}' -f $g.Key, $g.Title, ($g.Specs -join ', '))
    }
    while ($true) {
        $answer = Read-Host 'Install which extras'
        $sel = Resolve-Selection $answer $Groups
        if ($null -ne $sel) { return $sel }
        Write-Warn 'Not understood. Use e.g. 1,3,D or all or Enter.'
    }
}

# ------------------------------------------------------------------- python
# "py -3" can pick the newest interpreter, which may be a free-threaded build or a
# version the MetaTrader5 package has no wheel for yet. So: list every interpreter,
# prefer normal (GIL) builds, newest first, and check with a pip dry run that the
# core package really has a wheel for it before a venv is built.
function Test-PythonCandidate([string]$Exe) {
    try {
        $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        try { $out = @(& $Exe -c "import sys;print('%d.%d' % sys.version_info[:2]);print(sys.executable);print(getattr(sys,'_is_gil_enabled',lambda: True)())" 2>&1 | ForEach-Object { "$_" }) }
        finally { $ErrorActionPreference = $old }
        if ($LASTEXITCODE -ne 0 -or $out.Count -lt 3 -or $out[0] -notmatch '^\d+\.\d+$') { return $null }
        return @{ Path = $out[1]; Version = [version]$out[0]; Gil = ($out[2] -eq 'True') }
    } catch { return $null }
}

function Get-PythonPaths([string]$Explicit) {
    $paths = New-Object System.Collections.Generic.List[string]
    if ($Explicit -ne '') { $paths.Add($Explicit); return $paths.ToArray() }
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        try { $lines = @(& py.exe -0p 2>&1 | ForEach-Object { "$_" }) } finally { $ErrorActionPreference = $old }
        foreach ($l in $lines) { if ($l -match '([A-Za-z]:\\.*?\.exe)\s*$') { $paths.Add($Matches[1].Trim()) } }
    }
    $pc = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pc) { $paths.Add($pc.Source) }
    return $paths.ToArray() | Select-Object -Unique
}

function Test-PythonHasWheel([string]$Exe, [string]$Spec) {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $out = @(& $Exe -m pip install --dry-run --only-binary ':all:' --disable-pip-version-check --quiet $Spec 2>&1 | ForEach-Object { "$_" }); $code = $LASTEXITCODE }
    finally { $ErrorActionPreference = $old }
    if ($code -eq 0) { return $true }
    if (($out -join ' ') -match 'no such option: --dry-run') { return $true }   # old pip: cannot tell, do not reject
    return $false
}

function Find-Python([string]$Explicit, [string]$ProbeSpec, [bool]$SkipProbe) {
    $found = New-Object System.Collections.Generic.List[object]
    $tooOld = New-Object System.Collections.Generic.List[string]
    $seen = @{}
    foreach ($path in @(Get-PythonPaths $Explicit)) {
        $r = Test-PythonCandidate $path
        if ($null -eq $r -or $seen.ContainsKey($r.Path.ToLowerInvariant())) { continue }
        $seen[$r.Path.ToLowerInvariant()] = $true
        if ($r.Version -lt $script:MinPython) { $tooOld.Add([string]$r.Version); continue }
        $found.Add($r)
    }
    $ordered = @($found.ToArray() | Sort-Object @{ Expression = { $_.Gil }; Descending = $true }, @{ Expression = { $_.Version }; Descending = $true })
    $tried = New-Object System.Collections.Generic.List[string]
    foreach ($c in $ordered) {
        if ($Explicit -ne '' -or $SkipProbe -or $ProbeSpec -eq '') { return $c.Path }
        Write-Host ('    checking ' + $c.Path + ' (Python ' + $c.Version + $(if ($c.Gil) { '' } else { ', free-threaded' }) + ') for a ' + $ProbeSpec + ' wheel...')
        if (Test-PythonHasWheel $c.Path $ProbeSpec) { return $c.Path }
        $tried.Add([string]$c.Version + $(if ($c.Gil) { '' } else { 't' }))
    }
    $msg = 'No suitable Python 3.11+ was found.'
    if ($tooOld.Count -gt 0) { $msg += ' Too old: ' + ($tooOld -join ', ') + '.' }
    if ($tried.Count -gt 0) { $msg += ' No ' + $ProbeSpec + ' wheel for: ' + ($tried -join ', ') + ' (the package supports only some Python versions).' }
    Stop-Install ($msg + ' Install a regular (not free-threaded) Python 3.12 - 3.14 from https://www.python.org/downloads/ and run this again.')
}

# --------------------------------------------------------------------- pip
function Test-TransientPipFailure([string]$Text) {
    return [bool]($Text -match 'NameResolutionError|getaddrinfo failed|Temporary failure in name resolution|Connection (reset|aborted|broken)|ConnectTimeout|ReadTimeout|timed out|resume incomplete download|Max retries exceeded|Failed to establish a new connection')
}

# "from versions: none" is what pip prints both when no wheel fits and when the index answered
# incompletely (seen on flaky connections: a different package failed on every run). Retry it too.
function Test-RetryablePipFailure([string]$Text) {
    return ((Test-TransientPipFailure $Text) -or ($Text -match 'from versions: none'))
}

function Test-PypiReachable {
    try { [void][Net.Dns]::GetHostAddresses('pypi.org'); return $true } catch { return $false }
}

# --no-cache-dir: this runs as Administrator but the pip cache lives in the user's profile, where
# non-elevated programs can write. Installing a wheel from there would hand them admin code execution.
function Invoke-Pip([string]$VenvPython, [string[]]$PipArgs, [bool]$AllowSdist) {
    $all = @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-cache-dir', '--retries', '5', '--timeout', '30')
    if (-not $AllowSdist) { $all += @('--only-binary', ':all:') }
    $all += $PipArgs
    $code = 1
    $text = ''
    $sawNetworkTrouble = $false
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $tail = New-Object System.Collections.Generic.List[string]
        $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        try {
            & $VenvPython @all 2>&1 | ForEach-Object { $s = "$_"; Write-Host $s; $tail.Add($s); if ($tail.Count -gt 25) { $tail.RemoveAt(0) } }
            $code = $LASTEXITCODE
        } finally { $ErrorActionPreference = $old }
        $text = $tail -join "`n"
        if ($code -eq 0) { break }
        if (Test-TransientPipFailure $text) { $sawNetworkTrouble = $true }
        if ($attempt -eq 3 -or -not (Test-RetryablePipFailure $text)) { break }
        Write-Warn ('pip could not get a complete answer from the package index; retrying in 10 seconds (attempt ' + ($attempt + 1) + ' of 3)...')
        Start-Sleep -Seconds 10
    }
    if ($code -ne 0) {
        if ($text -match 'CERTIFICATE_VERIFY_FAILED|SSLError|SSL:') {
            Write-Warn 'pip could not verify the TLS certificate. An antivirus with "HTTPS scanning" is the usual cause.'
            Write-Warn 'See the README section on pip and antivirus (trusted-host / pip-system-certs). Nothing was changed for you.'
        }
        if ($sawNetworkTrouble -or (Test-TransientPipFailure $text)) {
            Write-Warn 'The network (DNS / connection to pypi.org) failed. Check your connection and run the installer again; finished steps are reused.'
        } elseif ($text -match 'No matching distribution|Could not find a version|no-binary|binary') {
            if (-not (Test-PypiReachable)) {
                Write-Warn 'This machine cannot resolve pypi.org right now (DNS). Fix the connection and run the installer again.'
            } elseif (-not $AllowSdist) {
                Write-Warn 'No usable prebuilt wheel was returned (after retries). Run the installer again; if it repeats for the same package, use --allow-sdist (source builds run as Administrator).'
            }
        }
    }
    return $code
}

function Install-Group([string]$VenvPython, [string]$Work, [string]$Name, $Specs, [bool]$AllowSdist) {
    $file = Join-Path $Work (($Name -replace '[^A-Za-z0-9]+', '_') + '.txt')
    [IO.File]::WriteAllText($file, (($Specs -join "`r`n") + "`r`n"), (New-Object Text.UTF8Encoding($false)))
    return Invoke-Pip $VenvPython @('-r', $file) $AllowSdist
}

# ---------------------------------------------------------------- .env + ACL
function Set-FileAcl([string]$Path, [string]$UserSid, [string]$UserRight) {
    & icacls.exe $Path /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' ('*' + $UserSid + ':' + $UserRight) | Out-Null
    if ($LASTEXITCODE -ne 0) { Stop-Install ('Could not set permissions on ' + $Path) }
}

function Set-DirAcl([string]$Path, [string]$UserSid, [string]$UserRight) {
    & icacls.exe $Path /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' ('*' + $UserSid + ':(OI)(CI)' + $UserRight) | Out-Null
    if ($LASTEXITCODE -ne 0) { Stop-Install ('Could not set permissions on ' + $Path) }
}

function Update-EnvText([string]$Text, [string]$Key, [string]$DataDir) {
    $fwd = $DataDir.Replace('\', '/')
    $changed = @{ Key = $false }
    $evaluator = [Text.RegularExpressions.MatchEvaluator] {
        param($m)
        $changed.Key = $true
        return 'MT5_BRIDGE_API_KEY=' + $Key + $m.Groups[1].Value
    }
    $Text = [regex]::Replace($Text, '(?m)^MT5_BRIDGE_API_KEY=[ \t]*(\r?)$', $evaluator)
    $Text = [regex]::Replace($Text, '(?m)^#\s*MT5_JOURNAL_DB=trade_journal\.sqlite3', ('MT5_JOURNAL_DB=' + $fwd + '/trade_journal.sqlite3'))
    $Text = [regex]::Replace($Text, '(?m)^#\s*MT5_BRIDGE_LOG=mt5_bridge\.log', ('MT5_BRIDGE_LOG=' + $fwd + '/mt5_bridge.log'))
    return @{ Text = $Text; KeyPlaced = $changed.Key }
}

function Initialize-EnvFile([string]$Workspace, [string]$VenvPython, [string]$UserSid) {
    $envPath = Join-Path $Workspace '.env'
    $example = Join-Path $Workspace '.env.example'
    $dataDir = Join-Path $Workspace 'data'
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    Set-DirAcl $dataDir $UserSid 'M'

    if (Test-Path -LiteralPath $envPath) {
        Set-FileAcl $envPath $UserSid 'M'
        Write-Ok '.env already exists: contents left untouched (permissions tightened).'
        return
    }
    if (-not (Test-Path -LiteralPath $example)) { Stop-Install '.env.example is missing; cannot create .env.' }

    # Order matters: create empty, restrict, only then write the secret.
    New-Item -ItemType File -Path $envPath -Force | Out-Null
    Set-FileAcl $envPath $UserSid 'M'

    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    # Collect all output first: stopping the pipe early can make python exit non-zero.
    try { $keyOut = @(& $VenvPython -c 'import secrets;print(secrets.token_urlsafe(32))' 2>$null) } finally { $ErrorActionPreference = $old }
    $keyExit = $LASTEXITCODE
    $key = ''
    if ($keyOut.Count -gt 0) { $key = [string]$keyOut[0] }
    if ($keyExit -ne 0 -or [string]::IsNullOrWhiteSpace($key) -or $key.Trim().Length -lt 16) { Stop-Install 'Could not generate an API key.' }

    $raw = [IO.File]::ReadAllText($example)
    $patched = Update-EnvText $raw $key.Trim() $dataDir
    if (-not $patched.KeyPlaced) { Write-Warn 'MT5_BRIDGE_API_KEY= line not found in .env.example; set the key in .env yourself.' }
    [IO.File]::WriteAllText($envPath, $patched.Text, (New-Object Text.UTF8Encoding($false)))
    Write-Ok '.env created from .env.example (API key generated, not displayed; journal/log paths point to data\).'
    $key = $null
}

# ---------------------------------------------------------------------- WSL
function Invoke-WslInstall([string]$Target, [bool]$DevFlag, [bool]$TestFlag, [bool]$YesFlag, [string]$FeaturesText, [bool]$AllowSdistFlag) {
    $wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
    if (-not (Test-Path -LiteralPath $wsl)) { Write-Warn 'wsl.exe not found; skipping the WSL step.'; return 0 }
    Write-Step 'Running the WSL installer (linux-side/install.sh)'
    $path = (& $wsl wslpath -a (Join-Path $Target 'linux-side') | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($path)) { Write-Warn 'WSL is not set up or has no default distribution; skipping.'; return 0 }
    $a = @('bash', ($path.Trim() + '/install.sh'), '--accept-policy')   # policy was accepted by install.cmd
    if ($DevFlag) { $a += '--dev' }
    if ($TestFlag) { $a += '--test' }
    if ($YesFlag) { $a += '--yes' }
    if ($AllowSdistFlag) { $a += '--allow-sdist' }
    if ($FeaturesText -ne '') { $a += @('--features', $FeaturesText) }
    & $wsl @a | Out-Host
    return $LASTEXITCODE
}

# --------------------------------------------------------------------- main
function Invoke-Main {
    try {
        if ([Environment]::OSVersion.Platform -ne 'Win32NT') { Stop-Install 'The bridge needs native Windows (the MetaTrader5 package is Windows-only). Use linux-side/install.sh for WSL.' }
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            Stop-Install 'Run this from an Administrator prompt (or use install.cmd, which asks for it).'
        }
        if ($Target -eq '' -or $UserSid -notmatch '^S-1-(5-21|12-1)(-[0-9]+)+$') { Stop-Install 'Usage: install.ps1 -Target <folder> -UserSid <SID> [options]. Normally start install.cmd instead.' }
        $workspace = Join-Path $Target 'windows-side'
        Assert-PolicyAccepted $Target
        [void](Enter-SecureTemp)
        foreach ($need in @('main.py', 'requirements.txt', '.env.example')) {
            if (-not (Test-Path -LiteralPath (Join-Path $workspace $need))) { Stop-Install ('Missing ' + $need + ' in ' + $workspace) }
        }
        $devWanted = [bool]$Dev -or [bool]$Test
        $featureText = $Features

        Write-Step 'Looking for Python 3.11+'
        $probeSpec = Get-ProbeSpec (Join-Path $workspace 'requirements.txt')
        $python = Find-Python $PythonPath $probeSpec ([bool]$AllowSdist)
        Write-Ok ('Using ' + $python)

        $venv = Join-Path $workspace 'venv'
        $venvPython = Join-Path $venv 'Scripts\python.exe'
        if ($Force -and (Test-Path -LiteralPath $venv)) {
            Write-Step 'Removing the old virtual environment (--force)'
            Remove-Item -LiteralPath $venv -Recurse -Force
        }
        if (-not (Test-Path -LiteralPath $venvPython)) {
            Write-Step 'Creating the virtual environment'
            & $python -m venv $venv
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) { Stop-Install 'Could not create the virtual environment.' }
        } else { Write-Ok 'Reusing the existing virtual environment.' }

        $work = Join-Path $env:ProgramData ('mt5-guardrail-pip-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $work -Force | Out-Null
        & icacls.exe $work /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
        $failed = New-Object System.Collections.Generic.List[string]
        $skipTests = $false
        $done = New-Object System.Collections.Generic.List[string]
        try {
            Write-Step 'Installing core packages (requirements.txt)'
            $coreSpecs = @(Get-RequirementSpecs (Join-Path $workspace 'requirements.txt'))
            if ((Install-Group $venvPython $work 'core' $coreSpecs ([bool]$AllowSdist)) -ne 0) { Stop-Install 'Core packages failed to install; the bridge cannot run without them. If the virtual environment was built with a different Python, re-run with --force.' }
            $done.Add('core')

            # Make the install usable (imports verified, .env ready) before any optional extras,
            # so a failing extra can never leave a half-configured folder behind.
            Write-Step 'Checking that the bridge imports'
            Push-Location $workspace
            try {
                $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
                try { $out = @(& $venvPython -c "import MetaTrader5, fastapi, uvicorn, pydantic_settings, mt5_bridge.app; print('imports ok')" 2>&1 | ForEach-Object { "$_" }) } finally { $ErrorActionPreference = $old }
                if ($LASTEXITCODE -ne 0) { Stop-Install ('Import check failed: ' + (($out | Select-Object -Last 3) -join ' ')) }
                Write-Ok 'imports ok'
            } finally { Pop-Location }

            if (-not $SkipEnv) {
                Write-Step 'Preparing .env'
                Initialize-EnvFile $workspace $venvPython $UserSid
            }


            $featuresFile = Join-Path $workspace 'requirements-features.txt'
            $groups = @()
            if (Test-Path -LiteralPath $featuresFile) {
                try { $groups = @(Get-FeatureGroups $featuresFile) } catch { Write-Warn ('Could not read optional groups: ' + $_.Exception.Message); $groups = @() }
            }
            $interactive = (-not $Yes) -and ($featureText -eq '') -and [Environment]::UserInteractive -and (-not [Console]::IsInputRedirected)
            if ($featureText -ne '') { $sel = Resolve-Selection $featureText $groups; if ($null -eq $sel) { Stop-Install 'Invalid --features for the groups in requirements-features.txt.' } }
            elseif ($interactive -and ($groups.Count -gt 0 -or -not $devWanted)) { $sel = Read-Selection $groups $devWanted }
            else { $sel = Resolve-Selection 'none' $groups }
            if ($sel.Dev) { $devWanted = $true }

            if ($devWanted) {
                Write-Step 'Installing dev tools (requirements-dev.txt)'
                $devFile = Join-Path $workspace 'requirements-dev.txt'
                $devSpecs = @(Get-RequirementSpecs $devFile)
                if ((Install-Group $venvPython $work 'dev' $devSpecs ([bool]$AllowSdist)) -ne 0) { $failed.Add('dev'); if ($Test) { $skipTests = $true } } else { $done.Add('dev') }
            }
            foreach ($k in $sel.Picked) {
                $g = $groups | Where-Object { $_.Key -eq $k } | Select-Object -First 1
                Write-Step ('Installing optional group ' + $g.Key + ': ' + $g.Title)
                if ((Install-Group $venvPython $work ('group' + $g.Key) $g.Specs ([bool]$AllowSdist)) -ne 0) { $failed.Add('group ' + $g.Key + ' (' + $g.Title + ')') } else { $done.Add('group ' + $g.Key) }
            }
        } finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }

        $record = Join-Path $workspace '.install-selection.txt'
        [IO.File]::WriteAllText($record, ('installed: ' + ($done -join ', ') + "`r`nfailed: " + ($failed -join ', ') + "`r`n"), (New-Object Text.UTF8Encoding($false)))

        if ($Test -and $skipTests) {
            $failed.Add('tests (skipped: dev tools did not install)')
        } elseif ($Test) {
            Write-Step 'Running the test suite'
            Push-Location $workspace
            try {
                $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
                try { $testLines = @(& $venvPython -m pytest 2>&1 | ForEach-Object { $l = "$_"; Write-Host $l; $l }); $testCode = $LASTEXITCODE } finally { $ErrorActionPreference = $old }
            } finally { Pop-Location }
            # Keep the result next to the install: the elevated window closes, and this file is readable without Administrator rights.
            [IO.File]::WriteAllText((Join-Path $workspace '.test-result.txt'), ('exit=' + $testCode + "`r`n" + (($testLines | Select-Object -Last 30) -join "`r`n") + "`r`n"), (New-Object Text.UTF8Encoding($false)))
            if ($testCode -ne 0) { $failed.Add('tests (exit ' + $testCode + ')') }
        }

        if ($Wsl) {
            $wslCode = Invoke-WslInstall $Target ([bool]$Dev) ([bool]$Test) ([bool]$Yes) $Features ([bool]$AllowSdist)
            if ($wslCode -ne 0) { $failed.Add('WSL installer (exit ' + $wslCode + ')') }
        }

        Write-Host ''
        Write-Host '------------------------------------------------------------' -ForegroundColor Cyan
        Write-Host ('Installed: ' + ($done -join ', '))
        if ($failed.Count -gt 0) { Write-Host ('FAILED:    ' + ($failed -join '; ')) -ForegroundColor Red }
        Write-Host ''
        Write-Host 'Next steps:'
        Write-Host ('  1. Open ' + (Join-Path $workspace '.env') + ' and fill MT5_LOGIN, MT5_PASSWORD, MT5_SERVER.')
        Write-Host '     (MT5_BRIDGE_API_KEY is already generated. Keep the account a DEMO account first.)'
        Write-Host ('  2. Start the bridge from a normal (non-admin) window: ' + (Join-Path $workspace 'start_bridge.cmd'))
        Write-Host '  3. WSL side: set the same MT5_BRIDGE_API_KEY (copy it from .env), then run linux-side/mt5_cli.py health'
        Write-Host '  The bridge listens on 127.0.0.1 by default. Read SECURITY.md before exposing it to your network.'
        if ($failed.Count -gt 0) { return 2 }
        return 0
    } catch {
        $m = $_.Exception.Message
        if ($m -match '^PG_ABORT:(\d+)$') { return [int]$Matches[1] }
        Write-Host ('ERROR: ' + $m) -ForegroundColor Red
        return 1
    } finally {
        Exit-SecureTemp
    }
}

if (-not $env:PG_NOMAIN) {
    $code = Invoke-Main
    if ($code -is [array]) { $code = $code[-1] }
    exit ([int]$code)
}
