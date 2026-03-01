param(
  [string]$RepoDir = $PSScriptRoot
)

$RepoDir = (Resolve-Path $RepoDir).Path
$HandlerBat = Join-Path $RepoDir "spo-protocol-handler.bat"
$HandlerPs1 = Join-Path $RepoDir "spo-protocol-handler.ps1"
$Pwsh = (Get-Command powershell.exe -ErrorAction SilentlyContinue).Source

if (-not (Test-Path $HandlerBat)) {
  Write-Error "Missing handler: $HandlerBat"
  exit 1
}
if (-not (Test-Path $HandlerPs1)) {
  Write-Error "Missing handler: $HandlerPs1"
  exit 1
}
if (-not $Pwsh) {
  Write-Error "Missing PowerShell executable (powershell.exe)"
  exit 1
}

function Set-Protocol($name, $mode) {
  $root = "HKCU:\Software\Classes\$name"
  New-Item -Path $root -Force | Out-Null
  New-ItemProperty -Path $root -Name "(Default)" -Value "URL:$name Protocol" -PropertyType String -Force | Out-Null
  New-ItemProperty -Path $root -Name "URL Protocol" -Value "" -PropertyType String -Force | Out-Null

  $iconKey = Join-Path $root "DefaultIcon"
  New-Item -Path $iconKey -Force | Out-Null
  New-ItemProperty -Path $iconKey -Name "(Default)" -Value "$HandlerBat,0" -PropertyType String -Force | Out-Null

  $cmdKey = Join-Path $root "shell\open\command"
  New-Item -Path $cmdKey -Force | Out-Null

  $cmd = '"{0}" -NoProfile -ExecutionPolicy Bypass -File "{1}" -Mode {2} -Raw "%1"' -f $Pwsh, $HandlerPs1, $mode
  New-ItemProperty -Path $cmdKey -Name "(Default)" -Value $cmd -PropertyType String -Force | Out-Null
}

Set-Protocol -name "spodl" -mode "dl"
Set-Protocol -name "spotr" -mode "tr"

Write-Host "Installed protocol handlers: spodl:// and spotr:// (HKCU)"
Write-Host "Repo: $RepoDir"
