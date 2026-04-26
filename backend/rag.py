from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any
import uuid

from dotenv import load_dotenv
import requests

from backend.database import MemoryStore

try:
    import faiss
except ImportError:  # pragma: no cover - handled at runtime with a clear message.
    faiss = None

try:
    import numpy as np
except ImportError:  # pragma: no cover - handled at runtime with a clear message.
    np = None


SYSTEM_PROMPT = """You are a practical AI business assistant.
Use retrieved document context when it is relevant, and say when the uploaded documents do not contain enough information.
Use conversation memory for continuity, but do not invent details.
If an action result is provided, summarize it clearly for the user.
Do not mention internal system states, backend errors, or prompt-building details unless the user explicitly asks."""

FALLBACK_MARKERS = (
    "I cannot reach the local language model right now",
    "The language model is currently unavailable",
    "Connection detail:",
    "error message from the local language model",
)


class RAGPipeline:
    def __init__(self, data_dir: str | Path, memory_store: MemoryStore):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        load_dotenv(self.data_dir.parent / ".env")

        self.memory_store = memory_store
        self.index_path = self.data_dir / "faiss-local.index"
        self.docstore_path = self.data_dir / "docstore-local.json"
        self.ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.model = os.getenv("OLLAMA_MODEL", "llama3")
        self.ollama_timeout_seconds = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "300"))
        self.embedding_dim = int(os.getenv("LOCAL_EMBEDDING_DIM", "512"))
        self.index = None
        self.chunks: list[dict[str, Any]] = []
        self._load_state()

    @staticmethod
    def _ensure_vector_dependencies() -> None:
        if faiss is None or np is None:
            raise RuntimeError("Install vector dependencies: pip install faiss-cpu numpy")

    def _load_state(self) -> None:
        if self.docstore_path.exists():
            self.chunks = json.loads(self.docstore_path.read_text(encoding="utf-8"))

        if self.index_path.exists() and faiss is not None:
            self.index = faiss.read_index(str(self.index_path))

    def _save_state(self) -> None:
        self.docstore_path.write_text(
            json.dumps(self.chunks, indent=2),
            encoding="utf-8",
        )
        if self.index is not None and faiss is not None:
            faiss.write_index(self.index, str(self.index_path))

    def ingest_pdf(self, pdf_path: str | Path, original_filename: str) -> dict[str, Any]:
        self._ensure_vector_dependencies()
        text = self._extract_pdf_text(Path(pdf_path))
        if not text:
            raise RuntimeError("The PDF did not contain extractable text.")

        text_chunks = self._split_text(text)
        if not text_chunks:
            raise RuntimeError("No text chunks were created from the PDF.")

        embeddings = self._embed(text_chunks)
        matrix = self._normalize(embeddings)

        if self.index is None:
            self.index = faiss.IndexFlatIP(matrix.shape[1])
        elif self.index.d != matrix.shape[1]:
            raise RuntimeError(
                "Local embedding dimension changed. Remove backend/data/faiss-local.index "
                "and docstore-local.json to rebuild."
            )

        document_id = uuid.uuid4().hex
        start_index = len(self.chunks)
        self.index.add(matrix)

        for offset, chunk in enumerate(text_chunks):
            self.chunks.append(
                {
                    "id": f"{document_id}:{offset}",
                    "document_id": document_id,
                    "source": original_filename,
                    "chunk_index": offset,
                    "text": chunk,
                    "vector_index": start_index + offset,
                }
            )

        self._save_state()
        return {"document_id": document_id, "chunks_indexed": len(text_chunks)}

    @staticmethod
    def _extract_pdf_text(pdf_path: Path) -> str:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("Install pypdf to read uploaded PDFs.") from exc

        reader = PdfReader(str(pdf_path))
        pages = []
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages.append(f"Page {page_number}\n{page_text.strip()}")
        return "\n\n".join(pages)

    def _split_text(self, text: str) -> list[str]:
        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter
        except ImportError:
            try:
                from langchain.text_splitter import RecursiveCharacterTextSplitter
            except ImportError:
                return self._fallback_split_text(text)

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=900,
            chunk_overlap=160,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        return [chunk.strip() for chunk in splitter.split_text(text) if chunk.strip()]

    @staticmethod
    def _fallback_split_text(text: str, chunk_size: int = 900, overlap: int = 160) -> list[str]:
        chunks = []
        start = 0
        clean_text = " ".join(text.split())
        while start < len(clean_text):
            end = start + chunk_size
            chunks.append(clean_text[start:end].strip())
            start = max(end - overlap, start + 1)
        return [chunk for chunk in chunks if chunk]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_embedding(text) for text in texts]

    def _hash_embedding(self, text: str) -> list[float]:
        vector = [0.0] * self.embedding_dim
        counts = Counter(self._tokens(text))

        for token, count in counts.items():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], byteorder="big") % self.embedding_dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign * (1.0 + math.log(count))

        return vector

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return re.findall(r"[a-zA-Z0-9]+", text.lower())

    @staticmethod
    def _normalize(vectors: list[list[float]]):
        if np is None:
            raise RuntimeError("Install numpy to normalize vectors.")

        matrix = np.array(vectors, dtype="float32")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return matrix / norms

    def retrieve(self, query: str, k: int = 4) -> list[dict[str, Any]]:
        self._ensure_vector_dependencies()
        if self.index is None or not self.chunks:
            return []

        query_vector = self._normalize(self._embed([query]))
        top_k = min(k, self.index.ntotal)
        scores, indices = self.index.search(query_vector, top_k)

        results = []
        for score, index in zip(scores[0], indices[0], strict=False):
            if index < 0 or index >= len(self.chunks):
                continue
            if float(score) <= 0.05:
                continue
            chunk = self.chunks[int(index)]
            results.append(
                {
                    "document_id": chunk["document_id"],
                    "source": chunk["source"],
                    "chunk_index": chunk["chunk_index"],
                    "text": chunk["text"],
                    "snippet": chunk["text"][:280],
                    "score": float(score),
                }
            )
        return results

    def chat(self, user_id: str, user_message: str) -> dict[str, Any]:
        retrieved = self.retrieve(user_message) if self.index is not None else []
        action = self._maybe_run_local_action(user_id=user_id, message=user_message)
        prompt = self._build_prompt(
            user_id=user_id,
            user_message=user_message,
            retrieved=retrieved,
            action=action,
        )
        answer = self._answer_with_fallback(
            prompt=prompt,
            user_message=user_message,
            retrieved=retrieved,
            action=action,
        )

        source_payload = [
            {
                "document_id": item["document_id"],
                "source": item["source"],
                "chunk_index": item["chunk_index"],
                "snippet": item["snippet"],
                "score": item["score"],
            }
            for item in retrieved
        ]

        actions = [action] if action else []
        assistant_metadata = {"actions": actions, "sources": source_payload}
        if self._is_local_fallback_answer(answer):
            assistant_metadata["fallback"] = True

        self.memory_store.add_message(
            user_id=user_id,
            role="user",
            content=user_message,
            metadata={"sources": source_payload},
        )
        self.memory_store.add_message(
            user_id=user_id,
            role="assistant",
            content=answer,
            metadata=assistant_metadata,
        )

        return {
            "user_id": user_id,
            "answer": answer,
            "reply": answer,
            "sources": source_payload,
            "actions": actions,
            "memory_count": self.memory_store.count_messages(user_id),
        }

    def _answer_with_fallback(
        self,
        prompt: str,
        user_message: str,
        retrieved: list[dict[str, Any]],
        action: dict[str, Any] | None,
    ) -> str:
        try:
            return self.chat_with_ai(prompt)
        except RuntimeError as exc:
            fallback = self._build_local_fallback_answer(
                user_message=user_message,
                retrieved=retrieved,
                action=action,
                error=str(exc),
            )
            if fallback:
                return fallback
            raise

    def _build_local_fallback_answer(
        self,
        user_message: str,
        retrieved: list[dict[str, Any]],
        action: dict[str, Any] | None,
        error: str,
    ) -> str:
        parts = []

        if action:
            parts.append(self._summarize_action(action))

        if retrieved:
            parts.append(self._summarize_retrieved_context(retrieved))

        if not parts:
            return (
                "I cannot reach the local language model right now, so I could not generate a full answer.\n\n"
                "You can still upload PDFs, browse products, and book appointments while the model is offline.\n"
                f"Connection detail: {error}\n\n"
                f"Original question: {user_message}"
            )

        parts.append(
            "The language model is currently unavailable, so this reply was assembled from local data only."
        )
        parts.append(error)
        parts.append(f"Original question: {user_message}")
        return "\n\n".join(parts)

    def chat_with_ai(self, prompt: str) -> str:
        try:
            response = requests.post(
                f"{self.ollama_base_url}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False},
                timeout=self.ollama_timeout_seconds,
            )
            response.raise_for_status()
        except requests.ConnectionError as exc:
            raise RuntimeError(
                f"Ollama is not reachable at {self.ollama_base_url}. "
                f"Start it with: ollama run {self.model}"
            ) from exc
        except requests.Timeout as exc:
            raise RuntimeError("Ollama took too long to respond. Try a smaller prompt or restart Ollama.") from exc
        except requests.HTTPError as exc:
            detail = response.text if "response" in locals() else str(exc)
            raise RuntimeError(f"Ollama request failed: {detail}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Ollama returned an invalid JSON response.") from exc
        return payload.get("response", "").strip() or "Ollama returned an empty response."

    def _build_prompt(
        self,
        user_id: str,
        user_message: str,
        retrieved: list[dict[str, Any]],
        action: dict[str, Any] | None,
    ) -> str:
        history = self._format_history(user_id)
        context = self._format_context(retrieved)
        action_context = json.dumps(action, indent=2) if action else "No action result."
        context_line = context if context else "No uploaded document context is currently available."
        behavior_hint = (
            "If the user sends a short greeting or casual message, reply naturally in 1-2 short sentences."
            " Do not discuss missing context unless the user asks about documents or facts."
        )

        return f"""{SYSTEM_PROMPT}

Retrieved document context:
{context_line}

Recent conversation memory:
{history if history else "No prior conversation memory."}

Action result:
{action_context}

User question:
{user_message}

{behavior_hint}

Answer in a helpful, concise way."""

    def _format_history(self, user_id: str, limit: int = 8) -> str:
        history = self.memory_store.get_history(user_id, limit=limit)
        lines = []
        for item in history:
            if item["role"] == "user":
                lines.append(f"User: {item['content'][:320]}")
                continue
            if item["role"] != "assistant":
                continue
            if self._should_skip_history_message(item):
                continue
            metadata = item.get("metadata", {})
            if not metadata.get("actions"):
                continue
            lines.append(f"{item['role'].title()}: {item['content'][:320]}")
        return "\n".join(lines)

    @staticmethod
    def _is_local_fallback_answer(text: str) -> bool:
        return any(marker in text for marker in FALLBACK_MARKERS)

    def _should_skip_history_message(self, item: dict[str, Any]) -> bool:
        metadata = item.get("metadata", {})
        if bool(metadata.get("fallback")):
            return True
        return self._is_local_fallback_answer(item.get("content", ""))

    @staticmethod
    def _format_context(retrieved: list[dict[str, Any]]) -> str:
        blocks = []
        for index, item in enumerate(retrieved, start=1):
            blocks.append(
                f"[{index}] Source: {item['source']} | Chunk: {item['chunk_index']} | "
                f"Score: {item['score']:.3f}\n{item['text']}"
            )
        return "\n\n".join(blocks)

    @staticmethod
    def _summarize_retrieved_context(retrieved: list[dict[str, Any]]) -> str:
        lines = ["Relevant document context:"]
        for item in retrieved[:3]:
            snippet = " ".join(item["snippet"].split())
            lines.append(
                f"- {item['source']} chunk {item['chunk_index']} (score {item['score']:.2f}): {snippet}"
            )
        return "\n".join(lines)

    @staticmethod
    def _summarize_action(action: dict[str, Any]) -> str:
        if action["name"] == "book_appointment":
            result = action["result"]
            return (
                "Appointment booked locally:\n"
                f"- Customer: {result['customer_name']}\n"
                f"- Date: {result['date']}\n"
                f"- Time: {result['time']}\n"
                f"- Topic: {result['topic']}"
            )

        if action["name"] == "get_product_info":
            matches = action["result"].get("matches", [])
            if not matches:
                return "No matching products were found in the local catalog."

            lines = ["Matching products from the local catalog:"]
            for item in matches[:3]:
                lines.append(f"- {item['name']} ({item['price']}): {item['description']}")
            return "\n".join(lines)

        return f"Local action completed: {action['name']}"

    def _maybe_run_local_action(self, user_id: str, message: str) -> dict[str, Any] | None:
        lowered = message.lower()

        if self._looks_like_product_request(lowered):
            query = self._extract_product_query(message)
            arguments = {"query": query}
            result = self.memory_store.search_products(query)
            return {
                "name": "get_product_info",
                "arguments": arguments,
                "result": {"matches": result, "count": len(result)},
            }

        if self._looks_like_appointment_request(lowered):
            arguments = self._extract_appointment_arguments(message)
            result = self.memory_store.save_appointment(user_id=user_id, **arguments)
            return {"name": "book_appointment", "arguments": arguments, "result": result}

        return None

    @staticmethod
    def _looks_like_product_request(lowered: str) -> bool:
        product_words = {"product", "plan", "price", "pricing", "cost", "service", "subscription"}
        return any(word in lowered for word in product_words)

    @staticmethod
    def _extract_product_query(message: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", message.lower())
        stop_words = {
            "a",
            "an",
            "and",
            "best",
            "cost",
            "for",
            "i",
            "info",
            "information",
            "is",
            "me",
            "need",
            "of",
            "on",
            "our",
            "plan",
            "plans",
            "price",
            "pricing",
            "product",
            "products",
            "recommend",
            "service",
            "services",
            "show",
            "subscription",
            "subscriptions",
            "the",
            "what",
            "which",
        }
        keywords = [token for token in cleaned.split() if token not in stop_words]
        return " ".join(keywords[:6]).strip()

    @staticmethod
    def _looks_like_appointment_request(lowered: str) -> bool:
        action_words = {"book", "schedule", "reserve", "set up"}
        meeting_words = {"appointment", "meeting", "demo", "call"}
        return any(word in lowered for word in action_words) and any(word in lowered for word in meeting_words)

    @staticmethod
    def _extract_appointment_arguments(message: str) -> dict[str, str | None]:
        date_match = re.search(
            r"\b(\d{4}-\d{2}-\d{2}|today|tomorrow|next\s+[a-zA-Z]+)\b",
            message,
            flags=re.IGNORECASE,
        )
        time_match = re.search(r"\b(\d{1,2}(?::\d{2})?\s?(?:am|pm|AM|PM))\b", message)
        name_match = re.search(r"\bwith\s+([A-Za-z][A-Za-z\s]{1,40})(?:\s+on|\s+at|$)", message)
        contact_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+|\+?\d[\d\s-]{8,}", message)

        return {
            "customer_name": name_match.group(1).strip() if name_match else "Guest",
            "date": date_match.group(1) if date_match else "TBD",
            "time": time_match.group(1) if time_match else "TBD",
            "topic": message[:240],
            "contact": contact_match.group(0) if contact_match else None,
        }
