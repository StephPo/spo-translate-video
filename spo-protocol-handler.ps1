param(
  [Parameter(Mandatory=$true)][ValidateSet('dl','tr')][string]$Mode,
  [Parameter(Mandatory=$true)][string]$Raw
)

$ErrorActionPreference = 'Stop'

$RepoDir = $PSScriptRoot
$Log = Join-Path $RepoDir 'protocol-handler.log'

Add-Content -Path $Log -Value "" -Encoding UTF8
Add-Content -Path $Log -Value "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss,ff')] ps1 mode=$Mode raw=$Raw" -Encoding UTF8

$Debug = $false
try {
  $Debug = [string]::Equals($env:SPO_PROTOCOL_DEBUG, '1') -or [string]::Equals($env:SPO_PROTOCOL_DEBUG, 'true', [System.StringComparison]::OrdinalIgnoreCase)
} catch {
  $Debug = $false
}

function Invoke-DebugExit([int]$code) {
  if ($Debug) {
    Write-Host ""
    Write-Host "Protocol handler debug mode is enabled (SPO_PROTOCOL_DEBUG=1)."
    Read-Host "Press Enter to close"
  }
  exit $code
}

function ConvertTo-YouTubeUrl([string]$value) {
  if ([string]::IsNullOrWhiteSpace($value)) { return '' }

  $v = $value
  if ($Mode -eq 'dl') {
    $v = $v -replace '^spodl:(//)?', ''
  } else {
    $v = $v -replace '^spotr:(//)?', ''
  }

  if ($v -match '^https?://') { return $v }
  if ($v -match '^(www\.)?(youtube\.com|youtu\.be)/') { return 'https://' + $v }

  # Assume it's just the video id
  return 'https://www.youtube.com/watch?v=' + $v
}

try {
  $Url = ConvertTo-YouTubeUrl $Raw
  Add-Content -Path $Log -Value "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss,ff')] ps1 normalized_url=$Url" -Encoding UTF8
} catch {
  Add-Content -Path $Log -Value "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss,ff')] ps1 ERROR normalize_url=$($_.Exception.Message)" -Encoding UTF8
  Invoke-DebugExit 1
}

if ([string]::IsNullOrWhiteSpace($Url)) {
  Add-Content -Path $Log -Value "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss,ff')] ps1 ERROR normalized_url_empty" -Encoding UTF8
  Invoke-DebugExit 1
}

$Runner = if ($Mode -eq 'dl') { 'spo-dl-video.bat' } else { 'spo-translate-video.bat' }
$RunnerPath = Join-Path $RepoDir $Runner
if (-not (Test-Path $RunnerPath)) {
  Add-Content -Path $Log -Value "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss,ff')] ps1 ERROR missing_runner=$RunnerPath" -Encoding UTF8
  Invoke-DebugExit 1
}

$UseWindowsTerminal = $false
try {
  $UseWindowsTerminal = [string]::Equals($env:SPO_PROTOCOL_USE_WT, '1') -or [string]::Equals($env:SPO_PROTOCOL_USE_WT, 'true', [System.StringComparison]::OrdinalIgnoreCase)
} catch {
  $UseWindowsTerminal = $false
}

$wt = if ($UseWindowsTerminal) { Get-Command wt.exe -ErrorAction SilentlyContinue } else { $null }
try {
  $cmdLine = "`"$RunnerPath`" `"$Url`""
  $cmdForCmdExe = "`"$cmdLine`""
  $launched = $false

  if ($wt) {
    try {
      Add-Content -Path $Log -Value "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss,ff')] ps1 launching=wt exe=$($wt.Source) cmd=$cmdForCmdExe" -Encoding UTF8
      Start-Process -FilePath $wt.Source -ArgumentList @('cmd.exe', '/k', $cmdForCmdExe) | Out-Null
      $launched = $true
    } catch {
      Add-Content -Path $Log -Value "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss,ff')] ps1 WARN wt_launch_failed=$($_.Exception.Message)" -Encoding UTF8
    }
  }

  if (-not $launched) {
    Add-Content -Path $Log -Value "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss,ff')] ps1 launching=cmd cmd=$cmdForCmdExe" -Encoding UTF8
    Start-Process -FilePath 'cmd.exe' -ArgumentList @('/k', $cmdForCmdExe) | Out-Null
  }
} catch {
  Add-Content -Path $Log -Value "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss,ff')] ps1 ERROR launch=$($_.Exception.Message)" -Encoding UTF8
  Invoke-DebugExit 1
}

Invoke-DebugExit 0
