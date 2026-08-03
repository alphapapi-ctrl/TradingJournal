# Creates a desktop shortcut for the Trading Journal launcher. Run once:
#   powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $desktop "Trading Journal.lnk"))
$lnk.TargetPath = Join-Path $root "launch.bat"
$lnk.WorkingDirectory = $root
$lnk.IconLocation = "shell32.dll,13"
$lnk.Description = "Start the Trading Journal app"
$lnk.Save()
Write-Host "Shortcut created: $desktop\Trading Journal.lnk"
