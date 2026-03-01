param(
  [Parameter(Mandatory=$true)][ValidateSet('dl','tr')][string]$Mode,
  [Parameter(Mandatory=$true)][string]$Raw
)

$RepoDir = $PSScriptRoot
$Log = Join-Path $RepoDir 'protocol-handler.log'

Add-Content -Path $Log -Value "" -Encoding UTF8
Add-Content -Path $Log -Value "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss,ff')] ps1 mode=$Mode raw=$Raw" -Encoding UTF8

function Normalize-Url([string]$value) {
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

$Url = Normalize-Url $Raw
Add-Content -Path $Log -Value "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss,ff')] ps1 normalized_url=$Url" -Encoding UTF8

$Runner = if ($Mode -eq 'dl') { 'spo-dl-video.bat' } else { 'spo-translate-video.bat' }
$RunnerPath = Join-Path $RepoDir $Runner
if (-not (Test-Path $RunnerPath)) {
  Add-Content -Path $Log -Value "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss,ff')] ps1 ERROR missing_runner=$RunnerPath" -Encoding UTF8
  exit 1
}

$Title = if ($Mode -eq 'dl') { 'SPO Download' } else { 'SPO Translate' }

$wt = (Get-Command wt.exe -ErrorAction SilentlyContinue)
if ($wt) {
  $cmdLine = "`"$RunnerPath`" `"$Url`""
  Add-Content -Path $Log -Value "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss,ff')] ps1 launching=wt cmd=$cmdLine" -Encoding UTF8
  # Avoid --title (not supported on all wt versions and can be misparsed). Always launch cmd.exe explicitly.
  Start-Process -FilePath $wt.Source -ArgumentList @('cmd.exe', '/k', $cmdLine) | Out-Null
} else {
  $cmdLine = "`"$RunnerPath`" `"$Url`""
  Add-Content -Path $Log -Value "[$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss,ff')] ps1 launching=cmd cmd=$cmdLine" -Encoding UTF8
  Start-Process -FilePath 'cmd.exe' -ArgumentList @('/k', $cmdLine) | Out-Null
}
