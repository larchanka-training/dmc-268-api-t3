from app.llm.prompt import FewShotExample, PromptContext, build_system_prompt
from app.llm.tokens import approximate_token_count


def test_prompt_includes_metadata_and_diff():
    context = PromptContext(language="python", commit_message="fix bug", few_shot_examples=[])
    prompt = build_system_prompt(context, diff="diff --git a b", max_tokens=1000)

    assert "python" in prompt
    assert "fix bug" in prompt
    assert "diff --git a b" in prompt


def test_prompt_includes_few_shot_examples_when_within_budget():
    context = PromptContext(
        language=None,
        commit_message=None,
        few_shot_examples=[FewShotExample(diff="example diff", review="example review")],
    )
    prompt = build_system_prompt(context, diff="short diff", max_tokens=1000)

    assert "example diff" in prompt
    assert "example review" in prompt


def test_prompt_trims_few_shot_examples_to_fit_budget():
    examples = [
        FewShotExample(diff=f"example {i}" * 50, review=f"review {i}" * 50) for i in range(5)
    ]
    context = PromptContext(language=None, commit_message=None, few_shot_examples=examples)

    prompt = build_system_prompt(context, diff="short diff", max_tokens=50)

    included = sum(1 for i in range(5) if f"example {i}" in prompt)
    assert included < 5
    assert approximate_token_count(prompt) <= 50 or included == 0
    if included > 0:
        assert all(f"example {i}" in prompt for i in range(included))


def test_prompt_never_drops_the_diff_even_when_over_budget():
    context = PromptContext(language=None, commit_message=None, few_shot_examples=[])
    oversized_diff = "x" * 10000

    prompt = build_system_prompt(context, diff=oversized_diff, max_tokens=10)

    assert oversized_diff in prompt
