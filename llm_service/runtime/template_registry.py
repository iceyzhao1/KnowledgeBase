from __future__ import annotations

import uuid
from datetime import datetime, timezone

from llm_service.db import LlmRuntimeDB


_ALLOWED_UPDATE_COLUMNS = frozenset({
    "template_key", "template_version", "purpose", "system_prompt",
    "user_prompt_template", "expected_output_type", "output_schema_json",
    "knowledge_domain",
    "status", "metadata_json",
})


class TemplateRegistry:
    def __init__(self, db: LlmRuntimeDB):
        self._db = db

    async def create(
        self,
        template_key: str,
        template_version: str,
        purpose: str,
        user_prompt_template: str,
        expected_output_type: str,
        system_prompt: str | None = None,
        output_schema_json: str = "{}",
        status: str = "active",
        knowledge_domain: str | None = None,
    ) -> str:
        existing = await self._db.fetchone(
            """SELECT id FROM agent_llm_prompt_templates
               WHERE template_key = %s
                 AND template_version = %s
                 AND knowledge_domain IS NOT DISTINCT FROM %s
               LIMIT 1""",
            (template_key, template_version, knowledge_domain),
        )
        tpl_id = existing["id"] if existing else str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO agent_llm_prompt_templates
               (id, template_key, template_version, knowledge_domain, purpose, system_prompt, user_prompt_template,
                expected_output_type, output_schema_json, status, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET
                   template_key = EXCLUDED.template_key,
                   template_version = EXCLUDED.template_version,
                   knowledge_domain = EXCLUDED.knowledge_domain,
                   purpose = EXCLUDED.purpose,
                   system_prompt = EXCLUDED.system_prompt,
                   user_prompt_template = EXCLUDED.user_prompt_template,
                   expected_output_type = EXCLUDED.expected_output_type,
                   output_schema_json = EXCLUDED.output_schema_json,
                   status = EXCLUDED.status""",
            (
                tpl_id, template_key, template_version, knowledge_domain, purpose, system_prompt,
                user_prompt_template, expected_output_type, output_schema_json, status, now,
            ),
        )
        return tpl_id

    async def get(self, tpl_id: str) -> dict | None:
        return await self._db.fetchone("SELECT * FROM agent_llm_prompt_templates WHERE id = %s", (tpl_id,))

    async def get_by_key(self, template_key: str, knowledge_domain: str | None = None) -> dict | None:
        if knowledge_domain:
            return await self._db.fetchone(
                """SELECT * FROM agent_llm_prompt_templates
                   WHERE template_key = %s
                     AND status = 'active'
                     AND (knowledge_domain = %s OR knowledge_domain IS NULL)
                   ORDER BY
                     CASE WHEN knowledge_domain = %s THEN 0 ELSE 1 END,
                     created_at DESC
                   LIMIT 1""",
                (template_key, knowledge_domain, knowledge_domain),
            )
        return await self._db.fetchone(
            """SELECT * FROM agent_llm_prompt_templates
               WHERE template_key = %s
                 AND status = 'active'
                 AND knowledge_domain IS NULL
               ORDER BY created_at DESC
               LIMIT 1""",
            (template_key,),
        )

    async def list_all(self, knowledge_domain: str | None = None) -> list[dict]:
        if knowledge_domain:
            return await self._db.fetchall(
                """SELECT * FROM agent_llm_prompt_templates
                   WHERE knowledge_domain = %s OR knowledge_domain IS NULL
                   ORDER BY
                     CASE WHEN knowledge_domain = %s THEN 0 ELSE 1 END,
                     created_at DESC""",
                (knowledge_domain, knowledge_domain),
            )
        return await self._db.fetchall(
            "SELECT * FROM agent_llm_prompt_templates ORDER BY created_at DESC"
        )

    async def update(self, tpl_id: str, **fields) -> None:
        # Safe: column names validated against static allowlist, not user input.
        sets = []
        values = []
        for k, v in fields.items():
            if k not in _ALLOWED_UPDATE_COLUMNS:
                raise ValueError(f"invalid column: {k}")
            sets.append(f"{k} = %s")
            values.append(v)
        if not sets:
            return
        values.append(tpl_id)
        await self._db.execute(
            f"UPDATE agent_llm_prompt_templates SET {', '.join(sets)} WHERE id = %s",
            tuple(values),
        )

    async def archive(self, tpl_id: str) -> None:
        await self.update(tpl_id, status="archived")
