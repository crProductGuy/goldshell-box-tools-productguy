<#
.SYNOPSIS
  Start `gbox serve` at logon on Windows, without administrator rights.

.DESCRIPTION
  Writes a small launcher into the user's Startup folder that runs
  `pythonw -m gbox serve` hidden (no console window), then starts it now.
  A second copy exits by itself because the port is already taken.

  Run from a clone of the repository:

    powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -Uninstall

  Before this, run `gbox init` (or `python -m gbox init`) once so config.json
  holds the miner address, and `gbox serve --remember` if the logger and
  watchdog should work after a reboot without anyone opening the dashboard.

.PARAMETER Uninstall
  Remove the launcher and stop a running service.
.PARAMETER DataDir
  Data directory to pass as --data. Default: gbox's default (~/.gbox).
#>
param(
  [switch]$Uninstall,
  [string]$DataDir = ""
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$startup = [Environment]::GetFolderPath("Startup")
$launcher = Join-Path $startup "gbox-serve.vbs"

function Stop-Gbox {
  Get-CimInstance Win32_Process -Filter "name='pythonw.exe' or name='python.exe'" |
    Where-Object { $_.CommandLine -match "-m gbox serve" } |
    ForEach-Object { Write-Host "stopping gbox serve (pid $($_.ProcessId))"; Stop-Process -Id $_.ProcessId -Force }
}

if ($Uninstall) {
  if (Test-Path $launcher) { Remove-Item $launcher; Write-Host "removed $launcher" }
  Stop-Gbox
  exit 0
}

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "python not found on PATH. Install Python 3 from python.org and tick 'Add to PATH'." }
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python }

$args = "-m gbox serve"
if ($DataDir) { $args += " --data ""$DataDir""" }

$vbs = @"
' Starts the gbox service (dashboard, logger, watchdog) hidden at logon. Written by scripts\install-windows.ps1.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "$repo"
sh.Run """$pythonw"" $args", 0, False
"@
Set-Content -Path $launcher -Value $vbs -Encoding ASCII
Write-Host "wrote $launcher"

Stop-Gbox
Start-Process -FilePath "wscript.exe" -ArgumentList "`"$launcher`"" -WorkingDirectory $repo
Start-Sleep -Seconds 3
$port = 8765
try {
  $cfgPath = if ($DataDir) { Join-Path $DataDir "config.json" } else { Join-Path $HOME ".gbox\config.json" }
  if (Test-Path $cfgPath) { $port = (Get-Content $cfgPath | ConvertFrom-Json).port }
} catch {}
try {
  $h = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 5
  Write-Host "gbox $($h.version) is up: http://127.0.0.1:$port/  (miner $($h.host), poll every $($h.poll_interval) s)"
} catch {
  Write-Warning "the service did not answer on port $port yet. Run 'python -m gbox serve' in a console to see why."
}
