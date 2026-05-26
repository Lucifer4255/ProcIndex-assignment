# Solstice Pilates — AI Receptionist

AI text and voice receptionist for Solstice Pilates. Phase 1: chat UI. Phase 2: Vapi voice.

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

# Run API (after Phase 1 implementation)
uvicorn api.main:app --reload --port 8000
```

See `architecture.md`, `context.md`, and `execution.md` for full design and build order.

## Environment variables

| Variable | Description |
|----------|-------------|
| `LLM_API_KEY` | OpenRouter API key |
| `LLM_MODEL` | OpenRouter model ID for Phase 1 agent |
| `VAPI_LLM_PROVIDER` | Vapi LLM provider, usually `openrouter` |
| `VAPI_LLM_MODEL` | Vapi model ID for Phase 2 voice |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Path to GCP service account JSON |
| `GOOGLE_CALENDAR_ID` | Studio calendar ID |
| `GOOGLE_SHEET_ID` | Contacts sheet ID |
| `REDIS_URL` | Redis connection URL |
| `VAPI_API_KEY` | Vapi API key (Phase 2) |
| `VAPI_PHONE_NUMBER_ID` | Vapi phone number (Phase 2) |
| `LOGFIRE_TOKEN` | Logfire observability token |
| `PORT` | Server port (default 8000) |
| `BASE_URL` | Public URL for webhooks (ngrok in dev) |
