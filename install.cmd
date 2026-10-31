<# :
@echo off
rem ---------------------------------------------------------------------
rem  mt5-guardrail one-command installer (Windows).
rem
rem  Open an Administrator Command Prompt once and run:
rem      install.cmd [folder] [options]       (see install.cmd --help)
rem  Not elevated? It asks for UAC once and continues in the new window.
rem
rem  This file is a batch/PowerShell hybrid: the batch lines below only hand
rem  the whole file to PowerShell. Keep it pure ASCII with CRLF line ends.
rem  The text between the two comment markers is cmd-only; do not use the
rem  PowerShell block-comment closing marker anywhere inside it.
rem ---------------------------------------------------------------------
setlocal DisableDelayedExpansion
set "PG_SELF=%~f0"
set "PG_ARGS=%*"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create([IO.File]::ReadAllText($env:PG_SELF)))"
set "PG_RC=%ERRORLEVEL%"
endlocal & exit /b %PG_RC%
#>

# ======================= PowerShell payload =======================
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$script:RepoName   = 'beaststudi0/mt5-guardrail'
# 40-hex commit the installer downloads when nothing else is given. Empty in the
# commit that adds this file; a follow-up commit (never squashed) sets it.
$script:PinCommit  = ''
$script:DefaultDir = 'C:\mt5-guardrail'
$script:SafeToken  = '^[A-Za-z0-9 ._:\\=,-]*$'
$script:PolicyVersion = '2026-10-31'
# Verbatim copy of POLICY.md (a test keeps the two identical). Shown before anything is installed.
$script:PolicyText = @'
MT5 GUARDRAIL - INSTALLATION POLICY AND RISK ACKNOWLEDGEMENT
Version 2026-10-31

Read this before you install. The installer will not continue until you accept it.

1. RELATIONSHIP TO THE LICENSE
This policy supplements the Apache License, Version 2.0 (see the LICENSE file). It does not
remove or restrict any right the Apache License gives you. If the two conflict, the Apache
License controls. It applies to running the installer and to installing and using this software.

2. NO WARRANTY - NO LIABILITY
The software is provided "AS IS", without warranties or conditions of any kind (Apache License
sections 7 and 8 apply in full). To the maximum extent permitted by law, the authors and
contributors are not liable for any loss or damage of any kind arising from the software or from
this installer, including trading losses, lost profits, missed, wrong, duplicate or rejected
orders, account restrictions or closure by a broker, data loss, downtime, or security incidents.
Nothing in this policy excludes or limits liability for fraud, intentional misconduct, gross
negligence, or death or personal injury, or any other liability that cannot be excluded or limited
by law, and nothing limits consumer rights that the law of your country does not allow to be waived.

3. TRADING RISK - NOT ADVICE
This software is infrastructure. It is not a trading strategy, not financial, investment, legal
or tax advice, and no promise of profit. Trading leveraged products (CFDs, forex, crypto and
others) can lose more than you deposit. The guardrails (preview and confirm token, order and lot
ceilings, demo-only default, mandatory reasoning record, cooldowns) reduce specific known
failure modes. They do not remove trading risk and they do not check whether a strategy, script
or AI agent that calls the bridge is any good. Use a DEMO account first. Do not turn off the
demo-only protection until you have tested your own setup and accept all consequences.

4. YOUR RESPONSIBILITY
You are solely responsible for: every order sent through this software; the accounts, passwords
and API keys you connect; any script, bot or AI agent you let call the bridge; your broker's
terms (many brokers restrict or prohibit automated, EA or AI-assisted trading, or contests and
promotions that involve it); and the laws, licensing, reporting and tax rules that apply to you.
You confirm you are legally allowed to use this software where you live and are of legal age.

5. WHAT THE INSTALLER DOES
It runs as Administrator. It downloads this project from github.com and Python packages from
PyPI through pip, and it can run package build code if you allow source builds. It creates
files in the install folder (default C:\mt5-guardrail), creates a virtual environment, creates
a .env file containing a newly generated API key, and sets permissions so that only
Administrators and SYSTEM can change the installed files. It does not change firewall rules,
PATH, the registry or the PowerShell execution policy, does not install services, and does not
collect, send or store your MT5 login or password. The installer sends no telemetry. Third-party
packages come under their own licenses and carry their own supply-chain risk, which you accept.

