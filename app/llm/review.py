from app.llm.gateway import LLMGateway
from app.llm.prompt import FewShotExample, PromptContext, build_system_prompt
from app.llm.schemas import ReviewResult


def generate_review(
    gateway: LLMGateway,
    diff: str,
    language: str | None = None,
    commit_message: str | None = None,
    few_shot_examples: list[FewShotExample] | None = None,
    max_prompt_tokens: int = 4000,
) -> ReviewResult:
    context = PromptContext(
        language=language,
        commit_message=commit_message,
        few_shot_examples=few_shot_examples or [],
    )
    prompt = build_system_prompt(context, diff, max_prompt_tokens)
    return gateway.generate_structured(prompt)
