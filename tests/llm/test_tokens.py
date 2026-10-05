from app.llm.tokens import approximate_token_count


def test_empty_string_has_zero_tokens():
    assert approximate_token_count("") == 0


def test_short_text_has_at_least_one_token():
    assert approximate_token_count("hi") == 1


def test_token_count_scales_with_length():
    short = approximate_token_count("a" * 40)
    long = approximate_token_count("a" * 400)
    assert long > short
