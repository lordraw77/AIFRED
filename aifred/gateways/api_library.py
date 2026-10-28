"""Dashboard API for the library: saved answers and results (docs/specs.md section 15)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from aifred.gateways.api import AdminDep, ServicesDep
from aifred.library import LibraryError

router = APIRouter()
_CODES = {"missing": status.HTTP_404_NOT_FOUND, "exists": status.HTTP_409_CONFLICT}


def _fail(exc: LibraryError) -> HTTPException:
    return HTTPException(_CODES.get(exc.code, status.HTTP_422_UNPROCESSABLE_CONTENT), str(exc))


class ItemIn(BaseModel):
    """A new item."""

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=250_000)
    prompt: str = Field(default="", max_length=8000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source: str = "manual"
    source_ref: str = Field(default="", max_length=200)
    replace: bool = False


class ItemPatch(BaseModel):
    """Fields to change; omitted ones stay."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=250_000)
    prompt: str | None = Field(default=None, max_length=8000)
    tags: list[str] | None = Field(default=None, max_length=20)


@router.get("/api/library")
def list_items(
    _: AdminDep, services: ServicesDep, q: Annotated[str, Query(max_length=300)] = "", tag: Annotated[str, Query(max_length=40)] = "",
) -> dict[str, Any]:
    """Items (latest first) or, with ``q``, the best matches; ``tags`` lists every tag in use."""
    store = services.store
    if q.strip():
        items = [store.library_get(h["id"]) for h in services.library.search(q, 50)]
        items = [i for i in items if i and (not tag or tag in i["tags"])]
    else:
        items = store.library_list(tag or None, 500)
    for item in items:
        item["preview"] = item.pop("content")[:240]
    tags = sorted({t for i in store.library_list(None, 100_000) for t in i["tags"]})
    return {"items": items, "tags": tags, "count": store.library_count(), "limit": int(services.config.get("LIBRARY_MAX_ITEMS"))}


@router.post("/api/library", status_code=status.HTTP_201_CREATED)
def create_item(body: ItemIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Save a text; 409 when the title exists (send ``replace`` to rewrite it)."""
    try:
        item, created = services.library.save(
            body.title, body.content, prompt=body.prompt, tags=body.tags, source=body.source, source_ref=body.source_ref, replace=body.replace,
        )
    except LibraryError as exc:
        raise _fail(exc) from None
    services.store.journal_add("library", "create" if created else "update", item["title"][:300], None, "applied", {"id": item["id"]})
    return item


@router.get("/api/library/{item_id}")
def get_item(item_id: int, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """One item in full."""
    item = services.store.library_get(item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such item")
    return item


@router.put("/api/library/{item_id}")
def update_item(item_id: int, body: ItemPatch, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Change an item; a changed content keeps the old text for one undo (``restore``)."""
    try:
        return services.library.update(item_id, body.model_dump(exclude_none=True))
    except LibraryError as exc:
        raise _fail(exc) from None


@router.delete("/api/library/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, _: AdminDep, services: ServicesDep) -> None:
    """Delete an item."""
    try:
        services.library.delete(item_id)
    except LibraryError as exc:
        raise _fail(exc) from None
    services.store.journal_add("library", "delete", f"deleted item #{item_id}", None, "applied", {"id": item_id})


@router.post("/api/library/{item_id}/restore")
def restore_item(item_id: int, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Swap the content with the previous version."""
    try:
        return services.library.restore(item_id)
    except LibraryError as exc:
        raise _fail(exc) from None


@router.post("/api/library/{item_id}/regenerate")
async def regenerate_item(item_id: int, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Ask again with the stored request; returns a draft and saves nothing."""
    try:
        draft = await services.library.regenerate(item_id)
    except LibraryError as exc:
        raise _fail(exc) from None
    return {"draft": draft}
