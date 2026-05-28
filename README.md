# Solstice Pilates — AI Receptionist

AI text and voice receptionist for Solstice Pilates. Phase 1: chat UI. Phase 2: Vapi voice.

## Demo

| Phase | Description | Link |
|-------|-------------|------|
| Phase 1 | Text chat UI | [Watch](https://youtu.be/KdBo7BSylpE) |
| Phase 2 | Vapi voice calls | [Watch](https://youtu.be/Bcd68wVTY44) |
| Architecture | System design walkthrough | [Watch](https://www.loom.com/share/9a933e49e3174a98a0f218605014f405) |

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or venv
- Docker (for local Redis)
- Google Cloud service account with Calendar + Sheets APIs enabled
- OpenRouter API key for local agent development

## Quick start

```bash
# Install dependencies
uv sync

# Copy env template and fill in values
cp .env.example .env

# Start Redis
docker run -d --name solstice-redis -p 6379:6379 redis:alpine

# Run API
uvicorn api.main:app --reload --port 8000
```

See `architecture.md`, `context.md`, and `execution.md` for full design and build order.

## Environment variables

| Variable | Description |
|----------|-------------|
| `LLM_API_KEY` | LLM API key (OpenRouter recommended for dev) |
| `LLM_MODEL` | Model ID for Phase 1 PydanticAI agent |
| `VAPI_LLM_MODEL` | OpenAI model ID for Vapi voice (Phase 2) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Path to GCP service account JSON |
| `GOOGLE_CALENDAR_ID` | Studio calendar ID |
| `GOOGLE_SHEET_ID` | Contacts sheet ID |
| `REDIS_URL` | Redis connection URL |
| `VAPI_API_KEY` | Vapi API key (Phase 2) |
| `VAPI_ASSISTANT_ID` | Vapi assistant ID — set after running `scripts/vapi_setup.py` |
| `VAPI_PHONE_NUMBER_ID` | Vapi phone number ID (Phase 2) |
| `LOGFIRE_TOKEN` | Logfire observability token |
| `PORT` | Server port (default 8000) |
| `BASE_URL` | Public URL for webhooks (ngrok in dev) |
