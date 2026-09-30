import json
from typing import Any

from app.llm.schemas import ReviewResult


class OllamaProvider:
    name = "ollama"

    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    def generate_structured(self, prompt: str) -> ReviewResult:
        response = self._client.generate(
            model=self._model,
            prompt=prompt,
            format=ReviewResult.model_json_schema(),
        )
        payload = json.loads(response["response"])
        return ReviewResult.model_validate(payload)
