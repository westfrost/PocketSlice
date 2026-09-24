<#
.SYNOPSIS
  Copy your OrcaSlicer presets from this Windows PC to the PocketSlice server.

.DESCRIPTION
  Copies %APPDATA%\OrcaSlicer\{user,system,OrcaSlicer.conf} to either a network
  share / mapped drive (-Destination) or over SSH with scp (-Ssh), then asks
  PocketSlice to rescan its profiles folder.

.EXAMPLE
  # to a Samba share that is mounted as the container's ./profiles folder
  .\sync-profiles.ps1 -Destination "\\nas\docker\pocketslice\profiles" -AppUrl http://nas:8080

.EXAMPLE
  # over SSH (needs OpenSSH client, which Windows 10/11 ships with)
  .\sync-profiles.ps1 -Ssh pi@voron.local:/home/pi/pocketslice/profiles -AppUrl http://voron.local:8080

  Schedule it: Task Scheduler -> "At log on" or daily -> run
  powershell -ExecutionPolicy Bypass -File C:\path\sync-profiles.ps1 -Destination ...
#>
param(
  [string]$Source = "$env:APPDATA\OrcaSlicer",
  [string]$Destination = "",
  [string]$Ssh = "",
  [string]$AppUrl = "",
  [string]$AppPassword = ""
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $Source)) { throw "OrcaSlicer config not found at $Source" }
if (-not $Destination -and -not $Ssh) { throw "Give either -Destination <folder> or -Ssh user@host:/path" }

$items = @("user", "system", "OrcaSlicer.conf") | Where-Object { Test-Path (Join-Path $Source $_) }
Write-Host "Syncing $($items -join ', ') from $Source"

if ($Destination) {
  New-Item -ItemType Directory -Force -Path $Destination | Out-Null
  foreach ($item in $items) {
    $src = Join-Path $Source $item
    if ((Get-Item $src).PSIsContainer) {
      robocopy $src (Join-Path $Destination $item) /MIR /R:2 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
      if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $item (code $LASTEXITCODE)" }
    } else {
      Copy-Item $src -Destination $Destination -Force
    }
  }
} else {
  foreach ($item in $items) {
    $src = Join-Path $Source $item
    & scp -r -q $src "$Ssh/"
    if ($LASTEXITCODE -ne 0) { throw "scp failed for $item" }
  }
}
Write-Host "Copied." -ForegroundColor Green

if ($AppUrl) {
  try {
    $session = $null
    if ($AppPassword) {
      Invoke-RestMethod -Method Post -Uri "$AppUrl/api/login" -ContentType "application/json" `
        -Body (@{ password = $AppPassword } | ConvertTo-Json) -SessionVariable session | Out-Null
    }
    $r = Invoke-RestMethod -Method Post -Uri "$AppUrl/api/presets/reload" -WebSession $session
    Write-Host ("PocketSlice rescanned: {0} printer / {1} process / {2} filament presets" -f `
      $r.counts.machine.user, $r.counts.process.user, $r.counts.filament.user) -ForegroundColor Green
  } catch {
    Write-Warning "Could not tell PocketSlice to rescan ($_). Tap 'Rescan folder' in the app."
  }
}
