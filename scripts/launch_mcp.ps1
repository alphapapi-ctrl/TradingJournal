param(
    [string]$BindAddress = "",
    [ValidateRange(0, 65535)][int]$Port = 0,
    [string[]]$AllowedHost = @(),
    [string[]]$AllowedOrigin = @(),
    [string]$Database = "",
    [string]$ConfigPath = "",
    [switch]$Install,
    [switch]$Background
)

$ErrorActionPreference = "Stop"
$journalRoot = Split-Path -Parent $PSScriptRoot
$journalPython = Join-Path $journalRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $journalPython)) {
    throw "Journal virtual environment missing. Set up the journal's .venv first."
}
if (-not $ConfigPath) { $ConfigPath = Join-Path $journalRoot "data\mcp\settings.json" }
$ConfigPath = [System.IO.Path]::GetFullPath($ConfigPath)
$configJson = & $journalPython (Join-Path $PSScriptRoot "mcp_config.py") --config $ConfigPath
if ($LASTEXITCODE -ne 0) { throw "Cannot read MCP startup settings." }
$mcpConfig = $configJson | ConvertFrom-Json
if (-not $BindAddress) { $BindAddress = $mcpConfig.bind_address }
if (-not $Port) { $Port = [int]$mcpConfig.port }
if (-not $PSBoundParameters.ContainsKey("AllowedHost")) { $AllowedHost = @($mcpConfig.allowed_hosts) }
if (-not $Database) { $Database = $mcpConfig.database }
if ($Install) {
    & $journalPython -m pip install -r (Join-Path $journalRoot "requirements.txt") -r (Join-Path $journalRoot "requirements-mcp.txt")
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
}
& $journalPython -c "from mcp.server import MCPServer"
if ($LASTEXITCODE -ne 0) { throw "MCP dependency missing. Run this launcher with -Install once." }

# Check that this endpoint isn't already running; don't spawn duplicate services.
$healthAddress = if ($BindAddress -eq "0.0.0.0") { "127.0.0.1" } else { $BindAddress }
try {
    $health = Invoke-RestMethod -Uri "http://${healthAddress}:$Port/health" -TimeoutSec 2
    if ($health.service -eq "trading-journal-mcp") {
        Write-Host "Trading Journal MCP is already running on port $Port."
        exit 0
    }
} catch { }

$journalArgs = @((Join-Path $journalRoot "mcp_server.py"), "--config", $ConfigPath, "--host", $BindAddress, "--port", "$Port")
if ($Database) {
    $Database = [System.IO.Path]::GetFullPath($Database)
    $journalArgs += @("--database", $Database)
}
foreach ($hostName in $AllowedHost) { $journalArgs += @("--allowed-host", $hostName) }
foreach ($origin in $AllowedOrigin) { $journalArgs += @("--allowed-origin", $origin) }
Write-Host "Journal MCP endpoint: http://<this-machine-LAN-IP>:$Port/mcp"
if ($Background) {
    $logDir = if ($Database) { Join-Path (Split-Path -Parent $Database) "mcp" } else { Join-Path $journalRoot "data\mcp" }
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    # close_fds in the helper prevents the service from keeping this shell's
    # redirected output pipes alive after the launcher exits.
    $launchedProcessId = & $journalPython (Join-Path $PSScriptRoot "start_mcp_background.py") --log-dir $logDir -- @journalArgs
    if ($LASTEXITCODE -ne 0) { throw "Could not launch MCP. Check $logDir." }
    $process = Get-Process -Id ([int]$launchedProcessId)
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 300
        $process.Refresh()
        if ($process.HasExited) { throw "Server exited. See $logDir\server.stderr.log." }
        try {
            $health = Invoke-RestMethod -Uri "http://${healthAddress}:$Port/health" -TimeoutSec 1
            if ($health.service -eq "trading-journal-mcp") { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) { throw "Startup was not confirmed. Check $logDir\server.stderr.log before retrying." }
    Set-Content -LiteralPath (Join-Path $logDir "server.pid") -Value $health.pid
    Write-Host "Running in background (PID $($health.pid)), below normal priority. Logs: $logDir"
} else {
    & $journalPython @journalArgs
    exit $LASTEXITCODE
}
