$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$runDir = Join-Path $projectRoot ".run"
$backendPidFile = Join-Path $runDir "backend.pid"
$frontendPidFile = Join-Path $runDir "frontend.pid"

function Get-ListenerPid {
  param(
    [int]$Port
  )

  $lines = netstat -ano -p tcp | Select-String "LISTENING"
  foreach ($line in $lines) {
    $parts = ($line.ToString().Trim() -split "\s+")
    if ($parts.Length -lt 5) {
      continue
    }
    $localAddress = $parts[1]
    $state = $parts[3]
    $pidText = $parts[4]
    if ($state -eq "LISTENING" -and $localAddress -match ":$Port$") {
      $pidValue = 0
      if ([int]::TryParse($pidText, [ref]$pidValue)) {
        return $pidValue
      }
    }
  }
  return $null
}

function Is-ExpectedProcess {
  param(
    [int]$PidValue,
    [string]$Marker
  )

  $procInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $PidValue" -ErrorAction SilentlyContinue
  if ($null -eq $procInfo) {
    return $false
  }
  $commandLine = ($procInfo.CommandLine | Out-String).Trim().ToLower()
  return $commandLine.Contains($Marker.ToLower())
}

function Stop-FromPidFile {
  param(
    [string]$Name,
    [string]$PidFile,
    [int]$Port,
    [string]$ExpectedCommandMarker
  )

  if (-not (Test-Path $PidFile)) {
    $listenerPid = Get-ListenerPid -Port $Port
    if ($listenerPid -and (Is-ExpectedProcess -PidValue $listenerPid -Marker $ExpectedCommandMarker)) {
      Stop-Process -Id $listenerPid -Force
      Write-Host "Stopped $Name from active port listener (PID $listenerPid)."
      return
    }
    Write-Host "$Name PID file not found."
    return
  }

  $raw = Get-Content -Raw -Path $PidFile
  $pidValue = 0
  if (-not [int]::TryParse($raw.Trim(), [ref]$pidValue)) {
    Remove-Item -Path $PidFile -Force
    Write-Host "$Name PID file was invalid and has been removed."
    return
  }

  $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
  if ($null -eq $proc) {
    Remove-Item -Path $PidFile -Force
    Write-Host "$Name process was not running."
    return
  }

  Stop-Process -Id $pidValue -Force
  Remove-Item -Path $PidFile -Force
  Write-Host "Stopped $Name (PID $pidValue)."
}

Stop-FromPidFile `
  -Name "backend API" `
  -PidFile $backendPidFile `
  -Port 8001 `
  -ExpectedCommandMarker "uvicorn backend.main:app"

Stop-FromPidFile `
  -Name "frontend server" `
  -PidFile $frontendPidFile `
  -Port 5500 `
  -ExpectedCommandMarker "http.server 5500"
