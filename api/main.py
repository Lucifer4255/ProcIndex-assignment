"""FastAPI app — lifespan, CORS, Logfire — implemented in Phase 1."""

from fastapi import FastAPI

app = FastAPI(title="Solstice Pilates Receptionist")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
