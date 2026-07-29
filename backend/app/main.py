from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import engine, games, health, uploads

app = FastAPI(title="Chess Insights API")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(uploads.router, prefix="/api")
app.include_router(games.router, prefix="/api")
app.include_router(engine.router, prefix="/api")
