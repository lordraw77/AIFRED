"""Admin API of the local knowledge base (spec 9.4)."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from aifred.gateways.api import AdminDep, Ok, ServicesDep
from aifred.rag import RagError

logger = logging.getLogger(__name__)

router = APIRouter()


def _refuse(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


def _require_enabled(services: Any) -> None:
    if not services.rag.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "the knowledge base is off: set RAG_ENABLED on the Config page")


class CollectionIn(BaseModel):
    """A collection to create or change."""

    name: str | None = None
    description: str | None = Field(default=None, max_length=500)
    embed_provider: str | None = None
    embed_model: str | None = None

    def fields(self) -> dict[str, Any]:
        """Only the fields the client sent."""
        return self.model_dump(exclude_unset=True, exclude={"name"})


class UrlIn(BaseModel):
    """A page or document to download."""

    collection: str
    url: str = Field(min_length=8, max_length=2000)


class SearchIn(BaseModel):
    """A test search."""

    query: str = Field(min_length=1, max_length=500)
    collection: str | None = None
    limit: int | None = Field(default=None, ge=1, le=10)
    mode: Literal["hybrid", "dense", "keyword"] = "hybrid"


class ScanIn(BaseModel):
    """Optional collection to rescan."""

    collection: str | None = None


class ReindexIn(BaseModel):
    """Optional collection to re-embed."""

    collection: str | None = None


@router.get("/api/rag/status")
def rag_status(_: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Totals, indexing queue and memory use."""
    return services.rag.status()


@router.get("/api/rag/collections")
def list_collections(_: AdminDep, services: ServicesDep) -> list[dict[str, Any]]:
    """Collections with counts, their effective embedding model and how many documents are stale."""
    return services.rag.collections()


@router.post("/api/rag/collections", status_code=status.HTTP_201_CREATED)
def create_collection(body: CollectionIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Create a collection."""
    try:
        return services.rag.create_collection(body.name or "", body.description or "", body.embed_provider, body.embed_model)
    except RagError as exc:
        raise _refuse(exc) from None


@router.put("/api/rag/collections/{name}")
def update_collection(name: str, body: CollectionIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Change the description or the embedding model of a collection."""
    try:
        return services.rag.update_collection(name, body.fields())
    except RagError as exc:
        raise _refuse(exc) from None


@router.delete("/api/rag/collections/{name}")
def delete_collection(name: str, _: AdminDep, services: ServicesDep) -> Ok:
    """Delete a collection with its documents."""
    try:
        services.rag.delete_collection(name)
    except RagError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return Ok()


@router.get("/api/rag/documents")
def list_documents(_: AdminDep, services: ServicesDep, collection: str | None = None, limit: Annotated[int, Query(ge=1, le=2000)] = 500) -> list[dict[str, Any]]:
    """Documents with state, size, chunks and the flagged badge."""
    try:
        return services.rag.documents(collection, limit)
    except RagError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None


@router.post("/api/rag/documents", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    request: Request, _: AdminDep, services: ServicesDep, collection: str, name: str = Query(min_length=1, max_length=200)
) -> dict[str, Any]:
    """Upload a document as the raw request body (no multipart); it is queued for indexing."""
    _require_enabled(services)
    limit = int(services.config.get("RAG_MAX_DOCUMENT_BYTES"))
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"the document is larger than {limit:,} bytes")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:  # checked while streaming: a missing or false Content-Length cannot get around it
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"the document is larger than {limit:,} bytes")
    try:
        return await asyncio.to_thread(services.rag.add_document, collection, name, bytes(data))
    except RagError as exc:
        raise _refuse(exc) from None


@router.post("/api/rag/documents/url", status_code=status.HTTP_202_ACCEPTED)
async def add_url(body: UrlIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Download a page or document (SSRF-guarded) and queue it."""
    _require_enabled(services)
    try:
        return await asyncio.to_thread(services.rag.add_url, body.collection, body.url)
    except RagError as exc:
        raise _refuse(exc) from None


@router.delete("/api/rag/documents/{doc_id}")
def delete_document(doc_id: int, _: AdminDep, services: ServicesDep) -> Ok:
    """Delete a document and its chunks."""
    try:
        services.rag.delete_document(doc_id)
    except RagError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return Ok()


@router.post("/api/rag/scan")
async def scan_folder(body: ScanIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Synchronise the knowledge folder with the knowledge base."""
    _require_enabled(services)
    return await asyncio.to_thread(services.rag.scan, body.collection)


@router.post("/api/rag/reindex")
def reindex(body: ReindexIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Queue every document of a collection (or of all) for re-embedding with the current model."""
    _require_enabled(services)
    try:
        return {"queued": services.rag.reindex(body.collection)}
    except RagError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None


@router.post("/api/rag/search")
async def search(body: SearchIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Run a search and show how each hit was found (dense and keyword ranks)."""
    _require_enabled(services)
    try:
        result = await asyncio.to_thread(services.rag.search, body.query, [body.collection] if body.collection else None, body.limit, body.mode)
    except RagError as exc:
        raise _refuse(exc) from None
    return {
        "notes": result.notes,
        "hits": [
            {"collection": h.collection, "source": h.source, "title": h.title, "heading": h.heading, "chunks": h.ords, "text": h.text,
             "score": round(h.score, 5), "dense_rank": h.dense_rank, "keyword_rank": h.keyword_rank, "flagged": h.flagged,
             "machine_read": h.vision, "page": h.page}
            for h in result.hits
        ],
    }
