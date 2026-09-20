"""In-process publish/subscribe event bus with full instrumentation.

Semantically equivalent to Redis pub/sub (topic -> subscribers), but runs
inside the single MAWOS process so the whole system needs zero external
infrastructure. Every hop is persisted to `workflow_events`, which is what
makes cross-agent propagation latency a *measured* quantity rather than a
claim.

A `workflow_id` is attached to the first publish of a cascade and carried
through every downstream event, so a 7-agent chain can be reconstructed
and timed end-to-end from the audit table.
"""
import json
import time
import uuid
from collections import defaultdict
from typing import Any, Awaitable, Callable

from .database import SessionLocal
from .models import WorkflowEvent

Handler = Callable[[dict], Awaitable[None]]


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[tuple[str, Handler]]] = defaultdict(list)
        # workflow_id -> monotonic start time, used to compute per-hop elapsed ms
        self._workflow_start: dict[str, float] = {}
        self._privacy_safe_workflows: set[str] = set()

    def subscribe(self, topic: str, agent_name: str, handler: Handler) -> None:
        self._subscribers[topic].append((agent_name, handler))

    def _log(self, workflow_id: str, topic: str, agent: str, hop: int,
             payload: dict, elapsed_ms: float) -> None:
        stored_payload = payload
        if workflow_id in self._privacy_safe_workflows or payload.get("_privacy_safe_log"):
            # Handlers retain the full in-process event for established
            # deterministic cascades, while the durable audit log contains no
            # roster/student identity list.
            stored_payload = {key: value for key, value in payload.items()
                              if key not in {"usns", "updates", "results"}}
        db = SessionLocal()
        try:
            db.add(WorkflowEvent(
                workflow_id=workflow_id, topic=topic, agent=agent, hop=hop,
                payload=json.dumps(stored_payload, default=str)[:2000],
                elapsed_ms=round(elapsed_ms, 2),
            ))
            db.commit()
        finally:
            db.close()

    async def publish(self, topic: str, payload: dict[str, Any],
                      source_agent: str = "system") -> str:
        """Publish an event and await all subscriber handlers.

        Returns the workflow_id so callers can correlate the cascade.
        """
        workflow_id = payload.get("workflow_id") or str(uuid.uuid4())
        payload = {**payload, "workflow_id": workflow_id}
        if payload.get("_privacy_safe_log"):
            self._privacy_safe_workflows.add(workflow_id)

        if workflow_id not in self._workflow_start:
            self._workflow_start[workflow_id] = time.perf_counter()
        elapsed = (time.perf_counter() - self._workflow_start[workflow_id]) * 1000
        hop = payload.get("_hop", 0)
        self._log(workflow_id, topic, source_agent, hop, payload, elapsed)

        for agent_name, handler in self._subscribers.get(topic, []):
            # Fault isolation: one failing subscriber must not take down the
            # rest of the cascade. The failure itself becomes an auditable
            # `agent.error` event under the same workflow_id, so recovery can
            # replay from the audit log.
            try:
                await handler({**payload, "_hop": hop + 1})
            except Exception as exc:  # noqa: BLE001 — deliberate isolation boundary
                err_elapsed = (time.perf_counter()
                               - self._workflow_start.get(workflow_id,
                                                          time.perf_counter())) * 1000
                self._log(workflow_id, "agent.error", agent_name, hop + 1,
                          {"failed_topic": topic, "error": str(exc)[:300]},
                          err_elapsed)

        # Root publisher cleans up the start marker once the cascade returns.
        if hop == 0:
            self._workflow_start.pop(workflow_id, None)
            self._privacy_safe_workflows.discard(workflow_id)
        return workflow_id


bus = EventBus()
