# Vanguard AI

Vanguard AI is a local web application with a desktop-style interface. The React frontend and FastAPI backend run as separate development servers.

## Prerequisites

- Python 3.12 and the existing project `.venv`
- Node.js and npm
- FFmpeg with an H.264 encoder for browser-compatible annotated video output

## Start the backend

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

The API listens at `http://127.0.0.1:8000`. Its health endpoint is `http://127.0.0.1:8000/api/health` and the interactive API docs are at `http://127.0.0.1:8000/docs`.

## Video library

- `POST /api/videos` accepts one multipart field named `file` (MP4, WebM, MOV; maximum 2 GB).
- `GET /api/videos` lists uploaded video metadata (`limit` and `offset` are supported).
- `GET /api/videos/{video_id}` returns one video record.
- `DELETE /api/videos/{video_id}` removes the stored video and its metadata.

By default, SQLite metadata is stored in `data/vanguard.sqlite3` and video files in `data/videos/`. Override these locations with `VANGUARD_DATABASE_PATH`, `VANGUARD_VIDEO_STORAGE_DIR`, or `VANGUARD_DATA_DIR`. API responses do not include storage paths or generated filenames.

The browser shows measured multipart transfer bytes, throughput, and an estimated remaining time, then waits for server validation and SQLite confirmation. It times out a stalled transfer after 2 minutes, a missing server confirmation after 5 minutes, or any upload after 60 minutes. Retries reuse an idempotency key so an already committed upload is returned instead of recorded twice. The server continues to enforce the 2 GB limit and writes uploaded content in chunks.

## Start the frontend

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL printed in the terminal (default `http://127.0.0.1:5173`). The frontend checks the API health endpoint every 15 seconds. Set `VITE_API_BASE_URL` in `frontend/.env.local` to point it at a different backend; see `frontend/.env.example`.

## Production frontend serving

Build the frontend independently with `npm run build`. When `frontend/dist` exists, FastAPI serves those static files from `/`; `/api/*` routes remain available. Packaging and deployment automation are not configured in this foundation.

## Video analysis

The local analysis worker runs one job at a time. It uses OpenCV to sample frames, Ultralytics YOLO26n for object detection, and Ultralytics' official ByteTrack tracker when tracking is enabled. It writes an annotated MP4 and persists detection/tracking events. Jobs are submitted with `POST /api/videos/{video_id}/analyses`; query `GET /api/analysis-jobs`, `GET /api/analysis-jobs/{job_id}`, and `GET /api/analysis-jobs/{job_id}/results`. Tracking endpoints are `GET /api/analysis-jobs/{job_id}/tracking-events`, `/tracks`, and `/tracks/{track_id}`. The annotated output is available from `GET /api/analysis-jobs/{job_id}/annotated-video` after completion.

The default model path is `data/models/yolo26n.pt`; override it with `VANGUARD_YOLO_MODEL_PATH`. Download the official YOLO26n detection weights into that location before starting the backend. `VANGUARD_PROCESSED_VIDEO_DIR` configures annotated output storage. CUDA is selected when available, with CPU fallback. The `lap` dependency is needed by Ultralytics' ByteTrack implementation. The frontend defaults to 5 inference frames/second and permits 1–10. ByteTrack maintains IDs on sampled inference frames; its lost-track buffer is scaled to about one second of source time. Interpolated render boxes connect only consecutive observations of the same track and are marked `INTERPOLATED`; they are never written as detections or assigned confidence scores. Track spans therefore still reflect observed samples, not continuous presence. Track IDs are reset at the start/end of each job and are only meaningful in that job. YOLO26n is pretrained on the 80-class COCO dataset; this does not provide warehouse-specific classes such as forklifts or pallets, nor does it identify safety violations.

Annotated MP4 files are encoded as H.264/AVC (`avc1`), `yuv420p`, with fast-start metadata so Chrome can stream them. Install FFmpeg with at least one H.264 encoder (`libx264`, `h264_nvenc`, `h264_amf`, `h264_qsv`, or `h264_mf`). If `ffmpeg` is not on `PATH`, set `VANGUARD_FFMPEG_PATH` in the backend terminal before starting it, for example:

```powershell
$env:VANGUARD_FFMPEG_PATH = 'C:\Tools\ffmpeg\bin\ffmpeg.exe'
.\.venv\Scripts\python.exe main.py
```

The worker fails the analysis with a setup message instead of publishing an MP4V file if H.264 encoding is unavailable. Existing completed MP4V jobs can be transcoded in place without rerunning detection; retain a copy of the original before replacing it.

