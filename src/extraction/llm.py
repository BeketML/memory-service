from __future__ import annotations

import logging
from src.config import settings

logger = logging.getLogger(__name__)


async def call_llm(system: str, user: str, max_tokens: int = 2048) -> str:
    provider = settings.llm_provider.lower()

    if provider == "openai":
        return await _call_openai(system, user, max_tokens)
    elif provider == "anthropic":
        return await _call_anthropic(system, user, max_tokens)
    elif provider == "ollama":
        return await _call_ollama(system, user, max_tokens)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}")


async def _call_openai(system: str, user: str, max_tokens: int) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    resp = await client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or "[]"


async def _call_anthropic(system: str, user: str, max_tokens: int) -> str:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    msg = await client.messages.create(
        model=settings.llm_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        temperature=0.0,
    )
    return msg.content[0].text if msg.content else "[]"


async def _call_ollama(system: str, user: str, max_tokens: int) -> str:
    import httpx

    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": max_tokens},
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.ollama_host}/api/chat",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "[]")
