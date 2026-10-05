from app.llm.providers.base import Provider
from app.llm.schemas import ReviewResult


class AllProvidersFailedError(RuntimeError):
    pass


class LLMGateway:
    def __init__(self, providers: list[Provider]) -> None:
        if not providers:
            raise ValueError("LLMGateway requires at least one provider")
        self._providers = providers

    def generate_structured(self, prompt: str) -> ReviewResult:
        errors: list[str] = []
        for provider in self._providers:
            try:
                return provider.generate_structured(prompt)
            # Any provider failure (network, validation, parsing) must trigger
            # fallback to the next provider rather than aborting the review.
            # pylint: disable-next=broad-except
            except Exception as exc:
                detail = str(exc) or type(exc).__name__
                errors.append(f"{provider.name}: {detail}")

        raise AllProvidersFailedError("; ".join(errors))
