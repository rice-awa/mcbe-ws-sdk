"""mcbe-ws-sdk test configuration."""

import pytest

# Default to auto so every ``async def test_*`` runs without an explicit
# ``@pytest.mark.asyncio`` annotation. Individual tests may still opt in to
# ``strict`` mode marks where they care.
pytest_plugins = ("pytest_asyncio",)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"
