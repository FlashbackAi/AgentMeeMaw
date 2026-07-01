import pytest


@pytest.fixture(autouse=True)
def _schema(schema_applied):
    """Ensure the test DB has all migrations applied (incl. 0034) before any
    collaborator test runs. schema_applied is session-scoped (rebuilds once)
    and skips when TEST_DATABASE_URL is unset."""
    return schema_applied
