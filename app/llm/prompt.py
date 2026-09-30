from dataclasses import dataclass, field

from app.llm.tokens import approximate_token_count


@dataclass
class FewShotExample:
    diff: str
    review: str


@dataclass
class PromptContext:
    language: str | None
    commit_message: str | None
    few_shot_examples: list[FewShotExample] = field(default_factory=list)


def build_system_prompt(context: PromptContext, diff: str, max_tokens: int) -> str:
    examples = list(context.few_shot_examples)

    while True:
        prompt = _assemble(context, examples, diff)
        if approximate_token_count(prompt) <= max_tokens or not examples:
            return prompt
        examples.pop()


def _build_header(context: PromptContext) -> str:
    lines = ["You are an expert code reviewer. Respond with structured findings only."]
    if context.language:
        lines.append(f"Language: {context.language}")
    if context.commit_message:
        lines.append(f"Commit message: {context.commit_message}")
    return "\n".join(lines)


def _assemble(context: PromptContext, examples: list[FewShotExample], diff: str) -> str:
    parts = [_build_header(context)]
    for example in examples:
        parts.append(f"Example diff:\n{example.diff}\nExample review:\n{example.review}")
    parts.append(f"Diff to review:\n{diff}")
    return "\n\n".join(parts)
