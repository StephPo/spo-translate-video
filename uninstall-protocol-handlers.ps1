# Removes the spodl: and spotr: custom URL protocol registrations for the
# current user (HKEY_CURRENT_USER). Safe to run even if not installed.

$ErrorActionPreference = 'SilentlyContinue'

foreach ($proto in @('spodl', 'spotr')) {
  $classPath = "HKCU:\Software\Classes\$proto"
  if (Test-Path $classPath) {
    Remove-Item -Path $classPath -Recurse -Force
    Write-Host "Removed protocol '$proto`:'"
  } else {
    Write-Host "Protocol '$proto`:' was not registered."
  }
}
