from flashback.usage import pricing
from flashback.usage.pricing import ModelRate, compute_cost


def test_compute_cost_sums_each_bucket_at_its_rate(monkeypatch):
    monkeypatch.setitem(
        pricing.PRICING, ("test", "m1"),
        ModelRate(input_per_mtok=10.0, output_per_mtok=20.0,
                  cache_read_per_mtok=1.0, cache_write_per_mtok=12.5),
    )
    # 1M input @10 + 1M output @20 + 1M cache_read @1 + 1M cache_write @12.5
    cost = compute_cost(
        "test", "m1",
        input_tokens=1_000_000, output_tokens=1_000_000,
        cache_read_tokens=1_000_000, cache_write_tokens=1_000_000,
    )
    assert cost == 10.0 + 20.0 + 1.0 + 12.5


def test_compute_cost_scales_with_token_count(monkeypatch):
    monkeypatch.setitem(
        pricing.PRICING, ("test", "m2"),
        ModelRate(input_per_mtok=3.0, output_per_mtok=15.0,
                  cache_read_per_mtok=0.3, cache_write_per_mtok=3.75),
    )
    cost = compute_cost("test", "m2", input_tokens=500_000, output_tokens=100_000)
    assert cost == 3.0 * 0.5 + 15.0 * 0.1


def test_unknown_model_returns_zero_and_does_not_raise():
    assert compute_cost("nope", "nope", input_tokens=1000, output_tokens=1000) == 0.0
