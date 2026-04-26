# AI-Powered Business Assistant using RAG and Agentic Workflows

Full-stack starter project for a business assistant that answers questions from uploaded PDFs, keeps conversation memory, and calls simple tools such as booking appointments or searching product info.

This version uses Ollama locally, so chat works without an OpenAI API key or billing.

## Features

- FastAPI backend with `/chat`, `/upload`, `/memory/{user_id}`, `/appointments/{user_id}`, and `/products`
- Local Ollama generation through `http://localhost:11434/api/generate`
- PDF ingestion with text extraction, chunking, local hash embeddings, and FAISS vector search
- SQLite memory for chat history, appointments, and sample business product data
- Local action workflow for `book_appointment()` and `get_product_info()`
- Static HTML, CSS, and JavaScript frontend

## Project Structure

```text
.
|-- backend/
|   |-- __init__.py
|   |-- main.py
|   |-- rag.py
|   |-- models.py
|   |-- database.py
|   |-- requirements.txt
|   |-- .env
|   `-- .env.example
|-- frontend/
|   |-- index.html
|   |-- script.js
|   `-- style.css
`-- README.md
```

## Ollama Setup

Install Ollama, then download and run the local model:

```powershell
ollama run llama3
```

Keep Ollama running in the background. Its local API should be available at:

```text
http://localhost:11434
```

## Backend Setup

```powershell
cd c:\Users\DIXIT\Desktop\ai-bot-system
python -m venv backend\venv
backend\venv\Scripts\activate
pip install -r backend\requirements.txt
```

The important local AI settings are in `backend/.env`:

```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3
OLLAMA_TIMEOUT_SECONDS=300
LOCAL_EMBEDDING_DIM=512
```

Run the API from the project root:

```powershell
uvicorn backend.main:app --reload --port 8001
```

The API docs will be available at `http://127.0.0.1:8001/docs`.

## Frontend Setup

Serve the frontend as static files:

```powershell
cd frontend
python -m http.server 5500
```

Open `http://127.0.0.1:5500` in your browser.

## One-Command Start/Stop

From the project root, start both backend and frontend:

```powershell
.\start-local.ps1
```

Stop both services:

```powershell
.\stop-local.ps1
```

## Test Chat

In `/docs`, call `POST /chat` with:

```json
{
  "user_id": "demo-user",
  "message": "hello"
}
```

The response includes both `answer` and `reply` for compatibility.

## How It Works

1. `/upload` saves the PDF, extracts text with `pypdf`, splits text into chunks, creates local hash embeddings, and stores vectors in FAISS.
2. `/chat` embeds the user question locally, retrieves similar chunks, adds recent SQLite chat memory, and sends the final prompt to Ollama.
3. Local action detection can book appointments or search product info before the final Ollama answer is generated.
4. Each user and assistant message is saved in SQLite under the provided `user_id`.
