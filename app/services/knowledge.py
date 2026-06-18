"""
Knowledge Base service — document upload, chunking, embedding, and RAG search.

Supports PDF, DOCX, and plain-text uploads.  Documents are split into
overlapping ~500-token chunks, embedded via Gemini text-embedding-004,
and stored in Postgres (pgvector) for cosine-similarity retrieval.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import textwrap
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from app.core.logger import get_logger
from app.db.supabase import get_supabase

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
EMBED_MODEL: str = os.getenv("EMBED_MODEL", "text-embedding-004")
EMBED_DIM: int = 768  # text-embedding-004 output dimension
EMBED_BATCH_SIZE: int = 20  # Gemini allows up to 100 texts; stay conservative

CHUNK_TARGET_TOKENS: int = 500
CHUNK_OVERLAP_TOKENS: int = 50
APPROX_CHARS_PER_TOKEN: float = 4.0  # rough English average

CHUNK_TARGET_CHARS: int = int(CHUNK_TARGET_TOKENS * APPROX_CHARS_PER_TOKEN)
CHUNK_OVERLAP_CHARS: int = int(CHUNK_OVERLAP_TOKENS * APPROX_CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------

def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text from a PDF using PyPDF2."""
    try:
        from PyPDF2 import PdfReader
        import io

        reader = PdfReader(io.BytesIO(file_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages).strip()
    except ImportError:
        logger.warning("PyPDF2 not installed — cannot extract PDF text")
        raise
    except Exception as exc:
        logger.error("PDF extraction failed: %s", exc)
        raise


def extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract text from a DOCX file using python-docx."""
    try:
        from docx import Document
        import io

        doc = Document(io.BytesIO(file_bytes))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs).strip()
    except ImportError:
        logger.warning("python-docx not installed — cannot extract DOCX text")
        raise
    except Exception as exc:
        logger.error("DOCX extraction failed: %s", exc)
        raise


def extract_text(filename: str, file_bytes: bytes) -> str:
    """Route to the correct extractor based on file extension."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_bytes)
    elif ext in (".docx",):
        return extract_text_from_docx(file_bytes)
    elif ext in (".txt", ".md", ".csv"):
        return file_bytes.decode("utf-8", errors="replace").strip()
    else:
        # Best-effort: treat as plain text
        return file_bytes.decode("utf-8", errors="replace").strip()


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str) -> list[dict[str, Any]]:
    """
    Split text into overlapping chunks of ~CHUNK_TARGET_CHARS characters.

    Splitting priority: paragraph → sentence → word boundaries.
    Returns list of dicts with 'content', 'chunk_index', 'token_count'.
    """
    if not text or not text.strip():
        return []

    # Normalise whitespace
    text = re.sub(r"\r\n", "\n", text)

    # Split into paragraphs first
    paragraphs = re.split(r"\n{2,}", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    chunks: list[str] = []
    current_chunk = ""

    for para in paragraphs:
        candidate = (current_chunk + "\n\n" + para).strip() if current_chunk else para
        if len(candidate) <= CHUNK_TARGET_CHARS:
            current_chunk = candidate
        else:
            if current_chunk:
                chunks.append(current_chunk)
            # If single paragraph is too long, split by sentences
            if len(para) > CHUNK_TARGET_CHARS:
                sentences = re.split(r"(?<=[.!?])\s+", para)
                current_chunk = ""
                for sent in sentences:
                    cand = (current_chunk + " " + sent).strip() if current_chunk else sent
                    if len(cand) <= CHUNK_TARGET_CHARS:
                        current_chunk = cand
                    else:
                        if current_chunk:
                            chunks.append(current_chunk)
                        # If a single sentence is still too long, force-split
                        if len(sent) > CHUNK_TARGET_CHARS:
                            words = sent.split()
                            current_chunk = ""
                            for word in words:
                                cand = (current_chunk + " " + word).strip() if current_chunk else word
                                if len(cand) <= CHUNK_TARGET_CHARS:
                                    current_chunk = cand
                                else:
                                    if current_chunk:
                                        chunks.append(current_chunk)
                                    current_chunk = word
                        else:
                            current_chunk = sent
            else:
                current_chunk = para

    if current_chunk:
        chunks.append(current_chunk)

    # Add overlap between consecutive chunks
    overlapped: list[str] = []
    for i, chunk in enumerate(chunks):
        if i > 0 and CHUNK_OVERLAP_CHARS > 0:
            prev = chunks[i - 1]
            overlap_text = prev[-CHUNK_OVERLAP_CHARS:]
            # Start from a word boundary
            space_idx = overlap_text.find(" ")
            if space_idx > 0:
                overlap_text = overlap_text[space_idx + 1:]
            chunk = overlap_text + " " + chunk
        overlapped.append(chunk.strip())

    return [
        {
            "content": c,
            "chunk_index": i,
            "token_count": max(1, len(c) // int(APPROX_CHARS_PER_TOKEN)),
        }
        for i, c in enumerate(overlapped)
        if c.strip()
    ]


# ---------------------------------------------------------------------------
# Embedding via Gemini
# ---------------------------------------------------------------------------

async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Call Gemini's embedding API for a batch of texts.
    Returns a list of embedding vectors (each 768-dim).
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set — cannot generate embeddings")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{EMBED_MODEL}"
        f":batchEmbedContents?key={GEMINI_API_KEY}"
    )

    all_embeddings: list[list[float]] = []

    for batch_start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[batch_start : batch_start + EMBED_BATCH_SIZE]
        payload = {
            "requests": [
                {
                    "model": f"models/{EMBED_MODEL}",
                    "content": {"parts": [{"text": t}]},
                    "taskType": "RETRIEVAL_DOCUMENT",
                }
                for t in batch
            ]
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        for emb in data.get("embeddings", []):
            all_embeddings.append(emb["values"])

    return all_embeddings


async def embed_query(text: str) -> list[float]:
    """Embed a single query text (uses RETRIEVAL_QUERY task type)."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set — cannot generate embeddings")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{EMBED_MODEL}"
        f":embedContent?key={GEMINI_API_KEY}"
    )

    payload = {
        "model": f"models/{EMBED_MODEL}",
        "content": {"parts": [{"text": text}]},
        "taskType": "RETRIEVAL_QUERY",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

    return data["embedding"]["values"]


# ---------------------------------------------------------------------------
# Document lifecycle
# ---------------------------------------------------------------------------

async def upload_document(
    tenant_id: str,
    filename: str,
    file_bytes: bytes,
    content_type: str = "text/plain",
    uploaded_by: str | None = None,
) -> dict[str, Any]:
    """
    Upload a document: extract text, save record, return doc metadata.
    Chunking + embedding happen asynchronously via Celery.
    """
    raw_text = extract_text(filename, file_bytes)
    if not raw_text.strip():
        raise ValueError(f"No text could be extracted from '{filename}'")

    db = get_supabase()
    record = {
        "tenant_id": tenant_id,
        "filename": filename,
        "content_type": content_type,
        "raw_text": raw_text,
        "char_count": len(raw_text),
        "status": "processing",
    }
    if uploaded_by:
        record["uploaded_by"] = uploaded_by

    def _insert():
        return db.table("knowledge_documents").insert(record).execute()

    result = await asyncio.to_thread(_insert)
    doc = result.data[0] if result.data else record
    logger.info("Knowledge doc uploaded: %s (tenant=%s)", filename, tenant_id)
    return doc


async def process_document(doc_id: str, tenant_id: str) -> None:
    """
    Full pipeline: chunk → embed → store.
    Called by the Celery task after upload.
    """
    db = get_supabase()

    # 1. Fetch raw text
    def _fetch():
        return (
            db.table("knowledge_documents")
            .select("raw_text, filename")
            .eq("id", doc_id)
            .eq("tenant_id", tenant_id)
            .single()
            .execute()
        )

    result = await asyncio.to_thread(_fetch)
    if not result.data:
        logger.error("Doc %s not found for tenant %s", doc_id, tenant_id)
        return

    raw_text = result.data["raw_text"]
    filename = result.data["filename"]

    # 2. Update status → chunking
    def _set_status(status: str, **extra):
        update = {"status": status, **extra}
        return (
            db.table("knowledge_documents")
            .update(update)
            .eq("id", doc_id)
            .execute()
        )

    await asyncio.to_thread(_set_status, "chunking")

    # 3. Chunk
    chunks = chunk_text(raw_text)
    if not chunks:
        await asyncio.to_thread(_set_status, "error", error_message="No chunks produced")
        return

    logger.info("Doc %s (%s): %d chunks", doc_id, filename, len(chunks))

    # 4. Insert chunk rows (without embeddings yet)
    chunk_rows = [
        {
            "document_id": doc_id,
            "tenant_id": tenant_id,
            "chunk_index": c["chunk_index"],
            "content": c["content"],
            "token_count": c["token_count"],
        }
        for c in chunks
    ]

    def _insert_chunks():
        return db.table("knowledge_chunks").insert(chunk_rows).execute()

    chunk_result = await asyncio.to_thread(_insert_chunks)
    saved_chunks = chunk_result.data or []

    # 5. Update status → embedding
    await asyncio.to_thread(_set_status, "embedding")

    # 6. Generate embeddings
    try:
        texts = [c["content"] for c in chunks]
        embeddings = await embed_texts(texts)
    except Exception as exc:
        logger.error("Embedding failed for doc %s: %s", doc_id, exc)
        await asyncio.to_thread(
            _set_status, "error", error_message=f"Embedding failed: {exc}"
        )
        return

    # 7. Update each chunk with its embedding
    for saved_chunk, emb in zip(saved_chunks, embeddings):
        chunk_id = saved_chunk["id"]

        def _update_emb(cid=chunk_id, embedding=emb):
            return (
                db.table("knowledge_chunks")
                .update({"embedding": embedding})
                .eq("id", cid)
                .execute()
            )

        await asyncio.to_thread(_update_emb)

    # 8. Mark ready
    await asyncio.to_thread(
        _set_status, "ready", chunk_count=len(chunks), error_message=None
    )
    logger.info("Doc %s (%s): ready with %d embedded chunks", doc_id, filename, len(chunks))


# ---------------------------------------------------------------------------
# Search (RAG retrieval)
# ---------------------------------------------------------------------------

async def search_knowledge(
    tenant_id: str,
    query: str,
    top_k: int = 5,
    min_score: float = 0.3,
) -> list[dict[str, Any]]:
    """
    Embed the query and retrieve the most relevant knowledge-base chunks.
    Returns list of dicts: {chunk_id, document_id, filename, content, similarity}.
    """
    try:
        query_emb = await embed_query(query)
    except Exception as exc:
        logger.error("Query embedding failed: %s", exc)
        return []

    db = get_supabase()

    def _search():
        return db.rpc(
            "search_knowledge_base",
            {
                "query_embedding": query_emb,
                "p_tenant_id": tenant_id,
                "p_top_k": top_k,
                "p_min_score": min_score,
            },
        ).execute()

    try:
        result = await asyncio.to_thread(_search)
        return result.data or []
    except Exception as exc:
        logger.error("Knowledge search RPC failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

async def list_documents(tenant_id: str) -> list[dict[str, Any]]:
    """List all knowledge documents for a tenant."""
    db = get_supabase()

    def _list():
        return (
            db.table("knowledge_documents")
            .select("id, filename, content_type, char_count, chunk_count, status, error_message, created_at")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .execute()
        )

    result = await asyncio.to_thread(_list)
    return result.data or []


async def get_document(doc_id: str, tenant_id: str) -> dict[str, Any] | None:
    """Fetch a single document by id, scoped to tenant."""
    db = get_supabase()

    def _get():
        return (
            db.table("knowledge_documents")
            .select("*")
            .eq("id", doc_id)
            .eq("tenant_id", tenant_id)
            .single()
            .execute()
        )

    try:
        result = await asyncio.to_thread(_get)
        return result.data
    except Exception:
        return None


async def delete_document(doc_id: str, tenant_id: str) -> bool:
    """Delete a document and its chunks (cascade). Returns True if deleted."""
    db = get_supabase()

    def _delete():
        return (
            db.table("knowledge_documents")
            .delete()
            .eq("id", doc_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )

    try:
        result = await asyncio.to_thread(_delete)
        deleted = bool(result.data)
        if deleted:
            logger.info("Deleted knowledge doc %s (tenant=%s)", doc_id, tenant_id)
        return deleted
    except Exception as exc:
        logger.error("Failed to delete doc %s: %s", doc_id, exc)
        return False