6. SECURITY
Anyone who holds your API key and can reach the bridge can place trades on the connected
account. Keep .env private, never commit or share it, do not expose the bridge to untrusted
networks, and read SECURITY.md before changing the listening address. Keeping your computer,
broker account and credentials secure is your responsibility.

7. THIRD PARTIES
MetaTrader is a trademark of MetaQuotes Ltd. This project is not affiliated with or endorsed by
MetaQuotes, any broker, or any other third party. Their software and terms apply to you.

8. SEVERABILITY
If a part of this policy is not enforceable where you live, the rest still applies.

9. RECORD OF ACCEPTANCE
When you accept, the installer writes a local file (.policy-accepted.txt in the install folder)
with the policy version, a hash of this text, the UTC time, your Windows account name and how
you accepted. Nothing is transmitted anywhere.

10. VERSIONS AND TRANSLATIONS
This English text is the binding version; translations (such as POLICY.th.md) are for convenience
only. A new policy version applies to installations and updates for which you accept it; a version
you already accepted keeps governing what you installed under it.

11. IF YOU DO NOT AGREE
Do not run the installer. At the prompt answer N, or just press Enter, or press Ctrl+C: nothing is
installed.
Declining this policy does not affect the rights the Apache License gives you.
'@

function Write-Step([string]$Text) { Write-Host ('==> ' + $Text) -ForegroundColor Cyan }
function Write-Warn([string]$Text) { Write-Host ('WARN: ' + $Text) -ForegroundColor Yellow }
function Stop-Install([string]$Text, [int]$Code = 1) {
    Write-Host ('ERROR: ' + $Text) -ForegroundColor Red
    throw (New-Object System.Exception ('PG_ABORT:' + $Code))
}

function Show-Usage {
    @'
mt5-guardrail installer

  install.cmd [folder] [options]

  folder            install location (default C:\mt5-guardrail, local drive path)
  --dev             also install requirements-dev.txt (pytest, ruff, mypy)
  --features LIST   optional package groups from requirements-features.txt:
                    comma list of group numbers, "all" or "none"
                    (no option = ask once; --yes = none)
  --test            run the test suite after installing (implies --dev)
  --wsl             also run linux-side/install.sh inside WSL
  --accept-policy   I have read POLICY.md and accept it (for unattended installs;
                    without it the policy is shown: answer Y to accept, N to decline)
  --yes             never ask questions (the policy is still required)
  --allow-sdist     let pip build source packages (default: wheels only)
  --force           recreate the virtual environment
  --ref NAME        download this tag/branch/commit instead of the pinned release
  --source PATH     use a local clone or .zip instead of downloading
  --help            this text

Everything runs as Administrator. The install folder is writable by
Administrators/SYSTEM only; your user may read/run it and may edit only
windows-side\.env and windows-side\data\.
'@ | Write-Host
}

# ---------------------------------------------------------------- arguments
function Split-ArgString([string]$Text) {
    $tokens = New-Object System.Collections.Generic.List[string]
    $sb = New-Object System.Text.StringBuilder
    $inQuote = $false
    $has = $false
    foreach ($ch in $Text.ToCharArray()) {
        if ($ch -eq '"') { $inQuote = -not $inQuote; $has = $true; continue }
        if ((-not $inQuote) -and [char]::IsWhiteSpace($ch)) {
            if ($has) { $tokens.Add($sb.ToString()); [void]$sb.Clear(); $has = $false }
            continue
        }
        [void]$sb.Append($ch)
        $has = $true
    }
    if ($inQuote) { throw 'Unbalanced double quote in arguments.' }
    if ($has) { $tokens.Add($sb.ToString()) }
    return , $tokens.ToArray()
}

