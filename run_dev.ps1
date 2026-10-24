# Dev run on a laptop: webcam + simulated valve + separate dev database.
# Usually launched via run_dev.bat. Ctrl+C (or Enter) stops everything.
# Pass a camera source to skip the menu, e.g. run_dev.bat webcam:1 / sim / pi
param([string]$Cam = "")

Set-Location $PSScriptRoot
$env:BEAN_DB = "data\dev.db"
New-Item -ItemType Directory -Force logs, var\sessions | Out-Null

# ---- camera picker (skipped when a source was passed or stdin isn't a console)
if (-not $Cam -and -not [Console]::IsInputRedirected) {
    Write-Host "[dev] probing cameras ..."
    $found = @()
    foreach ($line in (& ".\.venv\Scripts\python.exe" engine.py --list-cameras 2>$null)) {
        if ($line -match 'index (\d+): (.+)') {
            Write-Host "      index $($Matches[1]): $($Matches[2])"
            if ($Matches[2] -notmatch 'not present') { $found += [int]$Matches[1] }
        }
    }
    Write-Host ""
    Write-Host "  Choose camera:"
    Write-Host "    [1] Laptop camera (built-in webcam)"
    Write-Host "    [2] Camera Module 3 (Raspberry Pi only)"
    Write-Host "    [3] External USB camera (pick an index)"
    Write-Host "    [4] Simulated camera (no hardware)"
    switch ((Read-Host "  Camera [1-4, Enter=1]").Trim()) {
        '2' { $Cam = 'pi' }
        '3' {
            # prefer a non-zero index: 0 is usually the built-in laptop cam
            $ext = @($found | Where-Object { $_ -ne 0 })
            $defIx = if ($ext) { $ext[0] } elseif ($found) { $found[0] } else { 1 }
            $hint = if ($found) { $found -join ', ' } else { 'none detected' }
            $ix = (Read-Host "  Index ($hint detected, Enter=$defIx)").Trim()
            if ($ix -eq '') { $ix = "$defIx" }
            $Cam = "webcam:$ix"
        }
        '4' { $Cam = 'sim' }
        default { $Cam = 'webcam' }
    }
    Write-Host ""
}
if (-not $Cam) { $Cam = 'webcam' }   # non-interactive fallback

$eng = $null
$phpProc = $null
$appMode = $false   # we only auto-close the browser if we opened a dedicated app window

function Close-AppWindow {
    if (-not $appMode) { return }
    Get-Process msedge, chrome, brave -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowTitle -match 'Bean Sorter' } |
        ForEach-Object { $_.CloseMainWindow() | Out-Null }
}

try {
    Write-Host "[dev] starting engine (camera $Cam + simulated valve) ..."
    $eng = Start-Process -PassThru -WindowStyle Hidden `
        -FilePath ".\.venv\Scripts\python.exe" `
        -ArgumentList 'engine.py', '--camera', $Cam, '--valve', 'sim' `
        -WorkingDirectory $PWD `
        -RedirectStandardOutput 'logs\dev-engine.log' `
        -RedirectStandardError  'logs\dev-engine.err'

    Write-Host "[dev] starting PHP server ..."
    try {
        $phpProc = Start-Process -PassThru -WindowStyle Hidden `
            -FilePath 'php' -ArgumentList '-S', '127.0.0.1:8080', '-t', 'web' `
            -WorkingDirectory $PWD `
            -RedirectStandardOutput 'logs\dev-php.log' `
            -RedirectStandardError  'logs\dev-php.err'
    } catch {
        Write-Host "[dev] FAILED to start PHP - is it installed and on PATH?"
        return
    }

    Write-Host "[dev] waiting for engine + PHP (up to 25 s) ..."
    $ready = $false
    foreach ($i in 1..50) {
        if ($eng.HasExited -or $phpProc.HasExited) { break }
        curl.exe -sf http://127.0.0.1:5000/api/status >$null 2>&1
        $a = $LASTEXITCODE -eq 0
        curl.exe -sf -o NUL http://127.0.0.1:8080/ >$null 2>&1
        if ($a -and $LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
        Write-Host "[dev] FAILED - see logs\dev-engine.log and logs\dev-php.log"
        return
    }

    $url = 'http://127.0.0.1:8080/operate.php'
    Write-Host "[dev] opening $url ..."
    # a dedicated --app window is unambiguous to close on exit
    $browser = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
        "$env:ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe",
        "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\Application\brave.exe"
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if ($browser) {
        Start-Process $browser "--app=$url"
        $appMode = $true
    } else {
        Start-Process $url
    }

    Write-Host ""
    Write-Host "Dev servers running. Press Enter or Ctrl+C to stop them."
    while ($true) {
        try {
            if ([Console]::KeyAvailable -and
                [Console]::ReadKey($true).Key -eq 'Enter') { break }
        } catch { }   # no console stdin (redirected) - Ctrl+C still works
        if ($eng.HasExited -or $phpProc.HasExited) {
            Write-Host "[dev] a server exited on its own - shutting down"
            break
        }
        Start-Sleep -Milliseconds 250
    }
} finally {
    Write-Host "[dev] stopping engine + PHP ..."
    # taskkill /T kills the whole tree: .venv\Scripts\python.exe is a shim that
    # spawns the real interpreter as a child, and killing only the shim leaves
    # the engine orphaned with port 5000 still bound.
    foreach ($p in @($eng, $phpProc)) {
        if ($p -and -not $p.HasExited) {
            taskkill /PID $p.Id /T /F >$null 2>&1
        }
    }
    Close-AppWindow
}
