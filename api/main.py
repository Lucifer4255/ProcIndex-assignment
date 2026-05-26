"""FastAPI app — lifespan, CORS, Logfire, chat route, static UI."""

from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as redis
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agent.storage import SessionStore
from api.routes.chat import router as chat_router
from api.settings import get_settings

load_dotenv()

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    app.state.redis = redis_client
    app.state.session_store = SessionStore(redis_client)
    yield
    await redis_client.aclose()


app = FastAPI(title="Solstice Pilates Receptionist", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")
