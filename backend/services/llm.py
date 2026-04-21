import json
import httpx
from core.logging import setup_logging
import config

logger = setup_logging()

async def ask_qwen(prompt: str) -> str:
    logger.info(f"Prompt send to LLM: {prompt}")

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
        f"{config.LLM_URL}/v1/chat/completions",
        json={
            "model": "qwen2.5-coder-14b",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1024,
        },
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]

async def stream_qwen(prompt: str):
    logger.info(f"Prompt send to LLM: {prompt}")

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
        f"{config.LLM_URL}/v1/chat/completions",
        json={
            "model": "qwen2.5-coder-14b",
            "messages": [{"role": "user", "content": prompt}],
            "stream": True
        },
    ) as response:  
            async for line in response.aiter_lines():
                    if line:
                        if line.startswith("data:"):
                            payload = line[5:].strip()

                            if payload == "[DONE]":
                                break

                            try:
                                chunk = json.loads(payload)
                                token = chunk["choices"][0]["delta"].get("content", "")
                                if token:
                                    yield token
                            except Exception as e:
                                logger.error(f"Stream parsing error: {e}")
