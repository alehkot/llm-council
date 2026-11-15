"""Authenticated basic-run endpoints; the application's auth middleware applies."""

import asyncio
from time import monotonic
from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response

from . import basic_runs
from .consultation_contract import AnswerRequest, CreateRunRequest, RetryRequest

router = APIRouter(prefix="/api/basic-runs")


def call(operation: Callable[..., Any], *args: Any) -> Any:
    try:
        return operation(*args)
    except LookupError:
        raise HTTPException(404, "Run not found") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except RuntimeError:
        raise HTTPException(
            409, "Saved run is unavailable; no work was restarted"
        ) from None


@router.get("")
async def history() -> Any:
    return call(basic_runs.history)


@router.put("/{run}")
async def create(run: UUID, request: CreateRunRequest) -> Any:
    return call(basic_runs.create, str(run), request)


@router.get("/{run}")
async def inspect(
    run: UUID, cursor: str | None = None, wait: float = Query(0, ge=0, le=25)
) -> Any:
    deadline = monotonic() + wait
    while True:
        state = call(basic_runs.snapshot, str(run))
        if state.cursor != cursor or monotonic() >= deadline:
            return state
        await asyncio.sleep(0.1)


@router.post("/{run}/resume")
async def resume(run: UUID) -> Any:
    call(basic_runs.schedule, str(run))
    return call(basic_runs.snapshot, str(run))


@router.post("/{run}/answers/{instance}")
async def answer(run: UUID, instance: UUID, request: AnswerRequest) -> Any:
    return call(basic_runs.answer, str(run), str(instance), request)


@router.post("/{run}/retry")
async def retry(run: UUID, request: RetryRequest) -> Any:
    return call(basic_runs.retry, str(run), request.revision)


@router.post("/{run}/cancel")
async def cancel(run: UUID, request: RetryRequest) -> Any:
    return call(basic_runs.cancel, str(run), request.revision)


@router.get("/{run}/export")
async def export(run: UUID) -> Response:
    return Response(call(basic_runs.export, str(run)), media_type="text/markdown")
