"""
OpenTelemetry setup for Naboo.

Delegates to Strands' own StrandsTelemetry so the global provider is set up
correctly and Strands' native spans (LLM calls, tool invocations, token usage)
are exported alongside Naboo's custom spans.

Configure with env vars (standard OTel):
  OTEL_EXPORTER_OTLP_ENDPOINT  — e.g. http://192.168.0.185:4317
  OTEL_SERVICE_NAME            — defaults to "naboo"
  NABOO_OTEL_ENABLED           — set to "false" to disable entirely
"""

import logging
import os
import time
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

_initialised = False


def setup(service_name: Optional[str] = None) -> None:
    """
    Initialise OTel by delegating to StrandsTelemetry.

    Strands owns the global TracerProvider — using their setup avoids
    the 20-40s stall caused by our provider conflicting with Strands'
    own tracer/span creation on every event loop cycle.
    """
    global _initialised

    if _initialised:
        return

    if os.getenv("NABOO_OTEL_ENABLED", "true").lower() == "false":
        logger.info("OTel disabled (NABOO_OTEL_ENABLED=false)")
        _initialised = True
        return

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if not endpoint:
        logger.info("OTel: no OTEL_EXPORTER_OTLP_ENDPOINT set — traces will be no-ops")
        _initialised = True
        return

    # Set service name env var if passed (StrandsTelemetry reads OTEL_SERVICE_NAME)
    if service_name:
        os.environ.setdefault("OTEL_SERVICE_NAME", service_name)
    else:
        os.environ.setdefault("OTEL_SERVICE_NAME", "naboo")

    try:
        from strands.telemetry import StrandsTelemetry
        strands_telemetry = StrandsTelemetry()
        strands_telemetry.setup_otlp_exporter()
        _initialised = True
        logger.info(f"OTel tracing enabled via StrandsTelemetry → {endpoint}")
    except ImportError:
        logger.warning("StrandsTelemetry not available — tracing disabled")
        _initialised = True
    except Exception as e:
        logger.warning(f"OTel setup failed (non-fatal): {e}")
        _initialised = True


def get_tracer():
    """Return the global OTel tracer (set up by StrandsTelemetry), or None."""
    if not _initialised:
        return None
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if not endpoint or os.getenv("NABOO_OTEL_ENABLED", "true").lower() == "false":
        return None
    try:
        from opentelemetry import trace
        tracer = trace.get_tracer("naboo")
        return tracer
    except Exception:
        return None


@contextmanager
def span(name: str, attributes: Optional[dict] = None) -> Iterator:
    """
    Context manager for a named span under the current active span.
    No-op if OTel not configured.

    Usage:
        with telemetry.span("naboo.route", {"complexity": "simple"}) as s:
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
