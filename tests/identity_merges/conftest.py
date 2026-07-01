import pytest


@pytest.fixture(autouse=True)
def _schema(schema_applied):
    """Ensure all migrations (incl. 0035) are applied before identity-merge
    tests run. schema_applied is session-scoped; skips when no TEST_DATABASE_URL."""
    return schema_applied
