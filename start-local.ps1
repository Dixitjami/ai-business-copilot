$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$runDir = Join-Path $projectRoot ".run"
$backendPidFile = Join-Path $runDir "backend.pid"
$frontendPidFile = Join-Path $runDir "frontend.pid"

$backendHost = "127.0.0.1"
$backendPort = 8001
$frontendHost = "127.0.0.1"
$frontendPort = 5500

$pythonExe = Join-Path $projectRoot "backend\venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
  throw "Missing Python environment at backend\venv\Scripts\python.exe. Create/install the backend venv first."
}

if (-not (Test-Path $runDir)) {
  New-Item -ItemType Directory -Path $runDir | Out-Null
}

function Test-PortOpen {
  param(
    [string]$HostName,
    [int]$Port
  )

  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $async = $client.BeginConnect($HostName, $Port, $null, $null)
    $connected = $async.AsyncWaitHandle.WaitOne(300)
    if (-not $connected) {
      return $false
    }
    $client.EndConnect($async)
    return $true
  } catch {
    return $false
  } finally {
    $client.Close()
  }
}

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

function Start-ServiceIfNeeded {
  param(
    [string]$Name,
    [string]$WorkingDirectory,
    [string[]]$ServiceArgs,
    [string]$PidFile,
    [string]$HostName,
    [int]$Port,
    [string]$ExpectedCommandMarker
  )

  if (Test-PortOpen -HostName $HostName -Port $Port) {
    $existingPid = Get-ListenerPid -Port $Port
    if ($existingPid -and (Is-ExpectedProcess -PidValue $existingPid -Marker $ExpectedCommandMarker)) {
      $existingPid | Set-Content -Path $PidFile -Encoding ascii
    }
    Write-Host "$Name already available at http://$HostName`:$Port"
    return
  }

  $proc = Start-Process -FilePath $pythonExe `
    -ArgumentList $ServiceArgs `
    -WorkingDirectory $WorkingDirectory `
    -WindowStyle Hidden `
    -PassThru

  $proc.Id | Set-Content -Path $PidFile -Encoding ascii
  Write-Host "Started $Name (PID $($proc.Id))"
}

$backendArgs = @("-m", "uvicorn", "backend.main:app", "--host", $backendHost, "--port", "$backendPort")
$frontendArgs = @("-m", "http.server", "$frontendPort", "--bind", $frontendHost)

Start-ServiceIfNeeded `
  -Name "backend API" `
  -WorkingDirectory $projectRoot `
  -ServiceArgs $backendArgs `
  -PidFile $backendPidFile `
  -HostName $backendHost `
  -Port $backendPort `
  -ExpectedCommandMarker "uvicorn backend.main:app"

Start-ServiceIfNeeded `
  -Name "frontend server" `
  -WorkingDirectory (Join-Path $projectRoot "frontend") `
  -ServiceArgs $frontendArgs `
  -PidFile $frontendPidFile `
  -HostName $frontendHost `
  -Port $frontendPort `
  -ExpectedCommandMarker "http.server $frontendPort"

Start-Sleep -Seconds 1

Write-Host ""
Write-Host "Frontend:  http://$frontendHost`:$frontendPort"
Write-Host "Backend:   http://$backendHost`:$backendPort"
Write-Host "API Docs:  http://$backendHost`:$backendPort/docs"
