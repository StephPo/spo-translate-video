# Registers the spodl: and spotr: custom URL protocols for the current user
# (HKEY_CURRENT_USER, no admin rights required). Idempotent: safe to re-run,
# e.g. after moving the repo or to fix a broken registration.

$ErrorActionPreference = 'Stop'
$RepoDir = $PSScriptRoot
$HandlerBat = Join-Path $RepoDir 'spo-protocol-handler.bat'

if (-not (Test-Path $HandlerBat)) {
  Write-Error "Handler script not found: $HandlerBat"
  exit 1
}

function Register-Protocol([string]$ProtocolName, [string]$ModeArg) {
  $classPath = "HKCU:\Software\Classes\$ProtocolName"
  New-Item -Path $classPath -Force | Out-Null
  Set-ItemProperty -Path $classPath -Name '(Default)' -Value "URL:$ProtocolName Protocol"
  Set-ItemProperty -Path $classPath -Name 'URL Protocol' -Value ''

  New-Item -Path "$classPath\shell\open\command" -Force | Out-Null
  $command = '"' + $HandlerBat + '" ' + $ModeArg + ' "%1"'
  Set-ItemProperty -Path "$classPath\shell\open\command" -Name '(Default)' -Value $command

  Write-Host "Registered protocol '$ProtocolName`:' -> $command"
}

Register-Protocol -ProtocolName 'spodl' -ModeArg 'dl'
Register-Protocol -ProtocolName 'spotr' -ModeArg 'tr'

Write-Host ""
Write-Host "Done. Test with: Start-Process 'spodl:dQw4w9WgXcQ'"
Write-Host "See SPECIFICATIONS.md section 2.2 for the bookmarklets to add to your browser."
