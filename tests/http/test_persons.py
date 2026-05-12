"""``POST /persons`` route tests.

Happy-path / DB-touching tests use ``client_with_db`` (requires
``TEST_DATABASE_URL``); validation, auth, and idempotency-edge tests
use the fast in-memory ``client`` fixture so they run without
Postgres.
"""

from __future__ import annotations

from uuid import UUID

from tests.http.conftest import auth_headers


def person_payload(**overrides):
    payload = {
        "name": "Maya",
        "relationship": "daughter",
        "contributor_display_name": "Sarah",
    }
    payload.update(overrides)
    return payload


# --- Happy path / DB-touching ----------------------------------------------


class TestCreateHappyPath:
    async def test_creates_row_with_starter_defaults(
        self, client_with_db, async_db_pool
    ):
        resp = await client_with_db.post(
            "/persons",
            headers=auth_headers(),
            json=person_payload(name="Maya Patel"),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Response shape is exactly what the spec requires.
        assert set(body.keys()) == {
            "person_id",
            "name",
            "relationship",
            "phase",
            "created_at",
        }
        assert body["name"] == "Maya Patel"
        assert body["relationship"] == "daughter"
        assert body["phase"] == "starter"
        person_id = UUID(body["person_id"])  # raises if not a valid UUID

        # Persisted state mirrors the cold-start defaults from CLAUDE.md s6.
        async with async_db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT name, relationship, phase, coverage_state,
                           phase_locked_at, moments_at_last_thread_run,
                           profile_summary, image_url, thumbnail_url,
                           generation_prompt
                    FROM persons WHERE id = %s
                    """,
                    (str(person_id),),
                )
                row = await cur.fetchone()

        assert row is not None
        (
            name,
            relationship,
            phase,
            coverage,
            locked_at,
            thread_runs,
            profile_summary,
            image_url,
            thumbnail_url,
            generation_prompt,
        ) = row
        assert name == "Maya Patel"
        assert relationship == "daughter"
        assert phase == "starter"
        assert coverage == {
            "sensory": 0,
            "voice": 0,
            "place": 0,
            "relation": 0,
            "era": 0,
        }
        assert locked_at is None
        assert thread_runs == 0
        assert profile_summary is None
        assert image_url is None
        assert thumbnail_url is None
        assert generation_prompt is None

    async def test_strips_whitespace_before_insert(
        self, client_with_db, async_db_pool
    ):
        resp = await client_with_db.post(
            "/persons",
            headers=auth_headers(),
            json=person_payload(
                name="  Robert Smith  ",
                relationship=" father ",
                contributor_display_name=" Sarah ",
            ),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Robert Smith"
        assert body["relationship"] == "father"

    async def test_two_legacies_with_same_name_both_succeed(
        self, client_with_db
    ):
        # The duplicate is intentional -- two contributors creating
        # two unrelated "Robert Smith" legacies must both succeed.
        first = await client_with_db.post(
            "/persons",
            headers=auth_headers(),
            json=person_payload(name="Robert Smith", relationship="son"),
        )
        second = await client_with_db.post(
            "/persons",
            headers=auth_headers(),
            json=person_payload(name="Robert Smith", relationship="daughter"),
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["person_id"] != second.json()["person_id"]


# --- Idempotency (DB-touching) ---------------------------------------------


class TestIdempotency:
    async def test_same_key_returns_cached_response(self, client_with_db):
        headers = {**auth_headers(), "Idempotency-Key": "onboarding-attempt-1"}
        body = person_payload()

        first = await client_with_db.post("/persons", headers=headers, json=body)
        second = await client_with_db.post("/persons", headers=headers, json=body)

        assert first.status_code == 200
        assert second.status_code == 200
        # Same response means the second call returned the cached row,
        # not a fresh insert.
        assert first.json() == second.json()


# --- Idempotency edges (no DB needed) --------------------------------------


class TestIdempotencyEdges:
    async def test_in_flight_conflict_returns_409(self, client, fake_redis):
        # Pre-seed the in-flight lock so the next call's NX SET fails
        # without actually racing.
        key = "stuck-key"
        lock_key = f"http:idempotency:person_create:{key}:lock"
        await fake_redis.set(lock_key, "1", ex=120)

        resp = await client.post(
            "/persons",
            headers={**auth_headers(), "Idempotency-Key": key},
            json=person_payload(),
        )
        assert resp.status_code == 409


# --- Validation (no DB needed) ---------------------------------------------


class TestValidation:
    async def test_missing_name_is_422(self, client):
        resp = await client.post(
            "/persons",
            headers=auth_headers(),
            json={
                "relationship": "daughter",
                "contributor_display_name": "Sarah",
            },
        )
        assert resp.status_code == 422

    async def test_missing_relationship_is_422(self, client):
        resp = await client.post(
            "/persons",
            headers=auth_headers(),
            json={
                "name": "Maya",
                "contributor_display_name": "Sarah",
            },
        )
        assert resp.status_code == 422

    async def test_missing_contributor_display_name_is_422(self, client):
        resp = await client.post(
            "/persons",
            headers=auth_headers(),
            json={"name": "Maya", "relationship": "daughter"},
        )
        assert resp.status_code == 422

    async def test_blank_name_is_422(self, client):
        resp = await client.post(
            "/persons",
            headers=auth_headers(),
            json=person_payload(name="   "),
        )
        assert resp.status_code == 422

    async def test_blank_relationship_is_422(self, client):
        resp = await client.post(
            "/persons",
            headers=auth_headers(),
            json=person_payload(relationship="  "),
        )
        assert resp.status_code == 422

    async def test_blank_contributor_display_name_is_422(self, client):
        resp = await client.post(
            "/persons",
            headers=auth_headers(),
            json=person_payload(contributor_display_name="  "),
        )
        assert resp.status_code == 422

    async def test_oversized_name_is_422(self, client):
        resp = await client.post(
            "/persons",
            headers=auth_headers(),
            json=person_payload(name="x" * 201),
        )
        assert resp.status_code == 422

    async def test_oversized_relationship_is_422(self, client):
        resp = await client.post(
            "/persons",
            headers=auth_headers(),
            json=person_payload(relationship="x" * 81),
        )
        assert resp.status_code == 422

    async def test_oversized_contributor_display_name_is_422(self, client):
        resp = await client.post(
            "/persons",
            headers=auth_headers(),
            json=person_payload(contributor_display_name="x" * 65),
        )
        assert resp.status_code == 422

    async def test_extra_field_is_422(self, client):
        # CLAUDE.md s1 forbids DOB/DOD on persons; the model uses
        # ``extra='forbid'`` so unknown keys are rejected.
        resp = await client.post(
            "/persons",
            headers=auth_headers(),
            json={**person_payload(), "dob": "1950-01-01"},
        )
        assert resp.status_code == 422


# --- Auth ------------------------------------------------------------------


class TestAuth:
    async def test_missing_service_token_is_401(self, client):
        resp = await client.post(
            "/persons",
            json=person_payload(),
        )
        assert resp.status_code == 401

    async def test_wrong_service_token_is_401(self, client):
        resp = await client.post(
            "/persons",
            headers={"X-Service-Token": "wrong"},
            json=person_payload(),
        )
        assert resp.status_code == 401
