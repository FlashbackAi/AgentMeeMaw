from flashback.usage import pricing, recorder
from flashback.usage.pricing import compute_image_cost


def test_compute_image_cost_scales_with_image_count(monkeypatch):
    monkeypatch.setitem(pricing.IMAGE_PRICING, ("gemini", "img-x"), 0.04)
    assert compute_image_cost("gemini", "img-x", images=3) == 0.04 * 3
    assert compute_image_cost("gemini", "img-x", images=1) == 0.04


def test_compute_image_cost_unknown_model_returns_zero():
    assert compute_image_cost("nope", "nope", images=5) == 0.0


def test_record_image_usage_sync_builds_images_row(monkeypatch):
    monkeypatch.setitem(pricing.IMAGE_PRICING, ("gemini", "img-x"), 0.04)
    captured = {}
    monkeypatch.setattr(recorder, "insert_event", lambda row: captured.update(row) or "id")

    recorder.record_image_usage_sync(
        feature="storybook_image", provider="gemini", model="img-x", images=2,
    )
    assert captured["service"] == "agent"
    assert captured["feature"] == "storybook_image"
    assert captured["unit_type"] == "images"
    assert captured["units"] == 2
    assert captured["input_tokens"] == 0 and captured["output_tokens"] == 0
    assert captured["cost_usd"] == 0.04 * 2


def test_record_image_usage_never_raises_without_db(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    recorder.reset_pool_for_tests()
    # Silent no-op, not an exception (soft-fail contract).
    recorder.record_image_usage_sync(
        feature="tribute_image", provider="gemini",
        model="gemini-3.1-flash-image", images=1,
    )