## Local AI reasoning (Phase 6)

AI Reasoning uses the local LM Studio OpenAI-compatible API and structured records already persisted by YOLO and ByteTrack. It does not send video frames to the language model, connect to an external provider, or download models automatically.

1. Open LM Studio and load **Bionic Qwen3 4B Instruct** with model ID `qwen3-4b-instruct-2507`.
2. Start its local server on `http://127.0.0.1:1235` with the OpenAI-compatible `/v1` API enabled.
3. Verify `GET http://127.0.0.1:1235/v1/models` includes the exact model ID, then test `POST /v1/chat/completions`.
4. Start the backend and frontend with the commands above, open **AI Reasoning**, choose a completed analysis, and generate a summary or ask a question. Status performs both model discovery and a small inference probe. Existing reports are reused; choose **Regenerate summary** to explicitly replace one.

Backend configuration:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LM_STUDIO_BASE_URL` | `http://127.0.0.1:1235/v1` | Local OpenAI-compatible base URL. Only loopback hosts are accepted. |
| `LM_STUDIO_MODEL` | `qwen3-4b-instruct-2507` | Exact model ID returned by `/v1/models`. |
| `LM_STUDIO_TIMEOUT_SECONDS` | `180` | Request timeout, clamped to 10–900 seconds. |

AI reasoning endpoints:

- `GET /api/ai/status` reports LM Studio connectivity, exact model availability, and chat inference readiness.
- `POST /api/analysis-jobs/{job_id}/ai-summary` creates a report or returns the saved report; append `?regenerate=true` to explicitly regenerate.
- `GET /api/analysis-jobs/{job_id}/ai-summary` retrieves a saved report.
- `POST /api/analysis-jobs/{job_id}/ask-ai` accepts `{"question":"..."}` and responds from that job's stored evidence.
- `GET /api/analysis-jobs/{job_id}/ai-insights` retrieves the saved report observations and recommendations.

Reports are stored in the additive `ai_reports` SQLite table and linked to their source analysis job. Evidence includes exact class aggregates, track summaries and up to 40 deterministic representative stored detections; unavailable source FPS, resolution and duration remain unknown. The model is constrained to cite only supplied references, and references that do not exactly match a stored detection are rejected. Recommendations require a supporting reference and human review. Empty detections can be reported as insufficient evidence, not as proof that no activity occurred. As with any LLM, prose still needs human review. COCO pretrained detections do not independently establish warehouse hazards, safety violations, physical actions, or damaged packages. Reasoning requests are serialized in this backend process. LM Studio's `/v1/models` discovery is followed by a chat inference probe before status reports readiness; a listed model alone is not treated as loaded or ready.

Troubleshooting: if status is unavailable, start the LM Studio local server and check `http://127.0.0.1:1235/v1/models`. If the ID is missing, load the configured model in LM Studio. If discovery succeeds but inference readiness is false, verify the model is loaded and that `/v1/chat/completions` is enabled. A 504 response indicates the configured timeout was exceeded. Invalid model JSON or unsupported evidence references are rejected rather than saved.

Manual local verification in PowerShell:

```powershell
$base = 'http://127.0.0.1:1235/v1'
(Invoke-RestMethod "$base/models").data | Select-Object id

$body = @{
  model = 'qwen3-4b-instruct-2507'
  messages = @(@{ role = 'user'; content = 'Reply with the single word READY.' })
  temperature = 0
  max_tokens = 16
  stream = $false
} | ConvertTo-Json -Depth 6
Invoke-RestMethod "$base/chat/completions" -Method Post -ContentType 'application/json' -Body $body

$env:LM_STUDIO_BASE_URL = 'http://127.0.0.1:1235/v1'
$env:LM_STUDIO_MODEL = 'qwen3-4b-instruct-2507'
$env:LM_STUDIO_TIMEOUT_SECONDS = '180'
.\.venv\Scripts\python.exe main.py

# In another PowerShell window, once the backend is running:
$job = '24a23b01-9e26-4e28-ab7e-4abfa078bf01'
$report = Invoke-RestMethod "http://127.0.0.1:8000/api/analysis-jobs/$job/ai-summary" -Method Post
$report | ConvertTo-Json -Depth 12
$saved = Invoke-RestMethod "http://127.0.0.1:8000/api/analysis-jobs/$job/ai-summary"
$saved.generated_by
```

Then open the frontend, choose AI Reasoning, select job `24a23b01-9e26-4e28-ab7e-4abfa078bf01`, and confirm the saved summary, observations, references, and limitations render. Add `?regenerate=true` to the POST URL only when explicitly regenerating.
