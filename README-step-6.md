# Step 6 - Retrieval Service

This step adds the first Postgres read-side component used by the turn
loop. It exposes typed retrieval methods over the canonical graph and
wires the stub orchestrator to call them when intent needs context.

## What It Ships

```
src/flashback/
    retrieval/
        schema.py       Pydantic result models
        queries.py      literal SQL against active_* views
        voyage.py       query embedding wrapper around Voyage
        service.py      RetrievalService read-side API
    config.py           retrieval timeout/default/max limit settings
    http/app.py         retrieval singleton at app startup
    orchestrator/stub.py intent-based retrieval calls
src/sitecustomize.py    Windows selector-loop hook for uvicorn + psycopg async
```

The file paths follow the repo's existing `flashback` package layout.

## Retrieval Methods

- `search_moments(query, person_id, limit=None)`: vector similarity
  over active moments.
- `get_entities(person_id, kind=None)`: active entities, optionally by
  kind.
- `get_related_moments(entity_id, person_id, limit=None)`: active
  moments linked to an active entity by `involves`.
- `get_threads(person_id)`: active threads.
- `get_threads_for_entity(entity_id, person_id)`: active threads an
  active entity `evidences`.
- `get_threads_summary(person_id)`: v1 alias for `get_threads`.
- `get_dropped_phrases_for_session(session_id, person_id)`: active
  per-person `dropped_reference` questions. Session filtering lands
  once `motivated_by` edges exist.
- `get_session_summary(session_id)`: returns `None` until Session Wrap
  persists summaries in step 18.

## Intent Wiring

- `recall`, `clarify`, `switch`: call `search_moments`.
- `switch`: also calls `get_entities` and `get_threads` for a broader
  topic surface.
- `deepen`, `story`: skip retrieval so the stub can leave space for
  the user.

The Response Generator is still a stub, so retrieval results are logged
and discarded for now. Step 7 plugs real prompt context into this slot.

## Model Identity Filter

Similarity search filters by both `embedding_model` and
`embedding_model_version`, using the same config values as the
embedding worker. Rows embedded with a different model identity are
invisible to vector search until backfill catches them up. This keeps
invariant #3 enforceable: we never rank vectors from incompatible
embedding spaces together.

## Graceful Degradation

`VoyageQueryEmbedder` calls `voyageai.Client.embed(...,
input_type="query")` inside `asyncio.to_thread` with
`RETRIEVAL_QUERY_EMBED_TIMEOUT_SECONDS` as a hard timeout. Timeout or
SDK failure returns `None`; `search_moments` turns that into `[]`; the
orchestrator logs and continues with the stub reply.

## Running

```bash
pip install -e ".[dev]"
python -m pytest
```

To boot the service:

```bash
export DATABASE_URL=postgresql://flashback:flashback@localhost:5432/flashback
export VALKEY_URL=redis://localhost:6379/0
export SERVICE_TOKEN=changeme
export VOYAGE_API_KEY=pa-...
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...

uvicorn flashback.http.app:create_app --factory
```

On Windows local shells, the editable `src/sitecustomize.py` hook gives
psycopg's async pool the selector event loop it requires before uvicorn
starts.

## Verified

- [x] Retrieval result models cover moments, entities, threads,
      dropped phrases, and future session summaries.
- [x] Retrieval SQL uses `active_*` views and person-scoped filters.
- [x] Moment vector search filters by embedding model and version.
- [x] Voyage query embedding tests cover success, timeout, exception,
      and `input_type="query"`.
- [x] `/turn` integration tests cover recall retrieval, deepen skip,
      switch fan-out, and graceful degradation.
- [x] Live `uvicorn flashback.http.app:create_app --factory` smoke test
      reached app startup against local Docker Valkey/Postgres.

## Deviations

- **Package layout:** implemented under `src/flashback/...`, matching
  the repo introduced in step 3, rather than prompt shorthand
  `src/...`.
- **Session summary:** kept as the documented `None` placeholder and
  retained a no-row SQL constant for future parity, but no query runs
  until step 18 creates the persistence surface.
- **Vector search plan:** materialized the person/model-scoped
  candidates before ordering by cosine distance. This keeps the
  invariant filters exact and avoids pgvector HNSW returning fewer
  post-filtered rows than the requested limit in small test datasets.