function ConvertTo-Options([string[]]$Tokens) {
    $o = @{
        Dir = $null; Ref = $null; Source = $null; UserSid = $null; Features = $null
        Dev = $false; Test = $false; Wsl = $false; Yes = $false
        AllowSdist = $false; Force = $false; Elevated = $false; Help = $false
        AcceptPolicy = $false; AcceptedVia = $null
    }
    $valued = @{ '--ref' = 'Ref'; '--source' = 'Source'; '--user-sid' = 'UserSid'; '--features' = 'Features'; '--accepted-via' = 'AcceptedVia' }
    $flags = @{
        '--dev' = 'Dev'; '--test' = 'Test'; '--wsl' = 'Wsl'; '--yes' = 'Yes'
        '--allow-sdist' = 'AllowSdist'; '--force' = 'Force'; '--elevated' = 'Elevated'; '--help' = 'Help'; '--accept-policy' = 'AcceptPolicy'
    }
    $i = 0
    while ($i -lt $Tokens.Count) {
        $t = $Tokens[$i]
        if ($t -notmatch $script:SafeToken) {
            throw ('Argument contains characters that are not allowed: ' + $t)
        }
        $key = $t.ToLowerInvariant()
        if ($valued.ContainsKey($key)) {
            if (($i + 1) -ge $Tokens.Count -or $Tokens[$i + 1].StartsWith('--')) { throw ($t + ' needs a value.') }
            $o[$valued[$key]] = $Tokens[$i + 1]
            $i += 2
            continue
        }
        if ($flags.ContainsKey($key)) { $o[$flags[$key]] = $true; $i += 1; continue }
        if ($t.StartsWith('--')) { throw ('Unknown option: ' + $t) }
        if ($null -ne $o.Dir) { throw ('Only one folder may be given (got a second: ' + $t + ').') }
        $o.Dir = $t
        $i += 1
    }
    if ($o.Test) { $o.Dev = $true }
    return $o
}

function Test-Options($o) {
    if ($null -eq $o.Dir) { $o.Dir = $script:DefaultDir }
    if ($o.Dir -notmatch '^[A-Za-z]:\\[A-Za-z0-9 ._\\-]+$') {
        throw ('Folder must be an absolute local path such as C:\mt5-guardrail (letters, digits, space . _ - only): ' + $o.Dir)
    }
    if ($o.Dir -match '(^|\\)\.\.?(\\|$)' -or $o.Dir -match '[ .]\\' -or $o.Dir -match '[ .]$') {
        throw 'Folder must not contain "." / ".." segments or names ending in space or dot.'
    }
    $full = [IO.Path]::GetFullPath($o.Dir).TrimEnd('\')
    if ($full.Length -le 3) { throw 'Refusing to install into a drive root.' }
    $o.Dir = $full
    if ($null -ne $o.Ref -and ($o.Ref -notmatch '^[A-Za-z0-9._-]{1,100}$' -or $o.Ref.Contains('..'))) {
        throw 'Invalid --ref (letters, digits, . _ - only).'
    }
    if ($null -ne $o.Features -and $o.Features -notmatch '^(?i:none|all|[1-9D](,[1-9D])*)$') {
        throw 'Invalid --features (use none, all, or numbers such as 1,3,D).'
    }
    if ($null -ne $o.AcceptedVia -and $o.AcceptedVia -notmatch '^(interactive|flag)$') { throw 'Invalid --accepted-via.' }
    if ($null -ne $o.UserSid -and $o.UserSid -notmatch '^S-1-(5-21|12-1)(-[0-9]+)+$') {
        throw 'Invalid --user-sid.'
    }
    if ($null -ne $o.Source) {
        if (-not (Test-Path -LiteralPath $o.Source)) { throw ('--source not found: ' + $o.Source) }
        $o.Source = (Resolve-Path -LiteralPath $o.Source).ProviderPath
    }
    return $o
}

function ConvertTo-ArgLine($o) {
    $parts = New-Object System.Collections.Generic.List[string]
    $parts.Add('"' + $o.Dir + '"')
    foreach ($pair in @(@('--ref', 'Ref'), @('--source', 'Source'), @('--user-sid', 'UserSid'), @('--features', 'Features'), @('--accepted-via', 'AcceptedVia'))) {
        $v = $o[$pair[1]]
        if ($null -ne $v) { $parts.Add($pair[0]); $parts.Add('"' + $v + '"') }
    }
    foreach ($pair in @(@('--dev', 'Dev'), @('--test', 'Test'), @('--wsl', 'Wsl'), @('--yes', 'Yes'),
                        @('--allow-sdist', 'AllowSdist'), @('--force', 'Force'), @('--accept-policy', 'AcceptPolicy'))) {
        if ($o[$pair[1]]) { $parts.Add($pair[0]) }
    }
    $parts.Add('--elevated')
    return ($parts -join ' ')
}

# ------------------------------------------------------------------- policy
function Get-PolicyHash([string]$Text) {
    $canon = ($Text -replace "`r", '').TrimEnd("`n")
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($canon))) -replace '-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

