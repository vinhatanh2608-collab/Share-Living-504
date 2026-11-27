import json
import logging
import httpx
from urllib.parse import urlsplit


from ..core.config import settings

logger = logging.getLogger(__name__)


async def request_advice(client, endpoint, key, model, message, context):
    payload = {"model": model, "max_tokens": 400, "messages": [
        {"role": "system", "content": "Explain the supplied VND household spending and budgets. "
         "Keep advice practical. Never invent figures or propose investments, loans, or money transfers."},
        {"role": "system", "content": "Authenticated account spending summary: " + json.dumps(context)},
        {"role": "user", "content": message},
    ]}
    async with client.stream("POST", endpoint, json=payload,
                             headers={"Authorization": f"Bearer {key}", "Accept-Encoding": "identity"}) as response:
        response.raise_for_status()
        data = bytearray()
        async for chunk in response.aiter_raw(8192):
            if len(data) + len(chunk) > 64000:
                raise ValueError("Provider response too large")
            data.extend(chunk)
    content = json.loads(data)["choices"][0]["message"]["content"]
    return content.strip() if isinstance(content, str) else None


async def provider_reply(message, context, client, breaker):
    cfg = settings()
    if cfg.gemini_api_key:
        base = "https://generativelanguage.googleapis.com/v1beta/openai"
        key, model = cfg.gemini_api_key, cfg.gemini_model
    elif cfg.llm_base_url and cfg.llm_api_key and cfg.llm_model:
        base, key, model = cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model
    else:
        return None
    url = urlsplit(base)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        return None
    try:
        reply = await breaker.call(request_advice, client, f"{base.rstrip('/')}/chat/completions",
                                   key.get_secret_value(), model, message, context)
        secrets = (cfg.gemini_api_key, getattr(cfg, "llm_api_key", None))
        if reply and any(secret and secret.get_secret_value() in reply for secret in secrets):
            return None
        return reply[:2000] if reply else None
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
        logger.warning("Financial advisor provider unavailable")
        return None
