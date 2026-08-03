# Trading Journal launcher.
# - If the app is already running on the port, just opens the browser.
# - Otherwise starts Streamlit from the repo venv, waits for the port, opens the browser.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$port = 8502
$url = "http://localhost:$port"
$python = Join-Path $root ".venv\Scripts\python.exe"

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
    -ArgumentList "-m", "streamlit", "run", "app.py", "--server.port", "$port", "--server.headless", "true" `
    -WorkingDirectory $root -WindowStyle Hidden

# Wait up to 30s for the server, then open the browser
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Port) {
        Start-Process $url
        Write-Host "Trading Journal running at $url"
        exit 0
    }
}
Write-Host "Server did not start within 30s - check for errors by running:" -ForegroundColor Red
Write-Host "  `"$python`" -m streamlit run `"$root\app.py`""
exit 1