# Y / yes / "I ACCEPT" accept. N, Enter (the default) and similar decline. Anything else asks again.
function Resolve-PolicyAnswer([string]$Answer) {
    if ($null -eq $Answer) { return 'decline' }
    $a = $Answer.Trim()
    if ($a -match '^(?i:y|yes|i accept)$') { return 'accept' }
    if ($a -eq '' -or $a -match '^(?i:n|no|i not accept|i do not accept|decline|cancel|quit|exit)$') { return 'decline' }
    return 'again'
}

# Returns 'interactive' when the user answered Y, otherwise $null (declined, no valid answer, or no console).
function Request-PolicyAcceptance {
    if ((-not [Environment]::UserInteractive) -or [Console]::IsInputRedirected) { return $null }
    Write-Host ''
    try { $script:PolicyText | Out-Host -Paging } catch { Write-Host $script:PolicyText }
    Write-Host ''
    Write-Host ('Policy version ' + $script:PolicyVersion + ', sha256 ' + (Get-PolicyHash $script:PolicyText).Substring(0, 16) + '...  (Thai reading copy: POLICY.th.md)')
    for ($try = 1; $try -le 3; $try++) {
        $answer = Read-Host 'Do you accept this policy?  Y = accept and install,  N = decline (Enter also declines)  [y/N]'
        $decision = Resolve-PolicyAnswer $answer
        if ($decision -eq 'accept') { return 'interactive' }
        if ($decision -eq 'decline') { Write-Host 'You declined. Nothing was installed.'; return $null }
        Write-Warn 'Please answer Y or N.'
    }
    Write-Host 'No valid answer. Nothing was installed.'
    return $null
}

function Write-PolicyRecord([string]$Dir, [string]$Method, [string]$Source) {
    $lines = @(
        ('policy_version=' + $script:PolicyVersion),
        ('policy_sha256=' + (Get-PolicyHash $script:PolicyText)),
        ('accepted_utc=' + [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')),
        ('accepted_by=' + [Security.Principal.WindowsIdentity]::GetCurrent().Name),
        ('method=' + $Method),
        ('installer_source=' + $Source)
    )
    [IO.File]::WriteAllText((Join-Path $Dir '.policy-accepted.txt'), (($lines -join "`r`n") + "`r`n"), (New-Object Text.UTF8Encoding($false)))
}

# ------------------------------------------------------------------ privilege
function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-CurrentSid { return [Security.Principal.WindowsIdentity]::GetCurrent().User.Value }

function Start-Elevated($o, [string]$Self) {
    if ($Self -match '[&^%!"]') { Stop-Install 'The path of install.cmd contains & ^ % ! or a quote; move it to a simpler folder.' }
    $o.UserSid = Get-CurrentSid
    $line = ConvertTo-ArgLine $o
    Write-Step 'Administrator rights are required. Windows will ask once (UAC)...'
    try {
        $p = Start-Process -FilePath $env:ComSpec -ArgumentList ('/c ""' + $Self + '" ' + $line + '"') -Verb RunAs -Wait -PassThru
    } catch {
        Stop-Install 'Elevation was cancelled or failed. Nothing was installed. Re-run from an Administrator Command Prompt.' 3
    }
    return $p.ExitCode
}

# ------------------------------------------------------------ work directory
function New-LockedDirectory([string]$Path) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    & icacls.exe $Path /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { Stop-Install ('Could not restrict permissions on ' + $Path) }
}

# --------------------------------------------------------------- source tree
function Get-NativeTool([string]$Name) {
    $p = Join-Path $env:SystemRoot ('System32\' + $Name)
    if (Test-Path -LiteralPath $p) { return $p }
    return $null
}

function Save-Archive([string]$Ref, [string]$ZipPath) {
    $url = 'https://github.com/' + $script:RepoName + '/archive/' + $Ref + '.zip'
    Write-Step ('Downloading ' + $url)
    $curl = Get-NativeTool 'curl.exe'
    if ($curl) {
        & $curl -fsSL --proto '=https' --tlsv1.2 --max-time 600 --max-filesize 209715200 -o $ZipPath $url
        if ($LASTEXITCODE -ne 0) { Stop-Install ('Download failed (curl exit ' + $LASTEXITCODE + '). Check the network and the ref name.') }
    } else {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        try { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $ZipPath } catch { Stop-Install ('Download failed: ' + $_.Exception.Message) }
    }
    if (-not (Test-Path -LiteralPath $ZipPath) -or (Get-Item -LiteralPath $ZipPath).Length -lt 1024) {
        Stop-Install 'Downloaded file is missing or too small.'
    }
}

function Expand-SourceArchive([string]$ZipPath, [string]$Dest) {
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    $tar = Get-NativeTool 'tar.exe'
    if ($tar) {
        & $tar -xf $ZipPath -C $Dest
        if ($LASTEXITCODE -ne 0) { Stop-Install 'Could not extract the archive.' }
    } else {
        Expand-Archive -LiteralPath $ZipPath -DestinationPath $Dest -Force
    }
    $top = @(Get-ChildItem -LiteralPath $Dest -Force)
    if ($top.Count -ne 1 -or -not $top[0].PSIsContainer) { Stop-Install 'Unexpected archive layout (need exactly one top folder).' }
    return Assert-SourceTree $top[0].FullName
}

function Assert-SourceTree([string]$Root) {
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    foreach ($item in (Get-ChildItem -LiteralPath $Root -Recurse -Force)) {
        if (-not $item.FullName.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) { Stop-Install 'Archive entry escapes the extraction folder.' }
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Stop-Install 'Archive contains a link/reparse point; refusing.' }
    }
    foreach ($need in @('windows-side\main.py', 'windows-side\install.ps1', 'windows-side\requirements.txt')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $need))) { Stop-Install ('Source tree is missing ' + $need) }
    }
    return $Root
}

