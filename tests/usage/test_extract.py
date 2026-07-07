from types import SimpleNamespace

from flashback.usage.extract import usage_from_anthropic, usage_from_openai


def test_usage_from_anthropic_reads_all_buckets():
    resp = SimpleNamespace(usage=SimpleNamespace(
        input_tokens=100, output_tokens=50,
        cache_read_input_tokens=200, cache_creation_input_tokens=30,
    ))
    assert usage_from_anthropic(resp) == {
        "input_tokens": 100, "output_tokens": 50,
        "cache_read_tokens": 200, "cache_write_tokens": 30,
    }


def test_usage_from_openai_reads_prompt_and_cached():
    resp = SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=100, completion_tokens=50,
        prompt_tokens_details=SimpleNamespace(cached_tokens=40),
    ))
    out = usage_from_openai(resp)
    # OpenAI prompt_tokens INCLUDES cached; split so cache_read is priced separately.
    assert out == {
        "input_tokens": 60, "output_tokens": 50,
        "cache_read_tokens": 40, "cache_write_tokens": 0,
    }


def test_extractors_are_defensive_on_missing_usage():
    empty = SimpleNamespace(usage=None)
    zero = {"input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0}
    assert usage_from_anthropic(empty) == zero
    assert usage_from_openai(empty) == zero
    assert usage_from_anthropic(SimpleNamespace()) == zero
