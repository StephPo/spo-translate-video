$keys = @(
  "HKCU:\Software\Classes\spodl",
  "HKCU:\Software\Classes\spotr"
)

foreach ($k in $keys) {
  if (Test-Path $k) {
    Remove-Item -Path $k -Recurse -Force
    Write-Host "Removed $k"
  } else {
    Write-Host "Not found: $k"
  }
}

Write-Host "Uninstalled protocol handlers (HKCU): spodl:// and spotr://"
