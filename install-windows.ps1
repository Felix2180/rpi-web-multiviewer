# =============================================================================
# rpi-web-multiviewer - Windows installer
#
# From a downloaded/cloned copy:
#   powershell -ExecutionPolicy Bypass -File install-windows.ps1
#
# One-liner (downloads everything to %LOCALAPPDATA%\rpi-web-multiviewer):
#   irm https://raw.githubusercontent.com/Felix2180/rpi-web-multiviewer/main/install-windows.ps1 | iex
#
# Options (file mode): -NoAutostart  -NoFirewall  -Port 5000
# =============================================================================
param(
    [switch]$NoAutostart,
    [switch]$NoFirewall,
    [int]$Port = 5000
)

$ErrorActionPreference = "Stop"
$RepoZip = "https://github.com/Felix2180/rpi-web-multiviewer/archive/refs/heads/main.zip"
$AppName = "rpi-web-multiviewer"

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "    $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    $text" -ForegroundColor Yellow }

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Has-Winget { return [bool](Get-Command winget -ErrorAction SilentlyContinue) }

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  $AppName - Windows installer" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

# -----------------------------------------------------------------------------
# 1. Project files (download when run via "irm | iex")
# -----------------------------------------------------------------------------
$ProjectDir = $null
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "src\video_controller.py"))) {
    $ProjectDir = $PSScriptRoot
} else {
    $ProjectDir = Join-Path $env:LOCALAPPDATA $AppName
    Step "Downloading $AppName to $ProjectDir"
    $zip = Join-Path $env:TEMP "$AppName.zip"
    $extract = Join-Path $env:TEMP "$AppName-extract"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest $RepoZip -OutFile $zip -UseBasicParsing
    if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
    Expand-Archive $zip $extract -Force
    $srcDir = (Get-ChildItem $extract -Directory | Select-Object -First 1).FullName
    New-Item -ItemType Directory -Force $ProjectDir | Out-Null
    # update-safe: never overwrite videos, presets, state or the venv
    robocopy $srcDir $ProjectDir /E /XD data config logs .venv /NFL /NDL /NJH /NJS /NP | Out-Null
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    Remove-Item $extract -Recurse -Force -ErrorAction SilentlyContinue
}
Set-Location $ProjectDir
Ok "Project: $ProjectDir"

# -----------------------------------------------------------------------------
# 2. Python 3
# -----------------------------------------------------------------------------
function Find-Python {
    foreach ($cand in @(@("py", "-3"), @("python"))) {
        $exe = $cand[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $pyArgs = @()
            if ($cand.Count -gt 1) { $pyArgs += $cand[1..($cand.Count - 1)] }
            $pyArgs += @("-c", "import sys; print(sys.executable) if sys.version_info >= (3, 9) else None")
            $out = & $exe @pyArgs 2>$null
            if ($LASTEXITCODE -eq 0 -and $out -and (Test-Path $out.Trim())) { return $out.Trim() }
        } catch { }
    }
    return $null
}

Step "Checking Python"
$Python = Find-Python
if (-not $Python) {
    if (-not (Has-Winget)) { throw "Python 3.9+ not found and winget is not available. Install Python from https://www.python.org/downloads/ and run this script again." }
    Warn "Python not found - installing Python 3.12 via winget..."
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
    $Python = Find-Python
    if (-not $Python) { throw "Python was installed but cannot be found yet. Open a new PowerShell window and run the installer again." }
}
Ok "Python: $Python"

# -----------------------------------------------------------------------------
# 3. Virtual environment + dependencies
# -----------------------------------------------------------------------------
Step "Installing Python dependencies"
$VenvPy = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) { & $Python -m venv .venv }
& $VenvPy -m pip install --upgrade pip --quiet
& $VenvPy -m pip install -r requirements-windows.txt --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
Ok "Dependencies installed"

# -----------------------------------------------------------------------------
# 4. mpv
# -----------------------------------------------------------------------------
function Find-Mpv {
    $code = "import sys; sys.path.insert(0, 'src'); import platform_support as p; print(p.mpv_executable())"
    $out = (& $VenvPy -c $code 2>$null)
    if ($out -and (Test-Path $out.Trim())) { return $out.Trim() }
    return $null
}

Step "Checking mpv"
$Mpv = Find-Mpv
if (-not $Mpv) {
    if (Has-Winget) {
        Warn "mpv not found - installing mpv (shinchiro build) via winget..."
        winget install -e --id shinchiro.mpv --silent --accept-package-agreements --accept-source-agreements
        Refresh-Path
        $Mpv = Find-Mpv
    }
}
if ($Mpv) {
    Ok "mpv: $Mpv"
} else {
    Warn "mpv could not be installed automatically."
    Warn "Download it from https://mpv.io/installation/ and put mpv.exe into: $ProjectDir\mpv\"
}

# -----------------------------------------------------------------------------
# 5. Firewall (web UI reachable from other devices)
# -----------------------------------------------------------------------------
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $NoFirewall) {
    Step "Firewall"
    if ($IsAdmin) {
        Get-NetFirewallRule -DisplayName $AppName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        New-NetFirewallRule -DisplayName $AppName -Direction Inbound -Protocol TCP -LocalPort $Port `
            -Action Allow -Profile Private,Domain | Out-Null
        Ok "Inbound TCP $Port allowed (private/domain networks)"
    } else {
        Warn "Not running as administrator - skipped. Windows will ask on first start;"
        Warn "allow access for private networks so other devices can open the web UI."
    }
}

# -----------------------------------------------------------------------------
# 6. Autostart (shortcut in the user's Startup folder, no console window)
# -----------------------------------------------------------------------------
$VenvPyw = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"
$Script  = Join-Path $ProjectDir "src\video_controller.py"
if (-not $NoAutostart) {
    Step "Autostart"
    $startup = [Environment]::GetFolderPath("Startup")
    $lnk = Join-Path $startup "$AppName.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($lnk)
    $sc.TargetPath = $VenvPyw
    $sc.Arguments = "`"$Script`" --port $Port"
    $sc.WorkingDirectory = $ProjectDir
    $sc.Description = "$AppName web multiviewer"
    $sc.Save()
    Ok "Starts automatically at logon ($lnk)"
}

# -----------------------------------------------------------------------------
# 7. Start now
# -----------------------------------------------------------------------------
Step "Starting $AppName"
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*video_controller.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1
Start-Process -FilePath $VenvPyw -ArgumentList "`"$Script`" --port $Port" -WorkingDirectory $ProjectDir
Start-Sleep -Seconds 3
Start-Process "http://localhost:$Port"

$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
       Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
       Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host "  Done." -ForegroundColor Green
Write-Host "  Web UI:  http://localhost:$Port" -ForegroundColor Green
if ($ip) { Write-Host "           http://${ip}:$Port (other devices)" -ForegroundColor Green }
Write-Host "  Logs:    $ProjectDir\logs\app.log" -ForegroundColor Green
Write-Host "  Stop:    stop-multiviewer.bat" -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
