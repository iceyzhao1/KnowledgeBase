from __future__ import annotations

import ast
from pathlib import Path


SOURCE_PATH = Path(__file__).resolve().parents[1] / "knowledge_mining/mining/api/routes/runs.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


SCOPED_RUN_HANDLERS = {
    "get_run",
    "get_run_stages",
    "get_run_documents",
    "get_run_progress",
    "get_run_document",
    "get_run_document_stages",
    "get_run_document_artifacts",
    "get_run_document_segments",
    "get_run_document_units",
    "get_run_document_relations",
    "get_run_artifacts",
    "cancel_run",
    "publish_run",
    "get_run_trace",
    "resume_run",
}


def _async_functions() -> dict[str, ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in TREE.body
        if isinstance(node, ast.AsyncFunctionDef)
    }


def test_create_and_list_require_domain():
    assert "domain: str\n" in SOURCE or "domain: str = Field(" in SOURCE
    list_node = _async_functions()["list_runs"]
    assert "domain" in {arg.arg for arg in list_node.args.args}
    list_source = ast.get_source_segment(SOURCE, list_node) or ""
    assert 'conds: list[str] = ["domain = %s"]' in list_source
    assert "require_domain(domain)" in list_source


def test_run_guard_matches_id_and_domain():
    assert "async def _require_run_domain(" in SOURCE
    assert "WHERE id = %s AND domain = %s" in SOURCE


def test_every_run_resource_handler_requires_domain_and_guard():
    functions = _async_functions()
    assert SCOPED_RUN_HANDLERS <= set(functions)

    for name in sorted(SCOPED_RUN_HANDLERS):
        node = functions[name]
        assert "domain" in {arg.arg for arg in node.args.args}, name
        function_source = ast.get_source_segment(SOURCE, node) or ""
        assert "_require_run_domain(" in function_source, name


def test_publish_and_resume_cannot_override_the_validated_domain():
    functions = _async_functions()
    for name, assignment in (
        ("publish_run", "publish_domain = require_domain(domain)"),
        ("resume_run", "resume_domain = require_domain(domain)"),
    ):
        function_source = ast.get_source_segment(SOURCE, functions[name]) or ""
        assert assignment in function_source
