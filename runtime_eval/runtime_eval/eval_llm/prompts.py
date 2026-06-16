"""Prompt templates + JSON schemas for the operator (generate) and judge roles.

These are pure string builders with no web / IO dependency so they can be unit
tested directly and reused by the service layer.
"""

from __future__ import annotations

from ..shared.models import QuestionType

# --- generation (frontline operator persona) -------------------------------

OPERATOR_SYSTEM = """\
你是一名云核心网（5G Core）一线运维操作员。你日常通过一个知识库问答 Agent
查资料、排故障、做配置。现在请你阅读给定的一份知识库文档，站在"一线操作员真实
会问什么"的角度出题，用于测试这个问答 Agent 的回答质量。

要求：
1. 只依据给定文档内容出题，期望答案必须能在文档中找到依据，不要杜撰文档外的事实。
2. 问题要像真实操作员的口吻（口语化、目标导向），不要像考试填空题。
3. 每道题给出：问题、期望答案、评分要点(key_points)、黄金证据事实全集
   (expected_evidence)、黄金关键实体(expected_entities)、答案在文档中的出处(section/quote)。
4. 期望答案应简明准确，覆盖关键参数/步骤/结论。
5. expected_evidence 是"回答这道题所必须命中的关键事实/证据片段"的**完整集合**——
   即正确答案依据的每一条事实都要列出，缺一不可（用于检索层 Recall/Context Recall）。
   每条尽量取文档中可核对的短句/子串，互不重复；按重要性排列。
6. expected_entities 是回答该题应当出现的关键命名实体（功能名、参数名、命令、表项、
   协议、阈值等），用于实体级匹配。
7. 严格按要求的 JSON schema 输出，不要输出任何额外解释文字。
"""

TYPE_GUIDE = {
    QuestionType.FACTOID: "事实型：某个参数取值、阈值、定义、字段含义等可直接核对的事实。",
    QuestionType.CONCEPTUAL: "概念型：某功能/机制是什么、原理、作用、与其它概念的关系。",
    QuestionType.PROCEDURAL: "操作型：如何配置/开启/修改某项，期望给出有序操作步骤。",
    QuestionType.CONSTRAINT: "约束型：使用某功能的限制/前提/约束条件/取值边界（如必须满足、上限）。",
    QuestionType.TROUBLESHOOTING: "故障型：出现某报错/异常时如何定位与处理。",
    QuestionType.NAVIGATIONAL: "导航型：某内容在哪份文档/哪一节，用于定位资料位置。",
}


def build_generate_prompt(
    *,
    document_text: str,
    doc_ref: str,
    types: list[QuestionType],
    per_type: int,
    persona: str | None = None,
) -> tuple[str, str]:
    """Return (system, user) prompts for case generation.

    ``persona`` optionally overrides the default frontline-operator system prompt
    so the framework stays general across domains.
    """

    system = persona or OPERATOR_SYSTEM
    type_lines = "\n".join(
        f"- {t.value}（生成 {per_type} 道）：{TYPE_GUIDE[t]}" for t in types
    )
    schema = (
        '{\n'
        '  "cases": [\n'
        '    {\n'
        '      "question": "string",\n'
        '      "question_type": "factoid|conceptual|procedural|constraint|troubleshooting|navigational",\n'
        '      "expected_answer": "string",\n'
        '      "key_points": ["string", ...],\n'
        '      "expected_evidence": ["string", ...],\n'
        '      "expected_entities": ["string", ...],\n'
        '      "source": {"doc": "' + doc_ref + '", "section": "string|null", "quote": "string|null"},\n'
        '      "difficulty": "easy|normal|hard"\n'
        '    }\n'
        '  ]\n'
        '}'
    )
    user = (
        "MODE: GENERATE\n"
        f"FILE: {doc_ref}\n\n"
        "请为以下问题类型各出题：\n"
        f"{type_lines}\n\n"
        "输出 JSON schema：\n"
        f"{schema}\n\n"
        "==== 文档内容开始 ====\n"
        f"{document_text}\n"
        "==== 文档内容结束 ===="
    )
    return system, user


# --- judging ---------------------------------------------------------------

