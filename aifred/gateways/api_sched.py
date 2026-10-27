"""Dashboard API for scheduled tasks (docs/specs.md section 14)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from aifred.gateways.api import AdminDep, Ok, ServicesDep
from aifred.scheduler import SchedulerError

router = APIRouter()


class ScheduleIn(BaseModel):
    """Task fields; on update an omitted field keeps its value (``deliver_chat: null`` stops delivery)."""

    expression: str | None = None
    prompt: str | None = None
    deliver_chat: int | None = None
    enabled: bool | None = None


class PreviewIn(BaseModel):
    """An expression to check."""

    expression: str


def _error(exc: SchedulerError) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if str(exc).startswith("no task") else status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(code, str(exc))


@router.get("/api/schedules")
async def list_schedules(_: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Every task with its next run, plus the scheduler's state."""
    rows = await asyncio.to_thread(services.store.schedule_list)
    cfg = services.config
    return {
        "enabled": bool(cfg.get("SCHEDULER_ENABLED")), "running": services.scheduler.running,
        "timezone": str(cfg.get("SCHEDULER_TIMEZONE")), "min_interval": int(cfg.get("SCHEDULER_MIN_INTERVAL")),
        "telegram_ids": list(cfg.get("TELEGRAM_ALLOWED_IDS")), "tasks": [services.scheduler.describe(r) for r in rows],
    }


@router.post("/api/schedules/preview")
async def preview(body: PreviewIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """The next run times of an expression."""
    try:
        return {"runs": await asyncio.to_thread(services.scheduler.preview, body.expression, 5)}
    except SchedulerError as exc:
        raise _error(exc) from None


@router.put("/api/schedules/{name}")
async def save_schedule(name: str, body: ScheduleIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Create a task (``expression`` and ``prompt`` required) or change an existing one."""
    sched = services.scheduler
    fields = body.model_fields_set
    try:
        if await asyncio.to_thread(services.store.schedule_get, name) is None:
            if not body.expression or not body.prompt:
                raise SchedulerError("a new task needs an expression and a prompt")
            row = await asyncio.to_thread(sched.create, name, body.expression, body.prompt, body.deliver_chat, enabled=body.enabled is not False)
        else:
            keep: Any = body.deliver_chat if "deliver_chat" in fields else "keep"
            row = await asyncio.to_thread(sched.update, name, expression=body.expression, prompt=body.prompt, deliver_chat=keep)
            if body.enabled is not None and body.enabled != row["enabled"]:
                row = await asyncio.to_thread(sched.set_enabled, name, body.enabled)
    except SchedulerError as exc:
        raise _error(exc) from None
    return sched.describe(row)


@router.post("/api/schedules/{name}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_schedule(name: str, _: AdminDep, services: ServicesDep) -> Ok:
    """Run a task now, in the background."""
    try:
        await asyncio.to_thread(services.scheduler.run_now, name)
    except SchedulerError as exc:
        raise _error(exc) from None
    return Ok()


@router.get("/api/schedules/{name}/runs")
async def schedule_runs(name: str, _: AdminDep, services: ServicesDep) -> list[dict[str, Any]]:
    """Recent runs with their output."""
    try:
        await asyncio.to_thread(services.scheduler.get, name)
    except SchedulerError as exc:
        raise _error(exc) from None
    return await asyncio.to_thread(services.store.schedule_runs, name, 20)


@router.delete("/api/schedules/{name}")
async def delete_schedule(name: str, _: AdminDep, services: ServicesDep) -> Ok:
    """Delete a task and its history."""
    try:
        await asyncio.to_thread(services.scheduler.remove, name)
    except SchedulerError as exc:
        raise _error(exc) from None
    return Ok()
