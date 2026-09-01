# Trading Journal launcher.
# - If the app is already running on the port, just opens the browser.
# - Otherwise starts Streamlit from the repo venv, waits for the port, opens the browser.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$configPath = Join-Path $root "data\network.json"
$defaultAddress = "127.0.0.1"
$defaultPort = 8503
$defaultHeadless = "true"
$config = $null

if (Test-Path $configPath) {
    try {
        $raw = Get-Content $configPath -Raw
        $config = $raw | ConvertFrom-Json
    } catch {
        $config = $null
    }
}

$address = if ($config -and $config.server_address) { [string]$config.server_address } else { $defaultAddress }
$port = if ($config -and $config.server_port) { [int]$config.server_port } else { $defaultPort }
$openBrowser = if ($null -ne $config -and $null -ne $config.open_browser) { [bool]$config.open_browser } else { $true }
$headless = if ($null -ne $config -and $null -ne $config.server_headless) { [bool]$config.server_headless } else { $true }

if (-not $address) { $address = $defaultAddress }
if ($port -lt 1 -or $port -gt 65535) { $port = $defaultPort }
$listenHost = $address
$urlHost = if ($listenHost -eq "0.0.0.0") { "localhost" } else { $listenHost }
$url = "http://$urlHost:$port"
$headlessArg = if ($headless) { "true" } else { "false" }
$configSource = if (Test-Path $configPath) { "data\\network.json" } else { "defaults" }
Write-Host "Trading Journal launch config: source=$configSource address=$address port=$port headless=$headlessArg openBrowser=$openBrowser"

function Test-Port {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect("127.0.0.1", $port)
        $c.Close()
        return $true
    } catch { return $false }
}

if (Test-Port) {
    Write-Host "Trading Journal already running - opening browser."
    Write-Host "If you just changed network settings, stop the current Streamlit process first (Ctrl+C in terminal or task manager) and re-run launch."
    Start-Process $url
    exit 0
}

if (-not (Test-Path $python)) {
    Write-Host "Python venv not found at $python" -ForegroundColor Red
    Write-Host "Create it first:" -ForegroundColor Yellow
    Write-Host "  cd `"$root`""
    Write-Host "  python -m venv .venv"
    Write-Host "  .venv\Scripts\pip install -r requirements.txt"
    exit 1
}

Write-Host "Starting Trading Journal..."
Start-Process -FilePath $python `
    -ArgumentList "-m", "streamlit", "run", "app.py", "--server.port", "$port", "--server.address", "$address", "--server.headless", "$headlessArg" `
    -WorkingDirectory $root -WindowStyle Hidden

# Wait up to 30s for the server, then open the browser
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Port) {
        if ($openBrowser) { Start-Process $url }
        Write-Host "Trading Journal running at $url"
        exit 0
    }
}
Write-Host "Server did not start within 30s - check for errors by running:" -ForegroundColor Red
Write-Host "  `"$python`" -m streamlit run `"$root\app.py`""
exit 1
