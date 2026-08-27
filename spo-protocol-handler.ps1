param(
  [Parameter(Mandatory=$true)][ValidateSet('dl','tr')][string]$Mode,
  [Parameter(Mandatory=$true)][string]$Raw
)

# --------------------------------------------------------------------------
# Everything below is wrapped in a single global try/catch (SPECIFICATIONS.md
# section 2.2): this script must NEVER fail before logging something and
# showing the user an actionable message, even for "unthinkable" errors
# (execution policy issues, malformed $Raw, etc.).
# --------------------------------------------------------------------------

$ErrorActionPreference = 'Stop'
$RepoDir = $PSScriptRoot
$Log = Join-Path $RepoDir 'protocol-handler.log'

function Write-Log([string]$Message) {
  try {
    $line = "[{0}] pid={1} mode={2} {3}" -f (Get-Date -Format 'dd/MM/yyyy HH:mm:ss,fff'), $PID, $Mode, $Message
    Add-Content -Path $Log -Value $line -Encoding UTF8
  } catch {
    # Even logging can fail (e.g. read-only disk); never let this crash the script.
  }
}

function Show-FatalErrorWindow([string]$Message, [string]$AttemptedCommand) {
  $lines = @(
    "spo-translate-video protocol handler FAILED.",
    "",
    "Error: $Message",
    "",
    "Log file: $Log",
    "",
    "Diagnostic steps:",
    "  1) Re-run install-protocol-handlers.ps1 to reset the registry association.",
    "  2) Check that Node.js and ffmpeg are on PATH (see SPECIFICATIONS.md section 7).",
    "  3) Check the registry key HKCU\Software\Classes\spodl (or spotr).",
    ""
  )
  if ($AttemptedCommand) {
    $lines += "Command that was attempted (copy/paste to retry manually):"
    $lines += "  $AttemptedCommand"
    $lines += ""
  }
  $script = ($lines | ForEach-Object { "echo $($_.Replace('"','""'))" }) -join ' & '
  # Always keep the window open (cmd /k), even in this fallback path.
  Start-Process -FilePath 'cmd.exe' -ArgumentList @('/k', $script) | Out-Null
}

try {
  Write-Log "received raw=$Raw"

  function ConvertTo-YouTubeUrl([string]$value) {
    if ([string]::IsNullOrWhiteSpace($value)) { return '' }
    $v = $value
    if ($Mode -eq 'dl') { $v = $v -replace '^spodl:(//)?', '' } else { $v = $v -replace '^spotr:(//)?', '' }
    try { $v = [System.Uri]::UnescapeDataString($v) } catch { }
    if ($v -match '^https?://') { return $v }
    if ($v -match '^(www\.)?(youtube\.com|youtu\.be)/') { return 'https://' + $v }
    return 'https://www.youtube.com/watch?v=' + $v
  }

  $Url = ConvertTo-YouTubeUrl $Raw
  Write-Log "normalized_url=$Url"

  if ([string]::IsNullOrWhiteSpace($Url)) {
    Write-Log "ERROR normalized_url_empty"
    Show-FatalErrorWindow -Message "Could not extract a URL/video ID from the input ('$Raw')." -AttemptedCommand $null
    exit 1
  }

  $Runner = if ($Mode -eq 'dl') { 'spo-dl-video.bat' } else { 'spo-translate-video.bat' }
  $RunnerPath = Join-Path $RepoDir $Runner
  $cmdLine = "`"$RunnerPath`" `"$Url`""

  if (-not (Test-Path $RunnerPath)) {
    Write-Log "ERROR missing_runner=$RunnerPath"
    Show-FatalErrorWindow -Message "Runner script not found: $RunnerPath" -AttemptedCommand $cmdLine
    exit 1
  }

  $UseWindowsTerminal = [string]::Equals($env:SPO_PROTOCOL_USE_WT, '1') -or [string]::Equals($env:SPO_PROTOCOL_USE_WT, 'true', [System.StringComparison]::OrdinalIgnoreCase)
  $wt = if ($UseWindowsTerminal) { Get-Command wt.exe -ErrorAction SilentlyContinue } else { $null }
  $cmdForCmdExe = "`"$cmdLine`""
  $launched = $false

  if ($wt) {
    try {
      Write-Log "launching=wt exe=$($wt.Source) cmd=$cmdForCmdExe"
      Start-Process -FilePath $wt.Source -ArgumentList @('cmd.exe', '/k', $cmdForCmdExe) | Out-Null
      $launched = $true
    } catch {
      Write-Log "WARN wt_launch_failed=$($_.Exception.Message)"
    }
  }

  if (-not $launched) {
    Write-Log "launching=cmd cmd=$cmdForCmdExe"
    # /k (never /c): the window MUST stay open so any failure inside the runner is visible.
    Start-Process -FilePath 'cmd.exe' -ArgumentList @('/k', $cmdForCmdExe) | Out-Null
    $launched = $true
  }

  Write-Log "launch_ok=$launched"
  exit 0
} catch {
  $msg = $_.Exception.Message
  Write-Log "ERROR unhandled=$msg"
  Show-FatalErrorWindow -Message $msg -AttemptedCommand $null
  exit 1
}