JUDGE_SYSTEM = """\
你是一名严谨的知识库问答评测员。你会拿到一道题、标准期望答案、评分要点，以及被测
Agent 的实际回答。请判断被测回答相对期望答案是否正确、完整。

评分标准（score 取 0.0–1.0）：
- 1.0 完全正确且覆盖全部关键要点；
- 0.6–0.9 主体正确但漏掉部分要点或表述不够完整；
- 0.1–0.5 部分相关但关键信息错误或缺失较多；
- 0.0 完全错误、答非所问或无有效内容。

verdict 取值：correct(>=0.8) / partial(0<score<0.8) / incorrect(score==0)。
只依据"是否与期望答案/要点一致"评分，不要因为措辞不同而扣分。
严格按 JSON schema 输出，不要输出额外解释。
"""

JUDGE_SCHEMA = (
    '{\n'
    '  "verdict": "correct|partial|incorrect",\n'
    '  "score": 0.0,\n'
    '  "rationale": "string",\n'
    '  "covered_points": ["string", ...],\n'
    '  "missed_points": ["string", ...]\n'
    '}'
)


def build_judge_prompt(
    *,
    question: str,
    question_type: str,
    expected_answer: str,
    key_points: list[str],
    agent_answer: str,
) -> tuple[str, str]:
    """Return (system, user) prompts for judging a single answer."""

    key_lines = "\n".join(f"- {p}" for p in key_points) or "（无显式要点）"
    user = (
        "MODE: JUDGE\n"
        f"QUESTION: {question}\n"
        f"QUESTION_TYPE: {question_type}\n"
        f"AGENT_ANSWER: {agent_answer}\n"
        f"EXPECTED_ANSWER: {expected_answer}\n"
        f"KEY_POINTS:\n{key_lines}\n\n"
        "请按以下 JSON schema 输出评分：\n"
        f"{JUDGE_SCHEMA}"
    )
    return JUDGE_SYSTEM, user


# --- L2 答案对照层：基于证据包整合最终答案（作答） -------------------------

ANSWER_SYSTEM = """\
你是一名云核心网（5G Core）知识库问答助手。下面会给你一道运维人员的问题，以及知识库
为这道题检索回来的若干证据片段。请**只依据这些证据片段**整合出一份准确、完整、条理
清晰的最终答案，直接回答问题。

要求：
1. 只用给定证据作答，证据没提到的不要凭空编造、不要外部补充。
2. 答案要正面回应问题，覆盖关键参数/步骤/结论；该给步骤就分步给。
3. 用中文，简明专业，不要复述"根据证据片段"之类的套话，直接给结论。
4. 若证据不足以回答，明确说明证据中缺少哪部分信息，不要硬编。
5. 只输出答案正文，不要输出 JSON、不要列出引用编号。
"""


def build_answer_prompt(
    *,
    question: str,
    evidence: list[str],
) -> tuple[str, str]:
    """Return (system, user) prompts for L2 answering off a fixed evidence pack.

    证据包是 L1 检索回来、L2 焊死不动的输入；这里让作答模型只读它整合最终答案，
    不再检索（纯 completion，无工具）。证据 1-based 列出，便于模型组织条理。
    """

    ev_lines = "\n".join(f"{i + 1}. {e}" for i, e in enumerate(evidence)) or "（无证据片段）"
    user = (
        "MODE: ANSWER\n"
        f"QUESTION: {question}\n\n"
        "==== 证据片段（EVIDENCE，知识库检索回来的固定输入）====\n"
        f"{ev_lines}\n\n"
        "请只依据以上证据，整合出回答该问题的最终答案："
    )
    return ANSWER_SYSTEM, user


# --- L2 答案对照裁判（答案 A vs 黄金最终答案 G） ----------------------------

ANSWER_MATCH_JUDGE_SYSTEM = """\
你是一名严谨的「答案对照」裁判。打分锚点是**黄金最终答案 G**（标准答案 + 评分要点），
不是证据。你会拿到一道题、黄金最终答案 G、G 的关键要点，以及某个模型整合出的答案 A。
请只比较 A 与 G，一次给出三组判定：

A. 覆盖（防漏）：逐条看 G 的关键要点，A 说到了的放 covered_points，漏掉的放
   missed_points。判断按语义蕴含，不要因措辞不同而算漏。
B. 准确（防掺水）：把 A 拆成若干独立论断，逐条判断 G 支不支持：
   - G 明确支持/能从 G 推出 → 放 correct_claims；
   - G 没提到、属于多余/跑题的补充 → 放 extra_claims（拉低准确度，但不是错）。
C. 矛盾（防硬伤）：A 里**与 G 直接说反**的论断（不是没提，是说错），放 contradictions。
   矛盾比漏更严重，要单独列清楚。

只依据 A 与 G 的语义关系判断。严格按 JSON schema 输出，不要输出额外解释文字。
"""

