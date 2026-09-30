"""Hourly extraction with a persisted deadline and a bounded batch size."""

import time

from .service import Service


async def run_due_batch(service: Service, now: float | None = None, limit: int = 100) -> int:
    current = time.time() if now is None else now
    if current < service.store.next_batch_at():
        return 0
    processed = 0
    # Advance only after the batch: a crash leaves work eligible at restart.
    while processed < limit and await service.process_one():
        processed += 1
    service.store.schedule_batch(current + service.config.interval_seconds)
    return processed
