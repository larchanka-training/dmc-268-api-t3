import json

import httpx
from pydantic import ValidationError

from app.llm.schemas import ReviewResult

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class EurorouterProvider:
    name = "eurorouter"

    def __init__(
        self,
        base_url: str,
        api_keys: list[str],
        model: str,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_keys:
            raise ValueError("EurorouterProvider requires at least one API key")
        self._base_url = base_url.rstrip("/")
        self._api_keys = api_keys
        self._model = model
        self._client = client or httpx.Client()

    def generate_structured(self, prompt: str) -> ReviewResult:
        last_error: Exception | None = None
        for api_key in self._api_keys:
            try:
                response = self._client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": self._model,
                        "messages": [{"role": "user", "content": prompt}],
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "review_result",
                                "schema": ReviewResult.model_json_schema(),
                            },
                        },
                    },
                    timeout=60.0,
                )
                if response.status_code in RETRYABLE_STATUS_CODES:
                    last_error = RuntimeError(
                        f"Eurorouter returned {response.status_code} for key ending "
                        f"in ...{api_key[-4:]}"
                    )
                    continue
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return ReviewResult.model_validate(json.loads(content))
            except httpx.HTTPError as exc:
                last_error = exc
                continue
            # A 200 response with a malformed/unexpected body (bad JSON, missing
            # choices, schema mismatch) must also trigger key rotation rather than
            # aborting on the first key — same fallback contract as HTTP errors.
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                continue

        if last_error is None:
            raise RuntimeError("Eurorouter provider exhausted all keys with no recorded error")
        raise last_error
