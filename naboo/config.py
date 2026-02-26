"""
Naboo agent configuration.

Reads from environment variables (.env / infra/.env).
Creates the 3-tier model router: S1 (fast local) → S2 (smart local) → Bedrock (fallback).
"""

import os
import logging
from dotenv import load_dotenv

from naboo.router.model_router import ModelRouter, ModelConfig, create_bedrock_config_from_env
from naboo.router.query_classifier import QueryClassifier, QueryComplexity

# Load .env from infra/ directory
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "infra", ".env"))

logger = logging.getLogger(__name__)

# ── Ollama ──────────────────────────────────────────────────────────────────
OLLAMA_HOST     = os.getenv("OLLAMA_HOST", "http://192.168.0.50:11434")
OLLAMA_MODEL_S1 = os.getenv("OLLAMA_MODEL_S1", "qwen2.5:3b")   # ~1-2s
OLLAMA_MODEL_S2 = os.getenv("OLLAMA_MODEL_S2", "qwen2.5:7b")   # ~5-6s

# ── MLX (optional — native Metal, faster on Apple Silicon) ──────────────────
# Set MLX_HOST to use MLX-LM server instead of Ollama (same models, ~3x faster)
MLX_HOST        = os.getenv("MLX_HOST", "")   # e.g. http://192.168.0.50:11435
MLX_MODEL_S1    = os.getenv("MLX_MODEL_S1", "mlx-community/Qwen2.5-3B-Instruct-4bit")
MLX_MODEL_S2    = os.getenv("MLX_MODEL_S2", "mlx-community/Qwen2.5-7B-Instruct-4bit")

# ── Bedrock fallback ────────────────────────────────────────────────────────
BEDROCK_REGION   = os.getenv("BEDROCK_REGION", "eu-west-2")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "eu.anthropic.claude-haiku-4-5-20251001-v1:0")

# ── MQTT / IoT ──────────────────────────────────────────────────────────────
IOT_ENDPOINT    = os.getenv("AWS_IOT_ENDPOINT", "")
IOT_THING_NAME  = os.getenv("IOT_THING_NAME", "naboo")
IOT_CERT_PATH   = os.getenv("IOT_CERT_PATH", "/app/certs/device.cert.pem")
IOT_KEY_PATH    = os.getenv("IOT_KEY_PATH", "/app/certs/device.private.key")
IOT_CA_PATH     = os.getenv("IOT_CA_PATH", "/app/certs/AmazonRootCA1.pem")

# ── Home Assistant ──────────────────────────────────────────────────────────
HA_URL          = os.getenv("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN        = os.getenv("HA_TOKEN", "")
CAMERA_ENTITY   = os.getenv("CAMERA_ENTITY", "camera.esp32_cam")


def build_model_router() -> ModelRouter:
    """
    Build the 3-tier model router.

    With MLX_HOST set (recommended on Mac mini M4):
      Tier 1 (SIMPLE):   MLX qwen2.5:3b  — ~1-2s (native Metal)
      Tier 2 (MODERATE): MLX qwen2.5:7b  — ~3s (3x faster than Ollama!)

    Without MLX_HOST (Ollama fallback):
      Tier 1 (SIMPLE):   Ollama qwen2.5:3b  — ~2-3s
      Tier 2 (MODERATE): Ollama qwen2.5:7b  — ~8-10s

    Tier 3 (COMPLEX/CURRENT_INFO): AWS Bedrock Claude — cloud fallback
    """
    use_mlx = bool(MLX_HOST)

    if use_mlx:
        s1 = ModelConfig(
            provider="mlx",
            model_id=MLX_MODEL_S1,
            host=MLX_HOST,
            cost_per_1k_input_tokens=0.0,
            cost_per_1k_output_tokens=0.0,
            max_tokens=500,
            supports_streaming=False,
            supports_vision=False,
        )
        s2 = ModelConfig(
            provider="mlx",
            model_id=MLX_MODEL_S2,
            host=MLX_HOST,
            cost_per_1k_input_tokens=0.0,
            cost_per_1k_output_tokens=0.0,
            max_tokens=1000,
            supports_streaming=False,
            supports_vision=False,
        )
    else:
        s1 = ModelConfig(
            provider="ollama",
            model_id=OLLAMA_MODEL_S1,
            host=OLLAMA_HOST,
            cost_per_1k_input_tokens=0.0,
            cost_per_1k_output_tokens=0.0,
            max_tokens=500,
            supports_streaming=True,
            supports_vision=False,
        )
        s2 = ModelConfig(
            provider="ollama",
            model_id=OLLAMA_MODEL_S2,
            host=OLLAMA_HOST,
            cost_per_1k_input_tokens=0.0,
            cost_per_1k_output_tokens=0.0,
            max_tokens=1000,
            supports_streaming=True,
            supports_vision=False,
        )

    bedrock = create_bedrock_config_from_env(
        model_id_env_var="BEDROCK_MODEL_ID",
        region_env_var="BEDROCK_REGION",
        supports_streaming=True,
        supports_vision=True,  # Claude has vision
    )

    router = ModelRouter(
        model_configs={
            QueryComplexity.SIMPLE:       s1,
            QueryComplexity.MODERATE:     s2,
            QueryComplexity.COMPLEX:      bedrock,
            QueryComplexity.CURRENT_INFO: bedrock,
        }
    )

    if use_mlx:
        logger.info(
            f"Model router built (MLX): "
            f"SIMPLE={MLX_MODEL_S1}@{MLX_HOST}, "
            f"MODERATE={MLX_MODEL_S2}@{MLX_HOST}, "
            f"COMPLEX/CURRENT_INFO=Bedrock/{BEDROCK_MODEL_ID}"
        )
    else:
        logger.info(
            f"Model router built (Ollama): "
            f"SIMPLE={OLLAMA_MODEL_S1}@{OLLAMA_HOST}, "
            f"MODERATE={OLLAMA_MODEL_S2}@{OLLAMA_HOST}, "
            f"COMPLEX/CURRENT_INFO=Bedrock/{BEDROCK_MODEL_ID}"
        )

    return router


def build_query_classifier() -> QueryClassifier:
    """Build the query classifier with default settings."""
    return QueryClassifier(cache_ttl=300.0)
