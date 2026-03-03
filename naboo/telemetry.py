"""
OpenTelemetry setup for Naboo.

Exports traces via OTLP (gRPC or HTTP).
Configure with env vars:
  OTEL_EXPORTER_OTLP_ENDPOINT  — e.g. http://192.168.0.50:4317 (gRPC default)
  OTEL_EXPORTER_OTLP_PROTOCOL  — "grpc" (default) or "http/protobuf"
  OTEL_SERVICE_NAME            — defaults to "naboo"
  NABOO_OTEL_ENABLED           — set to "false" to disable entirely
"""

import logging
import os
import time
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

# Lazy-initialised tracer — None until setup() is called
_tracer = None


def setup(service_name: Optional[str] = None) -> None:
    """Initialise OTel SDK. Safe to call multiple times (no-op after first)."""
    global _tracer

    if _tracer is not None:
        return  # already set up

    if os.getenv("NABOO_OTEL_ENABLED", "true").lower() == "false":
        logger.info("OTel disabled (NABOO_OTEL_ENABLED=false)")
        return

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if not endpoint:
        logger.info("OTel: no OTEL_EXPORTER_OTLP_ENDPOINT set — traces will be no-ops")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource

        name = service_name or os.getenv("OTEL_SERVICE_NAME", "naboo")
        resource = Resource.create({"service.name": name})
        provider = TracerProvider(resource=resource)

        protocol = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc").lower()

        if protocol == "http/protobuf":
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        else:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)

        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        _tracer = trace.get_tracer("naboo")
        logger.info(f"OTel tracing enabled → {endpoint} ({protocol})")

    except ImportError as e:
        logger.warning(f"OTel packages not installed, tracing disabled: {e}")
    except Exception as e:
        logger.warning(f"OTel setup failed (non-fatal): {e}")


def get_tracer():
    """Return the tracer, or None if OTel is not set up."""
    return _tracer


@contextmanager
def span(name: str, attributes: Optional[dict] = None) -> Iterator:
    """
    Context manager for a named span. No-op if OTel not configured.

    Usage:
        with telemetry.span("naboo.route", {"complexity": "simple"}) as s:
            ...
            s.set_attribute("model", "mlx")
    """
    tracer = get_tracer()
    if tracer is None:
        yield _NoOpSpan()
        return

    with tracer.start_as_current_span(name) as s:
        if attributes:
            for k, v in attributes.items():
                if v is not None:
                    s.set_attribute(k, str(v))
        yield s


class _NoOpSpan:
    """Dummy span used when OTel is disabled."""
    def set_attribute(self, key, value):
        pass

    def record_exception(self, exc):
        pass

    def set_status(self, status):
        pass


class NabooCallbackHandler:
    """
    Strands callback handler that emits OTel spans for tool invocations.

    Attach to Agent via: agent = Agent(..., callback_handler=NabooCallbackHandler())

    When a tool fires, this records a child span under the current active span.
    Each tool invocation gets: start time, tool name, and duration.
    """

    def __init__(self):
        self._pending: dict[str, tuple[object, float]] = {}  # toolUseId → (span, t0)

    def __call__(self, **kwargs) -> None:
        event = kwargs.get("event", {})
        if not event:
            return

        # Tool starting — contentBlockStart with toolUse
        tool_use = event.get("contentBlockStart", {}).get("start", {}).get("toolUse")
        if tool_use:
            tool_name = tool_use.get("name", "unknown")
            tool_id = tool_use.get("toolUseId", tool_name)
            tracer = get_tracer()
            if tracer:
                s = tracer.start_span(f"naboo.tool.{tool_name}", attributes={"tool": tool_name})
                self._pending[tool_id] = (s, time.monotonic())

        # Tool result — contentBlockStop or toolResult event
        content_stop = event.get("contentBlockStop")
        if content_stop and self._pending:
            # Close the most recently opened tool span
            for tool_id, (s, t0) in list(self._pending.items()):
                s.set_attribute("duration_ms", f"{(time.monotonic() - t0) * 1000:.0f}")
                s.end()
                del self._pending[tool_id]
                break
