# Starts backend (FastAPI/uvicorn) and frontend (Vite) dev servers, each in its own window.
# Run with: .\dev.ps1   (from the repo root)

$repoRoot = $PSScriptRoot

Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "Set-Location '$repoRoot\backend'; .\.venv\Scripts\Activate.ps1; uvicorn app.main:app --reload --reload-dir app --port 8001"
)

Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "Set-Location '$repoRoot\frontend'; npm run dev"
)

Write-Host "Backend and frontend dev servers are launching in separate windows."