function Get-SourceRoot($o, [string]$Work, [string]$SelfDir) {
    if ($null -ne $o.Source) {
        if ((Get-Item -LiteralPath $o.Source).PSIsContainer) { return Assert-SourceTree $o.Source }
        return Expand-SourceArchive $o.Source (Join-Path $Work 'src')
    }
    if ($null -eq $o.Ref -and (Test-Path -LiteralPath (Join-Path $SelfDir 'windows-side\install.ps1'))) {
        Write-Step ('Using the local copy in ' + $SelfDir)
        return Assert-SourceTree $SelfDir
    }
    $ref = $o.Ref
    if ($null -eq $ref) {
        if ($script:PinCommit -notmatch '^[0-9a-f]{40}$') {
            Stop-Install 'This copy has no pinned release. Run it from a clone of the repository, or pass --ref <tag> (unpinned) or --source <path>.'
        }
        $ref = $script:PinCommit
    } else {
        Write-Warn ('--ref ' + $ref + ' is NOT pinned to a commit; you are trusting whatever that name points to right now.')
    }
    $zip = Join-Path $Work 'source.zip'
    Save-Archive $ref $zip
    return Expand-SourceArchive $zip (Join-Path $Work 'src')
}

# --------------------------------------------------------- copy + permissions
function Copy-Tree([string]$Src, [string]$Dst) {
    Write-Step ('Copying files to ' + $Dst)
    New-Item -ItemType Directory -Path $Dst -Force | Out-Null
    & robocopy.exe $Src $Dst /E /XF .env /R:2 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
    # robocopy: 0-7 = success (with/without changes), 8+ = failure.
    if ($LASTEXITCODE -ge 8) { Stop-Install ('File copy failed (robocopy exit ' + $LASTEXITCODE + ').') }
    $global:LASTEXITCODE = 0
}

function Set-InstallAcl([string]$Target, [string]$UserSid) {
    Write-Step 'Locking down permissions (Administrators/SYSTEM write; you read/run)'
    foreach ($step in @(
            @($Target, '/setowner', '*S-1-5-32-544', '/T', '/C', '/Q'),
            @($Target, '/reset', '/T', '/C', '/Q'),
            @($Target, '/inheritance:r'),
            @($Target, '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F', ('*' + $UserSid + ':(OI)(CI)RX')))) {
        & icacls.exe @step | Out-Null
        if ($LASTEXITCODE -ne 0) { Stop-Install ('icacls failed: ' + ($step -join ' ')) }
    }
    Assert-AclSafe $Target
}