ANSWER_MATCH_JUDGE_SCHEMA = (
    '{\n'
    '  "covered_points": ["string", ...],   // G 的要点里 A 命中的\n'
    '  "missed_points": ["string", ...],     // G 的要点里 A 漏掉的\n'
    '  "correct_claims": ["string", ...],    // A 的论断里 G 支持的\n'
    '  "extra_claims": ["string", ...],      // A 的论断里 G 没提到的（多余/跑题）\n'
    '  "contradictions": ["string", ...],    // A 里与 G 直接说反的（硬伤）\n'
    '  "rationale": "string"\n'
    '}'
)


def build_answer_match_judge_prompt(
    *,
    question: str,
    expected_answer: str,
    key_points: list[str],
    answer: str,
) -> tuple[str, str]:
    """Return (system, user) prompts for comparing answer A against gold answer G."""

    key_lines = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(key_points)) or "（无显式要点）"
    user = (
        "MODE: ANSWER_MATCH_JUDGE\n"
        f"QUESTION: {question}\n\n"
        "==== 黄金最终答案 G（GOLD ANSWER）====\n"
        f"{expected_answer}\n\n"
        "==== 黄金要点（KEY POINTS）====\n"
        f"{key_lines}\n\n"
        "==== 被测模型答案 A（MODEL ANSWER）====\n"
        f"{answer}\n\n"
        "请按以下 JSON schema 输出对照判定：\n"
        f"{ANSWER_MATCH_JUDGE_SCHEMA}"
    )
    return ANSWER_MATCH_JUDGE_SYSTEM, user


# --- retrieval relevance judging (检索层：相关性裁判) -----------------------

RETRIEVAL_JUDGE_SYSTEM = """\
你是一名严谨的检索结果相关性评测员。你会拿到一道题、它的标准答案、一组"黄金证据
事实"（gold facts，即正确答案应当依据的关键事实），以及被测检索器按排名返回的若干
证据条目（retrieved items）。请完成两件事：

A. 给每个检索条目打一个相关性等级 grade（0–3）：
   - 3 高度相关：直接、完整地支撑回答该问题所需的关键事实；
   - 2 相关：包含与问题直接相关的有用信息，但不完整；
   - 1 弱相关：沾边/同主题，但对回答该问题帮助很小；
   - 0 不相关：与问题无关或答非所问。
B. 对每条"黄金证据事实"，判断它最早被哪一个检索条目覆盖（语义蕴含即可，不要求字面
   完全一致），给出该条目的 1-based 序号 covered_by；若没有任何条目覆盖该事实，
   covered_by 填 0。

只依据语义是否相关/是否覆盖来判断，不要因措辞不同而扣分。
严格按 JSON schema 输出，不要输出任何额外解释文字。
"""

RETRIEVAL_JUDGE_SCHEMA = (
    '{\n'
    '  "items": [ {"index": 1, "grade": 0}, ... ],   // index 为条目 1-based 序号\n'
    '  "facts": [ {"index": 1, "covered_by": 0}, ... ], // index 为黄金事实 1-based 序号\n'
    '  "rationale": "string"\n'
    '}'
)


def build_retrieval_judge_prompt(
    *,
    question: str,
    expected_answer: str,
    gold_facts: list[str],
    items: list[str],
) -> tuple[str, str]:
    """Return (system, user) prompts for judging one case's retrieved evidence.

    ``gold_facts`` and ``items`` are plain strings; both are presented 1-based so
    the model's ``index`` / ``covered_by`` references map cleanly back by position.
    """

    fact_lines = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(gold_facts)) or "（无黄金事实）"
    item_lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(items)) or "（无检索条目）"
    user = (
        "MODE: RETRIEVAL_JUDGE\n"
        f"QUESTION: {question}\n"
        f"EXPECTED_ANSWER: {expected_answer}\n\n"
        "==== 黄金证据事实（GOLD FACTS）====\n"
        f"{fact_lines}\n\n"
        "==== 被测检索条目（RETRIEVED ITEMS，按排名）====\n"
        f"{item_lines}\n\n"
        "请按以下 JSON schema 输出评判：\n"
        f"{RETRIEVAL_JUDGE_SCHEMA}"
    )
    return RETRIEVAL_JUDGE_SYSTEM, user
