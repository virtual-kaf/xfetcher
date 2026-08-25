import json

import httpx
from nonebot import logger

from ..config import GROK_API_KEY, GROK_API_URL, REQUEST_TIMEOUT


class GrokDiscoveryError(RuntimeError):
    """A Grok response that cannot be used for URL discovery."""

    def __init__(self, reason: str, status_code: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


GROK_URL_SYSTEM_PROMPT = """\
You are a strict X post URL extractor.
For each target account, find the LATEST post URLs from the provided input.
Return ONLY a JSON object, no markdown, no commentary.

Output schema:
{
  "members": [
    {"handle": "string", "urls": ["https://x.com/user/status/123", ...]}
  ]
}
"""


def _build_grok_prompt(members: list[str]) -> str:
    joined = ", ".join(members)
    return f"""Target accounts: {joined}

Search the latest posts for each account. Return ONLY the JSON with post URLs.
For each account, return up to 2 most recent post URLs."""


async def grok_fetch_urls(members: list[str]) -> list[dict]:
    """Fetch latest tweet URLs for each member via Grok."""
    payload = {
        "model": "grok-4.20-0309-non-reasoning",
        "temperature": 0,
        "top_p": 1,
        "stream": False,
        "messages": [
            {"role": "system", "content": GROK_URL_SYSTEM_PROMPT},
            {"role": "user", "content": _build_grok_prompt(members)},
        ],
    }

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
        r = await client.post(GROK_API_URL, headers={"Authorization": GROK_API_KEY}, json=payload)
        if r.status_code != 200:
            raise GrokDiscoveryError(
                f"http_{r.status_code}", status_code=r.status_code
            )

        try:
            body = r.json()
            content = body["choices"][0]["message"]["content"]
        except Exception as exc:
            raise GrokDiscoveryError("invalid_upstream_response") from exc

        if not isinstance(content, str):
            raise GrokDiscoveryError("invalid_model_content")

        # Extract JSON from response
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Try to find JSON block
            s = content.find("{")
            e = content.rfind("}")
            if s != -1 and e > s:
                try:
                    data = json.loads(content[s:e+1])
                except json.JSONDecodeError as exc:
                    raise GrokDiscoveryError("invalid_model_json") from exc
            else:
                raise GrokDiscoveryError("invalid_model_json")

        if not isinstance(data, dict):
            raise GrokDiscoveryError("invalid_model_schema")

        result: list[dict[str, str]] = []
        if "members" not in data:
            raise GrokDiscoveryError("invalid_model_schema")
        raw_members = data["members"]
        if not isinstance(raw_members, list):
            raise GrokDiscoveryError("invalid_model_schema")
        for m in raw_members:
            if not isinstance(m, dict):
                raise GrokDiscoveryError("invalid_model_schema")
            handle = m.get("handle")
            urls = m.get("urls")
            if (
                not isinstance(handle, str)
                or not handle.strip()
                or not isinstance(urls, list)
                or any(not isinstance(url, str) for url in urls)
            ):
                raise GrokDiscoveryError("invalid_model_schema")
            result.extend(
                {"member": handle, "url": url}
                for url in urls
            )

        logger.info(
            f"[Discovery] source=grok raw_urls={len(result)} "
            f"members={len(members)}"
        )
        return result
