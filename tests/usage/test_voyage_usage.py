from types import SimpleNamespace


def test_sync_query_embedder_records_embedding_query(monkeypatch):
    from flashback.workers.extraction import voyage_query

    calls = {}
    monkeypatch.setattr(
        voyage_query.usage_recorder, "record_llm_usage_sync",
        lambda **kw: calls.update(kw),
    )
    embedder = voyage_query.SyncVoyageQueryEmbedder(
        model="voyage-3-large", timeout=5.0, api_key="x")
    monkeypatch.setattr(embedder, "_get_client", lambda: SimpleNamespace(
        embed=lambda *a, **k: SimpleNamespace(
            embeddings=[[0.0] * 1024], total_tokens=42)))

    vec = embedder.embed("hello")
    assert vec is not None and len(vec) == 1024
    assert calls["feature"] == "embedding_query"
    assert calls["provider"] == "voyage"
    assert calls["model"] == "voyage-3-large"
    assert calls["input_tokens"] == 42
    assert calls["output_tokens"] == 0


def test_batch_embedder_records_embedding_row(monkeypatch):
    from flashback.workers.embedding import voyage_client

    calls = {}
    monkeypatch.setattr(
        voyage_client.usage_recorder, "record_llm_usage_sync",
        lambda **kw: calls.update(kw),
    )
    client = voyage_client.VoyageClient(api_key="x")
    monkeypatch.setattr(client, "_get_client", lambda: SimpleNamespace(
        embed=lambda *a, **k: SimpleNamespace(
            embeddings=[[0.0] * 1024, [0.0] * 1024], total_tokens=99)))

    vectors = client.embed_batch(["a", "b"], model="voyage-3-large")
    assert len(vectors) == 2
    assert calls["feature"] == "embedding_row"
    assert calls["provider"] == "voyage"
    assert calls["model"] == "voyage-3-large"
    assert calls["input_tokens"] == 99
