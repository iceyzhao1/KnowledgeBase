import pytest

pytestmark = pytest.mark.asyncio


async def test_db_init_creates_all_tables(db):
    """All agent_llm_* tables must exist after init."""
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'agent_llm_%' ORDER BY name"
    )
    tables = [row[0] for row in await cursor.fetchall()]
    expected = [
        "agent_llm_attempts",
        "agent_llm_events",
        "agent_llm_model_calls",
        "agent_llm_prompt_templates",
        "agent_llm_requests",
        "agent_llm_results",
        "agent_llm_tasks",
    ]
    assert tables == expected


async def test_config_defaults():
    from llm_service.config import LLMServiceConfig

    cfg = LLMServiceConfig()
    assert cfg.port == 8900
    assert cfg.default_max_attempts == 3
    assert cfg.retry_backoff_base == 2.0


async def test_fastapi_app_creates():
    from llm_service.main import create_app

    app = create_app(start_worker=False)
    assert app.title == "LLM Service"
