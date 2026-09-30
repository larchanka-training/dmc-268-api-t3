from typing import Protocol

from app.llm.schemas import ReviewResult


class Provider(Protocol):
    name: str

    def generate_structured(self, prompt: str) -> ReviewResult: ...