function Assert-AclSafe([string]$Path) {
    $bad = @('S-1-1-0', 'S-1-5-11', 'S-1-5-32-545')   # Everyone, Authenticated Users, Users
    foreach ($rule in (Get-Acl -LiteralPath $Path).Access) {
        $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($bad -contains $sid) { Stop-Install ('Permission check failed: ' + $Path + ' is accessible to ' + $rule.IdentityReference) }
    }
}

# --------------------------------------------------------------------- main
function Invoke-Main {
    $self = $env:PG_SELF
    $selfDir = Split-Path -Parent $self
    $work = $null
    $exit = 0
    try {
        $tokens = Split-ArgString ([string]$env:PG_ARGS)
        $o = ConvertTo-Options $tokens
        if ($o.Help) { Show-Usage; return 0 }
        $o = Test-Options $o

        if (-not $o.AcceptPolicy) {
            $via = Request-PolicyAcceptance
            if ($null -eq $via) { Stop-Install 'Policy not accepted. Nothing was installed. For unattended installs read POLICY.md and pass --accept-policy.' 5 }
            $o.AcceptPolicy = $true
            $o.AcceptedVia = $via
        } elseif ($null -eq $o.AcceptedVia) { $o.AcceptedVia = 'flag' }

        if (-not (Test-IsAdmin)) {
            return (Start-Elevated $o $self)
        }
        $cur = Get-CurrentSid
        if ($null -eq $o.UserSid) { $o.UserSid = $cur }
        elseif ($o.UserSid -ne $cur) {
            Stop-Install 'UAC elevated to a different account than the one that started the installer. Run this from an Administrator Command Prompt of the account that will run the bridge.' 4
        }

        $work = Join-Path $env:ProgramData ('mt5-guardrail-setup-' + [guid]::NewGuid().ToString('N'))
        New-LockedDirectory $work

        $root = Get-SourceRoot $o $work $selfDir
        $copyNeeded = ($root.TrimEnd('\') -ne $o.Dir.TrimEnd('\'))
        if ($copyNeeded) { Copy-Tree $root $o.Dir }
        Set-InstallAcl $o.Dir $o.UserSid
        $srcLabel = 'pin:' + $script:PinCommit
        if ($null -ne $o.Source) { $srcLabel = 'source:' + $o.Source }
        elseif ($null -ne $o.Ref) { $srcLabel = 'ref:' + $o.Ref }
        elseif (Test-Path -LiteralPath (Join-Path $selfDir 'windows-side\install.ps1')) { $srcLabel = 'local-clone' }
        Write-PolicyRecord $o.Dir $o.AcceptedVia $srcLabel

        $ps1 = Join-Path $o.Dir 'windows-side\install.ps1'
        $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ps1, '-Target', $o.Dir, '-UserSid', $o.UserSid)
        if ($o.Dev) { $a += '-Dev' }
        if ($o.Test) { $a += '-Test' }
        if ($o.Wsl) { $a += '-Wsl' }
        if ($o.Yes) { $a += '-Yes' }
        if ($o.AllowSdist) { $a += '-AllowSdist' }
        if ($o.Force) { $a += '-Force' }
        if ($null -ne $o.Features) { $a += @('-Features', $o.Features) }
        & $psExe @a | Out-Host
        $exit = $LASTEXITCODE
    } catch {
        $m = $_.Exception.Message
        if ($m -match '^PG_ABORT:(\d+)$') { $exit = [int]$Matches[1] }
        else { Write-Host ('ERROR: ' + $m) -ForegroundColor Red; $exit = 1 }
    } finally {
        if ($null -ne $work -and (Test-Path -LiteralPath $work)) {
            Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    return $exit
}

if (-not $env:PG_NOMAIN) {
    $code = Invoke-Main
    if ($code -is [array]) { $code = $code[-1] }
    $isElevatedChild = ([string]$env:PG_ARGS) -match '(^|\s)--elevated(\s|$)'
    $quiet = ([string]$env:PG_ARGS) -match '(^|\s)--yes(\s|$)'
    # The elevated window closes when we exit: always wait unless --yes and everything worked.
    if ($isElevatedChild -and [Environment]::UserInteractive -and ((-not $quiet) -or ([int]$code -ne 0))) {
        Write-Host ''
        [void](Read-Host 'Finished. Press Enter to close this window')
    }
    exit ([int]$code)
}
