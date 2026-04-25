"""Gemini Flash-Lite wrapper for summarization and compression.

Replaces the upstream haiku.py which spawned nested `claude -p` subprocesses.
Uses the google-genai SDK with Gemini 2.5 Flash-Lite for all summarization:
session saves, NDC compression, and consolidation.

Requires GEMINI_API_KEY in the environment (exported in client profile).
"""

from __future__ import annotations

import os

from .types import HaikuResult, TokenUsage

MODEL = "gemini-2.5-flash-lite"

INPUT_PRICE = 0.075 / 1_000_000
OUTPUT_PRICE = 0.30 / 1_000_000


def call_haiku(
    prompt: str,
    timeout: int = 120,
    max_output_tokens: int = 500,
) -> HaikuResult:
    """Call Gemini Flash-Lite and return a structured result.

    Keeps the call_haiku name for compatibility with existing callers.
    """
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=max_output_tokens,
                http_options=types.HttpOptions(timeout=timeout * 1000),
            ),
        )
    except Exception as e:
        raise RuntimeError(f"Gemini API error ({type(e).__name__}): {e}") from e

    text = response.text or ""
    is_skip = text.strip().upper().startswith("SKIP")

    usage = response.usage_metadata
    input_tokens = (usage.prompt_token_count or 0) if usage else 0
    output_tokens = (usage.candidates_token_count or 0) if usage else 0
    cost = float(input_tokens) * INPUT_PRICE + float(output_tokens) * OUTPUT_PRICE

    return HaikuResult(
        text=text,
        tokens=TokenUsage(
            input=input_tokens,
            output=output_tokens,
            cache=0,
            cost_usd=cost,
        ),
        is_skip=is_skip,
    )
