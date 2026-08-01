# Starts the backend (FastAPI/uvicorn), the analysis worker, and the frontend
# (Vite), each in its own window.
# Run with: .\dev.ps1   (from the repo root)
#
# The worker is a separate process on purpose. It executes Stockfish analysis
# so the API never has to, which is what lets you restart either one without
# disturbing the other. Skip it with -NoWorker if you only need the UI.

param(
  [switch]$NoWorker,
  # Games analysed at once. Defaults to ANALYSIS_WORKER_CONCURRENCY in .env.
  # CPU demand is roughly this times STOCKFISH_THREADS.
  [int]$Concurrency = 0
)

$repoRoot = $PSScriptRoot
$workerArgs = if ($Concurrency -gt 0) { " --concurrency $Concurrency" } else { "" }

Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "Set-Location '$repoRoot\backend'; .\.venv\Scripts\Activate.ps1; uvicorn app.main:app --reload --reload-dir app --port 8001"
)

if (-not $NoWorker) {
  Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$repoRoot\backend'; .\.venv\Scripts\Activate.ps1; python -m app.worker$workerArgs"
  )
}

Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "Set-Location '$repoRoot\frontend'; npm run dev"
)

if ($NoWorker) {
  Write-Host "Backend and frontend dev servers are launching in separate windows (no worker)."
} else {
  Write-Host "Backend, analysis worker, and frontend are launching in separate windows."
  Write-Host "The worker exits immediately if STOCKFISH_PATH is not set in backend\.env."
}
