from pathlib import Path
import os
import re
import shutil
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from database import MemoryStore
from models import ChatRequest, ChatResponse, UploadResponse
from rag import RAGPipeline


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"

DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
load_dotenv(BASE_DIR / ".env")


def _cors_origins() -> list[str]:
    raw = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5500,http://127.0.0.1:5500,http://localhost:5173",
    )
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app = FastAPI(
    title="AI-Powered Business Assistant",
    description="FastAPI backend for PDF RAG, chat memory, and tool-using workflows.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

memory_store = MemoryStore(DATA_DIR / "assistant.sqlite3")
rag_pipeline = RAGPipeline(data_dir=DATA_DIR, memory_store=memory_store)


def _safe_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", filename.strip()).strip(".-")
    return cleaned or "uploaded-document.pdf"


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "AI-Powered Business Assistant",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/upload", response_model=UploadResponse)
async def upload_pdf(
    file: UploadFile = File(...),
    user_id: str = Form(default="demo-user"),
) -> UploadResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    safe_name = _safe_filename(file.filename)
    stored_name = f"{uuid.uuid4().hex}-{safe_name}"
    stored_path = UPLOAD_DIR / stored_name

    try:
        with stored_path.open("wb") as output:
            shutil.copyfileobj(file.file, output)

        result = rag_pipeline.ingest_pdf(stored_path, original_filename=safe_name)
        memory_store.add_message(
            user_id=user_id,
            role="system",
            content=f"Uploaded and indexed PDF: {safe_name}",
            metadata={"document_id": result["document_id"]},
        )
    except RuntimeError as exc:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc
    finally:
        await file.close()

    return UploadResponse(
        document_id=result["document_id"],
        filename=safe_name,
        chunks_indexed=result["chunks_indexed"],
        message=f"Indexed {result['chunks_indexed']} chunks from {safe_name}.",
    )


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    try:
        result = rag_pipeline.chat(user_id=request.user_id, user_message=request.message)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chat failed: {exc}") from exc

    return ChatResponse(**result)


@app.get("/memory/{user_id}")
def get_memory(user_id: str, limit: int = Query(default=20, ge=1, le=100)) -> dict:
    return {"user_id": user_id, "messages": memory_store.get_history(user_id, limit)}


@app.get("/appointments/{user_id}")
def get_appointments(user_id: str) -> dict:
    return {"user_id": user_id, "appointments": memory_store.list_appointments(user_id)}


@app.get("/products")
def products(q: str = "", limit: int = Query(default=5, ge=1, le=20)) -> dict:
    return {"products": memory_store.search_products(q, limit=limit)}
