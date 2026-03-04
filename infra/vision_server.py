#!/usr/bin/env python3
"""
Naboo Vision Server
Runs mlx-vlm on Mac mini M4, exposes HTTP API for image queries.
Port: 11436
"""
import base64
import contextlib
import io
import logging
import os
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("naboo.vision")

MODEL_ID = os.getenv("VISION_MODEL", "mlx-community/Qwen2-VL-2B-Instruct-4bit")

# Globals — loaded once at startup
_model = None
_processor = None
_config = None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _processor, _config
    logger.info(f"Loading vision model: {MODEL_ID}")
    t0 = time.time()
    from mlx_vlm import load
    from mlx_vlm.utils import load_config
    _model, _processor = load(MODEL_ID)
    _config = load_config(MODEL_ID)   # cache config — do NOT reload per-request
    logger.info(f"Vision model + config loaded in {time.time()-t0:.1f}s — ready")
    yield
    logger.info("Vision server shutting down")


app = FastAPI(title="Naboo Vision Server", lifespan=lifespan)


class VisionRequest(BaseModel):
    image_b64: str                   # base64-encoded JPEG/PNG
    question: str = "What do you see? Describe briefly in 2-3 sentences."
    max_tokens: int = 150


class VisionResponse(BaseModel):
    description: str
    model: str
    elapsed_s: float


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_ID, "loaded": _model is not None}


@app.post("/vision", response_model=VisionResponse)
def describe(req: VisionRequest):
    if _model is None:
        raise HTTPException(503, "Model not loaded yet")

    from PIL import Image
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template

    t0 = time.time()

    # Decode image
    try:
        image_bytes = base64.b64decode(req.image_b64)
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as e:
        raise HTTPException(400, f"Invalid image: {e}")

    # Build prompt using cached config
    prompt = apply_chat_template(_processor, _config, req.question, num_images=1)

    # Run inference
    result = generate(
        _model, _processor, prompt, [image],
        verbose=False, max_tokens=req.max_tokens
    )

    elapsed = time.time() - t0
    logger.info(f"Vision: '{req.question[:50]}' → {elapsed:.1f}s → '{result[:60]}'")

    return VisionResponse(description=result, model=MODEL_ID, elapsed_s=elapsed)


if __name__ == "__main__":
    port = int(os.getenv("VISION_PORT", "11436"))
    logger.info(f"Naboo Vision Server starting on :{port} (model: {MODEL_ID})")
    uvicorn.run(app, host="0.0.0.0", port=port)
