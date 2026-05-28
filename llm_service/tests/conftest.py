import pytest

# Use auto mode for llm_service tests
pytest_plugins = []

collect_ignore_glob = []

# Single test config dict — import from test files as:
#   from llm_service.tests.conftest import TEST_CFG
TEST_CFG = {
    "host": "0.0.0.0",
    "port": 8900,
    "provider": {
        "base_url": "http://localhost:11434/v1",
        "api_key": "test-key",
        "model": "test-model",
        "headers": {},
        "timeout": 30,
        "bypass_proxy": False,
    },
    "embedding": {
        "base_url": "http://localhost:11434/v1",
        "api_key": "test-key",
        "model": "embedding-3",
        "dimensions": 1024,
    },
    "rerank": {
        "base_url": "http://localhost:11434/v1",
        "api_key": "test-key",
        "model": "rerank-pro",
    },
    "model": {
        "timeout": 60,
        "bypass_proxy": False,
        "extra_headers": {},
    },
    "worker": {
        "concurrency": 4,
        "poll_interval": 1.0,
    },
    "task": {
        "default_max_attempts": 3,
        "retry_backoff_base": 2.0,
        "retry_backoff_max": 60.0,
        "execute_timeout": 60,
        "lease_duration": 300,
        "lease_recovery_interval": 30.0,
    },
    "template": {
        "cache_ttl": 300.0,
    },
}


def pytest_configure(config):
    """Override asyncio mode for llm_service tests."""
    config.option.asyncio_mode = "auto"


class _FakePool:
    """Minimal in-memory fake pool for unit tests that don't need real DB."""

    def __init__(self):
        self._data: dict = {}
        self._closed = False

    class _FakeConn:
        def __init__(self, pool_data):
            self._data = pool_data
            self._transaction_stack = 0

        async def execute(self, sql, params=()):
            return None

        def cursor(self):
            return self

        async def fetchone(self):
            return None

        async def fetchall(self):
            return []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class _FakeTransaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def open(self):
        pass

    async def close(self):
        self._closed = True

    def connection(self):
        return self._FakeConn(self._data)


class MockDB:
    """Mock database for tests. Provides just enough to satisfy type checks."""

    def __init__(self):
        self.pool = _FakePool()

    async def execute(self, sql, params=()):
        pass

    async def fetchone(self, sql, params=()):
        return None

    async def fetchall(self, sql, params=()):
        return []

    async def commit(self):
        pass

    async def open(self):
        pass

    async def close(self):
        pass


@pytest.fixture
def db():
    """Create a mock database for unit tests."""
    return MockDB()


@pytest.fixture
def config():
    return TEST_CFG


def _mock_provider():
    from llm_service.providers.mock import MockProvider

    return MockProvider(
        responses=[{"choices": [{"message": {"content": '{"answer": 42}'}}]}]
    )


def _mock_model_provider():
    class MockModelProvider:
        async def embed(self, texts, *, model=None, dimensions=None):
            return {
                "model": model or "embedding-3",
                "data": [
                    {"index": idx, "embedding": [float(idx + 1), float(len(text))]}
                    for idx, text in enumerate(texts)
                ],
                "usage": {"prompt_tokens": sum(len(text) for text in texts)},
            }

        async def rerank(self, query, documents, *, model=None, top_n=None):
            limit = top_n or len(documents)
            results = [
                {
                    "index": idx,
                    "relevance_score": float(limit - idx) / float(limit),
                    "document": documents[idx],
                }
                for idx in range(min(limit, len(documents)))
            ]
            return {
                "model": model or "rerank",
                "results": results,
            }

    return MockModelProvider()


@pytest.fixture
async def api_client():
    """HTTP client pointing at a test ASGI app with MockProvider.

    Note: This requires a running PostgreSQL instance with test config.
    For pure unit tests, use the `db` fixture with MockDB instead.
    """
    pytest.skip("Integration tests require running PostgreSQL — skipped in unit test mode")
