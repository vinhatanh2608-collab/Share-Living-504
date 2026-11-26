import asyncio
import json
import logging
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..core.config import settings

logger = logging.getLogger(__name__)
provider_slots = asyncio.Semaphore(4)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_advice(endpoint, key, model, message, context):
    payload = {"model": model, "max_tokens": 400, "messages": [
        {"role": "system", "content": "Explain the supplied VND household spending and budgets. "
         "Keep advice practical. Never invent figures or propose investments, loans, or money transfers."},
        {"role": "system", "content": "Authenticated account spending summary: " + json.dumps(context)},
        {"role": "user", "content": message},
    ]}
    req = Request(endpoint, data=json.dumps(payload).encode(), method="POST",
                  headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with build_opener(NoRedirect()).open(req, timeout=8) as response:
        content = json.loads(response.read(64_000))["choices"][0]["message"]["content"]
    return content.strip() if isinstance(content, str) else None


async def provider_reply(message, context):
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
        async with provider_slots:
            reply = await asyncio.to_thread(request_advice, f"{base.rstrip('/')}/chat/completions",
                                            key.get_secret_value(), model, message, context)
        secrets = (cfg.gemini_api_key, getattr(cfg, "llm_api_key", None))
        if reply and any(secret and secret.get_secret_value() in reply for secret in secrets):
            return None
        return reply[:2000] if reply else None
    except (URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
        logger.warning("Financial advisor provider unavailable")
        return None
