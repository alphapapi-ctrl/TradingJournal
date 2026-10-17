param([string]$Database = "", [string]$ConfigPath = "")
$ErrorActionPreference = "Stop"
$journalRoot = Split-Path -Parent $PSScriptRoot
if (-not $Database) {
    if (-not $ConfigPath) { $ConfigPath = Join-Path $journalRoot "data\mcp\settings.json" }
    $configJson = & (Join-Path $journalRoot ".venv\Scripts\python.exe") (Join-Path $PSScriptRoot "mcp_config.py") --config $ConfigPath
    if ($LASTEXITCODE -ne 0) { throw "Cannot read MCP startup settings." }
    $Database = ($configJson | ConvertFrom-Json).database
}
$pidFile = if ($Database) { Join-Path (Split-Path -Parent ([System.IO.Path]::GetFullPath($Database))) "mcp\server.pid" } else { Join-Path $journalRoot "data\mcp\server.pid" }
if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "No background MCP process recorded. A foreground server can be stopped with Ctrl+C."
    exit 0
}
$serverProcessId = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $serverProcessId" -ErrorAction SilentlyContinue
if (-not $process) { Write-Host "MCP server is already stopped."; exit 0 }
$expectedScript = Join-Path $journalRoot "mcp_server.py"
if (-not $process.CommandLine -or $process.CommandLine.IndexOf($expectedScript, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
    throw "Recorded PID belongs to a different process. It has been left running."
}
Stop-Process -Id $serverProcessId
Write-Host "Trading Journal MCP stopped."
