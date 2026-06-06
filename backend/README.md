# LD Backend

FastAPI service for LD native AI chat, docs ingestion, and drawing guidance.

## Local LLM (Ollama)

- Set `LD_LLM_PROVIDER=ollama` and `LD_LLM_BASE_URL` to your local Ollama endpoint.
- Ensure the model in `LD_TEXT_MODEL` is pulled by Ollama.

## SiliconFlow (siflow)

- Set `LD_LLM_PROVIDER=siliconflow` and provide `LD_LLM_API_KEY`.
- `LD_LLM_BASE_URL` should point to SiliconFlow (OpenAI-compatible).

## Run (dev)

1. Create venv and install deps (from LD root `requirements.txt`)
2. Start server

Example:
- `cd .. && python -m pip install -r requirements.txt`
- `uvicorn main:app --reload --port 8008`

## Endpoints

- `GET /api/ld/health`
- `POST /api/ld/chat` (multipart form: message, history (json), image)
- `GET /api/ld/docs`
- `POST /api/ld/docs/upload`
- `POST /api/ld/draw`
