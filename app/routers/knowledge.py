"""
Knowledge Base API — upload, list, delete documents, and test search.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, Depends

from app.core.logger import get_logger
from app.core.auth import Principal, get_current_principal
from app.services.knowledge import (
    delete_document,
    list_documents,
    get_document,
    search_knowledge,
    upload_document,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/knowledge", tags=["Knowledge Base"])

# Max upload size (10 MB)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv"}

# Placeholder tenant for single-tenant mode — replaced by auth principal later.
DEFAULT_TENANT = os.getenv("DEFAULT_TENANT_ID", "00000000-0000-0000-0000-000000000000")


def _tenant_id() -> str:
    """Resolve the current tenant. Will be swapped for auth dependency."""
    return DEFAULT_TENANT


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@router.post("/upload", summary="Upload a knowledge document")
async def upload_knowledge_doc(
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
    tenant_id: str = Form(None),
):
    """
    Upload a PDF, DOCX, TXT, or MD file.
    The document is stored and queued for async chunking + embedding.
    """
    # Resolve tenant from authenticated principal (ignore form override in production)
    tid = (tenant_id or principal.tenant_id)

    # Validate extension
    filename = file.filename or "unknown.txt"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Read file
    file_bytes = await file.read()
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        doc = await upload_document(
            tenant_id=tid,
            filename=filename,
            file_bytes=file_bytes,
            content_type=file.content_type or "application/octet-stream",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Trigger async processing via Celery
    try:
        from app.workers.tasks import process_knowledge_document
        process_knowledge_document.delay(doc["id"], tid)
    except Exception as exc:
        logger.warning("Celery dispatch failed — processing inline: %s", exc)
        # Fallback: process synchronously (dev mode)
        from app.services.knowledge import process_document
        import asyncio
        asyncio.create_task(process_document(doc["id"], tid))

    return {"status": "uploaded", "document": doc}


# ---------------------------------------------------------------------------
# List documents
# ---------------------------------------------------------------------------

@router.get("/documents", summary="List knowledge documents")
async def list_knowledge_docs(principal: Principal = Depends(get_current_principal)):
    docs = await list_documents(principal.tenant_id)
    return {"documents": docs}


# ---------------------------------------------------------------------------
# Get document status
# ---------------------------------------------------------------------------

@router.get("/documents/{doc_id}", summary="Get document details")
async def get_knowledge_doc(doc_id: str, principal: Principal = Depends(get_current_principal)):
    doc = await get_document(doc_id, principal.tenant_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"document": doc}


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@router.delete("/documents/{doc_id}", summary="Delete a document and its chunks")
async def delete_knowledge_doc(doc_id: str, principal: Principal = Depends(get_current_principal)):
    deleted = await delete_document(doc_id, principal.tenant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found or already deleted")
    return {"status": "deleted", "id": doc_id}


# ---------------------------------------------------------------------------
# Search (admin/debug)
# ---------------------------------------------------------------------------

@router.post("/search", summary="Search the knowledge base (debug)")
async def search_knowledge_docs(
    query: str = Form(...),
    top_k: int = Form(5),
    principal: Principal = Depends(get_current_principal),
):
    results = await search_knowledge(principal.tenant_id, query, top_k=top_k)
    return {"query": query, "results": results}
