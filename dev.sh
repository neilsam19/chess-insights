#!/usr/bin/env bash
# Starts backend (FastAPI/uvicorn) and frontend (Vite) dev servers together.
# Run with: bash dev.sh   (from the repo root, in Git Bash)
# Ctrl+C stops both.

cleanup() {
  echo "Stopping dev servers..."
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

(cd backend && source .venv/Scripts/activate && uvicorn app.main:app --reload) &
BACKEND_PID=$!

(cd frontend && npm run dev) &
FRONTEND_PID=$!

wait
