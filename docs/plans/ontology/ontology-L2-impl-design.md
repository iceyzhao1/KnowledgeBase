# 本体概念层 — 实现设计文档 〔L2 · 蓝图〕

> 日期：2026-06-09
> 状态：实现设计蓝图（方案见 L1）
> 范围：knowledge_mining（挖掘侧）· databases（PG）· kb-ui（前端）· agent_serving（检索侧，基础消费）
> 目标读者：开发实现
> 复用：`docs/plans/14-mining-pipeline-evolution-prd.md` 的 EPIC-1/2/3/4/8 作为抽取底座

---

## 0. 文档体系（本文在 L2）

本体设计文档分三层，从宏观到微观逐层收敛，**本文是中间层 L2（实现蓝图）**：

| 层 | 文档 | 本文与它的关系 |
|---|---|---|
| **L1 北极星·方案** | `docs/plans/ontology/ontology-L1-solution-design.md` | 上游。它定"做什么/为什么/有哪些表"；本文把它的原则落成可实现的设计 |
| **L2 实现设计·蓝图**（本文） | `docs/plans/ontology/ontology-L2-impl-design.md` | 写**表的完整字段、阶段契约、存储接口、迁图风险、质量门槛规则、打分指标** |
| **L3 实施计划·落地** | `docs/plans/ontology/ontology-L3-impl-plan.md` | 下游。**分批次、文件改动、kb-ui 流程、暂停/恢复、验收**都在 L3；本文不排执行顺序 |
[ontology-L2-impl-design.md](ontology-L2-impl-design.md)
**两个上游输入**：
- **L1 方案** = "做什么/为什么"，本文实现它本体特有的部分（本体版本治理 + 出处强约束 + 人审 Gate + 进化轨 + 透明前端）。
- **管线 PRD** = 概念层抽取/归一/关系/可组合化的**通用工程机制**（schema 约束抽取、3 层归一、双向链接、pattern 关系、Stage 协议），本文**直接复用**其 EPIC-1/2/3/4/8，不重造。

一句话：**PRD 负责"抽得准"，本文负责"抽进受版本治理的本体图、带出处、可人审"；怎么分阶段把它做出来在 L3。**

> 名词不清时回看 L1 §2.3 术语（canonical 对象 / mention / 出处）。本文默认读者已读 L1。

---

## 1. MVP 范围（再收口一次）

| 维度 | MVP 取值 |
|---|---|
| 本体层 | **只做概念层**（concept）。8 类对象 + 4 类概念关系 |
| 领域 | 只做 `cloud_core_network` |
| 冷启动 | 从 `domain_cloud_core.yaml` 提取出一份**独立的本体种子文件**，引种成本体 v1（不直接读 domain.yaml，见 §4），不走"自由抽+人审定第一版" |
| 关系类型 | 先 4 种：`connects_to` / `uses_protocol` / `part_of` / `is_a`（§6.3） |
| 跑通范围 | 代表性子集：`5G核心网基础` + `SMF会话管理功能` + `UPF用户面功能` 三个主题 |
| 交互 | 全程 kb-ui：上传 → 跑挖掘 → 透明看过程 → 人审 Gate → 落图 |
| 存储 | 全 PostgreSQL，图走薄接口 |
| 检索侧 | 基础消费：实体链接 + 概念邻域 1~2 跳 + 出处回链 |

**不做**：机制/方法/条件/场景四层、跨章节对象合成、社区检测（EPIC-7）、向量归一 Tier-2 默认关闭（留开关）、专家本体文档的**上传 UI**（MVP 用本地种子文件引种，格式与将来上传一致，见 §4）。

---

## 2. 总体数据流

加入本体之后，整个挖掘过程可以这样理解。下面用两张图：**第一张只看大方向**（一共几步、循环怎么转），**第二张再钻进每一步看细节**。两张图都和实际代码一致。

### 2.1 概览图：一共五步，两个循环

```
 ┌─ 准备（只做一次）────────────────────────────┐
 │ 第 0 步：引种本体                             │
 │   把"这个领域允许有哪些类型的东西、这些东西    │
 │   之间允许有哪些关系"先写进数据库（=立规矩）。 │
 │   立好之后，以后挖掘都照这套规矩来。           │
 └──────────────────────────────────────────────┘
                  │
                  ▼
 ┌─ 每次上传文档后，走一遍下面这套 ───────────────────────────────┐
 │                                                                │
 │ 第 1 步：逐篇挖掘（切片后分两条并行线，互不依赖）              │
 │   ├─ 本体线：从每段挖"有哪些东西、东西之间什么关系"            │
 │   └─ 篇章线：挖"段落与段落之间的逻辑关系"（老功能，没改）      │
 │      两条线都挖完，再汇到一起                                  │
 │                  │                                             │
 │                  ▼                                             │
 │ 第 2 步：全局汇总                                               │
 │   整批文档都挖完后，把零散结果拼成一张知识图，                  │
 │   每一条都记着"出自哪篇文档哪句话"。                            │
 │                  │                                             │
 │                  ▼                                             │
 │ 第 3 步：人工把关（只有遇到拿不准的才触发）                     │
 │      ┌────────────────────────────────────┐                  │
 │  有疑问 ─►│ 暂停，等人在界面上一条条拍板    │──┐ 拍完点"继续"  │
 │      │   └────────────────────────────────┘  │              │
 │      │            ▲                           │              │
 │      │            └───── 还有没审完的就再停 ──┘              │
 │      │                  │ 没疑问 / 都审完了                   │
 │  没疑问 ──────────────►  ▼                                    │
 │ 第 4 步：发布上线                                              │
 └────────────────────────────────────────────────────────────────┘
                  │
                  ▼
 第 5 步：供检索使用（按一个东西找它的"邻居"，并能翻回原文）

 ※ 还有一条"慢循环"（让本体越用越厚）：
   第 3 步里，人通过的"新类型/新关系" → 升级本体版本 →
   以后再挖掘时就用更全的规矩。这样本体会随着文档越喂越完善。
```

**两个循环说明**：
- **快循环（人工把关）**：第 2 步汇总后，如果冒出"系统拿不准的东西"（不认识的新类型、或同一个东西认不准是不是同一个），就**暂停**等人拍板，拍完点"继续"接着跑；可能停两次（先审类型、再审具体对象），都审完才发布。没疑问就直接放行，不打扰人。
- **慢循环（本体进化）**：人审时通过的新类型/新关系，会让本体"升一个版本"。下一次挖掘就用更全的规矩，于是本体随着喂进来的文档越来越完善。

### 2.2 细节图：每一步具体做了什么

```
【第 0 步·引种本体】只做一次
  上传一份"规矩文件"（写明允许哪些类型、哪些关系、常见别名）
  → 系统读进数据库，建成"本体第 1 版"并设为生效版
  → 此后挖掘只看数据库里这个生效版，不再读那份文件

────────────────────────────────────────────────────────────
【第 1 步·逐篇挖掘】每篇文档先解析、切片，然后分成两条独立的线同时挖，挖完再汇合
  ① 解析 ── 把上传的文件读成规整的内容
  ② 切片 ── 把整篇文章切成一段段
            │
            │ 切完后兵分两路（两路互不依赖，各挖各的）
   ┌────────┴──────────────────────────┬──────────────────────────────────┐
 【A 本体挖掘线】                       │                      【B 篇章关系挖掘线】
  ③ 抽对象 ── 用大模型在每段里找出       │   分析段落与段落之间的逻辑关系
            "符合本体类型的东西"          │   （如"这段是上一段的举例/转折/因果"）
            （如某网元、某接口）          │   —— 老功能，本次没改动
            · 本体里没有、但看着重要的，    │
              先记下来当"候选"，不丢掉      │
  ④ 认户口 ── 把同一个东西的不同叫法       │
            认到同一个"标准户口"上          │
            （"会话管理功能"="SMF"）        │
            · 能认准的自动认；拿不准的       │
              挂起，留给后面人工确认         │
  ⑤ 抽关系 ── 同段里把对象两两看要不要连边  │
            · 过五道质检：                  │
              1) 两端都得是真抽出的对象      │
              2) 不能自己连自己             │
              3) 两类对象按本体规矩允许相连   │
              4) 关联够强（不是碰巧同段出现）│
              5) 同一事实不重复；矛盾的标出   │
                 来但不强行合并             │
            · 像有关系但不合本体规矩的       │
              也记成"候选"                  │
   └────────┬──────────────────────────┴──────────────────────────────────┘
            │ 两条线都跑完再汇合
  ⑥ 收尾 ── 生成检索用的小单元、算向量、这一篇先入库

  注：上图是两条线的"逻辑结构"——它们本来就互不依赖，所以画成并行两路。
      但当前代码里仍是排成一条直线先后跑（先 A 后 B），尚未真正同时执行；
      "执行层真正并行（分叉/汇合）"作为后续可选重构，排在 B8 检索做完之后再评估。

────────────────────────────────────────────────────────────
【第 2 步·全局汇总】整批文档都挖完后，统一做一次
  · 把每篇攒下的零散结果汇到一起
  · 算两个"只能全局算"的指标：
      - 关联强度：两个东西是真有关系，还是只是碰巧常一起出现
      - 出现篇数：一个新概念在多少篇不同文档里出现过（出现越广越可信）
  · 给每个东西建好"标准户口"
  · 把够可信的关系连成边——每条边都必须带原文出处，没出处不许入库
  · 给每个东西、每条边都记下"出自哪篇文档哪句话"，随时能翻回原文
  · 那些"候选"（新类型、新关系）攒起来，只有出现够广的才送去人审

────────────────────────────────────────────────────────────
【第 3 步·人工把关】只有攒出候选或有认不准的对象时才触发，否则跳过
  关卡一·审本体：看新冒出来的类型/关系候选，逐条 通过 / 改名 / 拒绝
                通过的，提交后会让本体升一个新版本
  关卡二·审对象：看那些认不准户口的叫法，人来定 合并到已有 / 新建 / 丢弃
  · 触发时整批挖掘会暂停（不发布），并记住停在哪一关
  · 人在界面拍完板点"继续"，系统从暂停那一关接着往下跑

────────────────────────────────────────────────────────────
【第 4 步·发布上线】组装这一批的成果 → 校验 → 正式发布（沿用老机制）

────────────────────────────────────────────────────────────
【第 5 步·供检索使用】（这一环还没接上，见下方进度）
  查询时先把问句里的词对到"标准户口"，再顺着图找它的邻居（一两跳），
  并把每条结果的原文出处一并给出。这是新增的一路通道，
  不替代原有的关键词、向量、段落关系三种检索。
```

> **实现进度**：从引种、四个新环节、到人工把关和前端界面都已经做完并跑通；**只剩第 5 步"供检索使用"还没接上**，所以上图最后一块目前是虚的。

---

## 3. 数据库表设计

> 命名约定：领域级共享表用 `ontology_*` / `domain_*`；文章级、绑定快照的用 `asset_*`，与现有 `asset_document_snapshots` / `asset_raw_segments` / `mining_runs` 对齐。所有 DDL 以 PG 为准。

### 3.1 本体规则层（domain-scoped，慢变，人审写入）

```sql
-- 本体版本快照
CREATE TABLE ontology_versions (
    id              TEXT PRIMARY KEY,            -- uuid
    domain_id       TEXT NOT NULL,               -- 'cloud_core_network'
    version_no      INTEGER NOT NULL,            -- 1,2,3...
    status          TEXT NOT NULL DEFAULT 'draft', -- draft|active|superseded
    source          TEXT NOT NULL,               -- 'bootstrap_yaml'|'human_review'
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    note            TEXT,
    UNIQUE(domain_id, version_no)
);
-- 同一 domain 同时只有一个 active

-- 节点类型（点规则），MVP 全是 layer='concept'
CREATE TABLE ontology_node_types (
    id              TEXT PRIMARY KEY,
    ontology_version_id TEXT NOT NULL REFERENCES ontology_versions(id),
    name            TEXT NOT NULL,               -- 'network_element' ...
    layer           TEXT NOT NULL DEFAULT 'concept',
    is_strong       BOOLEAN NOT NULL DEFAULT false, -- 对应 strong_entity_types
    definition      TEXT,
    examples_json   JSONB DEFAULT '[]',
    UNIQUE(ontology_version_id, name)
);

-- 关系类型（边规则），含 head/tail 类型约束
CREATE TABLE ontology_relation_types (
    id              TEXT PRIMARY KEY,
    ontology_version_id TEXT NOT NULL REFERENCES ontology_versions(id),
    name            TEXT NOT NULL,               -- 'connects_to' ...
    layer           TEXT NOT NULL DEFAULT 'concept',
    is_directed     BOOLEAN NOT NULL DEFAULT true,
    inverse_name    TEXT,
    allowed_pairs_json JSONB NOT NULL,           -- [{"head":"network_element","tail":"interface"}]
    definition      TEXT,
    UNIQUE(ontology_version_id, name)
);
```

### 3.2 本体事实层（domain-scoped，快变，机器抽 + 人审消歧）

```sql
-- canonical 对象（消歧后的领域实体档案）
CREATE TABLE ontology_entities (
    id              TEXT PRIMARY KEY,
    domain_id       TEXT NOT NULL,
    canonical_name  TEXT NOT NULL,               -- 'UPF'
    node_type       TEXT NOT NULL,               -- 'network_element'
    layer           TEXT NOT NULL DEFAULT 'concept',
    aliases_json    JSONB DEFAULT '[]',          -- ['用户面功能','User Plane Function']
    attributes_json JSONB DEFAULT '{}',
    first_ontology_version_id TEXT REFERENCES ontology_versions(id),
    mention_count   INTEGER NOT NULL DEFAULT 0,
    document_count  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(domain_id, node_type, canonical_name)
);

-- 事实边（领域级，出处强制非空）
CREATE TABLE ontology_entity_relations (
    id              TEXT PRIMARY KEY,
    domain_id       TEXT NOT NULL,
    head_entity_id  TEXT NOT NULL REFERENCES ontology_entities(id),
    tail_entity_id  TEXT NOT NULL REFERENCES ontology_entities(id),
    relation_type   TEXT NOT NULL,
    confidence      REAL NOT NULL DEFAULT 0.7,
    source_refs_json JSONB NOT NULL,             -- 出处 evidence_node_id 列表，CHECK 非空
    ontology_version_id TEXT REFERENCES ontology_versions(id),
    has_conflict    BOOLEAN NOT NULL DEFAULT false, -- 冲突只标记不合并
    CONSTRAINT chk_source_refs CHECK (jsonb_array_length(source_refs_json) > 0),
    CONSTRAINT chk_no_self CHECK (head_entity_id <> tail_entity_id)
);

-- 别名词典（消歧产出，可种子自 dictionaries/builtin_alias_hints.yaml）
CREATE TABLE ontology_alias_dictionary (
    id              TEXT PRIMARY KEY,
    domain_id       TEXT NOT NULL,
    alias_normalized TEXT NOT NULL,              -- 小写+合并空白
    canonical_name  TEXT NOT NULL,
    node_type       TEXT,
    source          TEXT,                        -- 'seed'|'human'|'auto'
    UNIQUE(domain_id, alias_normalized)
);
```

### 3.3 出处（provenance，强约束的核心）

```sql
-- 任一对象/边 → 片段 → 文档快照 的可追溯链
CREATE TABLE ontology_evidence_nodes (
    id              TEXT PRIMARY KEY,
    domain_id       TEXT NOT NULL,
    document_snapshot_id TEXT NOT NULL REFERENCES asset_document_snapshots(id),
    segment_id      TEXT NOT NULL REFERENCES asset_raw_segments(id),
    quote           TEXT,                        -- 支撑这条知识的原文片段
    -- 反向：这条 evidence 支撑了哪个对象/边（多对多，用关联或冗余字段）
    target_kind     TEXT NOT NULL,               -- 'entity'|'relation'|'mention'
    target_id       TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_ev_target ON ontology_evidence_nodes(target_kind, target_id);
CREATE INDEX idx_ev_segment ON ontology_evidence_nodes(segment_id);
```

### 3.4 文章级 mention（doc-scoped，= PRD EPIC-3 的 asset_entity_mentions 扩展）

```sql
CREATE TABLE asset_segment_entity_mentions (
    id              TEXT PRIMARY KEY,
    document_snapshot_id TEXT NOT NULL REFERENCES asset_document_snapshots(id),
    segment_id      TEXT NOT NULL REFERENCES asset_raw_segments(id),
    node_type       TEXT NOT NULL,
    mention_text    TEXT NOT NULL,               -- 原始 mention
    canonical_name  TEXT,                        -- 归一后；pending 时可空
    resolved_entity_id TEXT REFERENCES ontology_entities(id),
    resolve_status  TEXT NOT NULL DEFAULT 'pending', -- auto|human|pending
    confidence      REAL DEFAULT 1.0,
    metadata_json   JSONB DEFAULT '{}'
);
CREATE INDEX idx_mention_canon ON asset_segment_entity_mentions(canonical_name, node_type);
CREATE INDEX idx_mention_seg ON asset_segment_entity_mentions(segment_id);
CREATE INDEX idx_mention_status ON asset_segment_entity_mentions(resolve_status);
```

### 3.5 候选本体（进化轨，逃生口/归纳产出）

```sql
CREATE TABLE ontology_candidates (
    id              TEXT PRIMARY KEY,
    domain_id       TEXT NOT NULL,
    kind            TEXT NOT NULL,               -- 'node_type'|'relation_type'
    layer           TEXT NOT NULL DEFAULT 'concept',
    proposed_name   TEXT NOT NULL,
    payload_json    JSONB NOT NULL,              -- 候选定义/示例/head-tail
    source          TEXT NOT NULL,               -- 'escape_hatch'|'global_induction'
    evidence_json   JSONB DEFAULT '[]',          -- 跨文档频次/出处
    score           REAL,
    status          TEXT NOT NULL DEFAULT 'proposed', -- proposed|queued|accepted|rejected
    review_note     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_cand_status ON ontology_candidates(domain_id, status);
```

### 3.6 复用现有表

- `asset_document_snapshots` / `asset_raw_segments`：**不改结构**，新表通过外键挂接。
- `mining_runs`：复用 `status` 字段做**人审暂停/恢复**（§7），新增状态值 `awaiting_review`；并增两列（断点续跑用，落地见 L3 B6）：
  - `subloop_stage TEXT`：人审检查点（`ontology_review` / `entity_review` / `done`），恢复时据此跳转。
  - `ontology_version_id TEXT REFERENCES ontology_versions(id)`：本次 run 所用的 active 本体版本，记录抽取依据。

### 3.7 字段详解（逐字段中文对照）

> 上面 §3.1–§3.6 给的是 DDL；本节把每张表每个字段的含义用中文讲清，看图纸的人不用再问。两个贯穿概念：**领域级**=整个 cloud_core_network 共享、所有文档累积；**文章级**=绑定某一篇上传文档。

#### `ontology_versions` —— 本体的"版本快照"（像 App 版本号，抽取只认当前 active 那版）
| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | TEXT 主键 | 版本记录唯一编号（uuid） |
| `domain_id` | TEXT | 所属领域，MVP 固定 `cloud_core_network` |
| `version_no` | INTEGER | 第几版：1、2、3…… |
| `status` | TEXT | `draft`草稿 / `active`当前上线 / `superseded`已被顶替。**同领域同时只能一个 active** |
| `source` | TEXT | 来源：`bootstrap_yaml`种子引种 / `human_review`人审升版 |
| `created_by` | TEXT | 创建者（人/系统） |
| `created_at` | TIMESTAMPTZ | 创建时间 |
| `note` | TEXT | 备注（这版改了啥） |
| *约束* | UNIQUE(domain_id, version_no) | 同领域版本号不重复 |

#### `ontology_node_types` —— "点的规则"：允许有哪几类概念对象（MVP 8 类）
| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | TEXT 主键 | 唯一编号 |
| `ontology_version_id` | TEXT 外键→versions | 属于哪一版本体（换版即新一批记录） |
| `name` | TEXT | 类型名，如 `network_element`(网元)、`protocol`(协议) |
| `layer` | TEXT | 五层中的哪层，MVP 全 `concept` |
| `is_strong` | BOOLEAN | 是否强类型（核心骨架，对应 strong_entity_types）；弱类型抽取门槛更松 |
| `definition` | TEXT | 该类定义（喂 LLM 当判断标准） |
| `examples_json` | JSONB | 例子，如 `["SMF","UPF","AMF"]` |
| *约束* | UNIQUE(version_id, name) | 同版本内类型名不重复 |

#### `ontology_relation_types` —— "边的规则"：允许有哪几类关系、谁能连谁
| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | TEXT 主键 | 唯一编号 |
| `ontology_version_id` | TEXT 外键→versions | 属于哪一版本体 |
| `name` | TEXT | 关系名，如 `connects_to`、`uses_protocol` |
| `layer` | TEXT | 哪层关系，MVP `concept` |
| `is_directed` | BOOLEAN | 有无方向（`connects_to` 有向 A→B） |
| `inverse_name` | TEXT | 反向叫法（`part_of` ↔ `has_part`），方便双向查 |
| `allowed_pairs_json` | JSONB | **核心**：允许的"头类型→尾类型"配对，如 `[{"head":"interface","tail":"protocol"}]`；`*` 表任意 |
| `definition` | TEXT | 该关系含义 |
| *约束* | UNIQUE(version_id, name) | 同版本内关系名不重复 |

#### `ontology_entities` —— canonical 对象登记表（领域级"户口本"，一个真实东西只一条）
| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | TEXT 主键 | 对象唯一编号（边、mention 都指它） |
| `domain_id` | TEXT | 所属领域 |
| `canonical_name` | TEXT | 规范名（唯一标准叫法），如 `UPF` |
| `node_type` | TEXT | 哪类（对应 node_types.name） |
| `layer` | TEXT | 哪层，MVP `concept` |
| `aliases_json` | JSONB | 别名列表，如 `["用户面功能","User Plane Function"]` |
| `attributes_json` | JSONB | 额外属性（键值对），如默认值、参数范围 |
| `first_ontology_version_id` | TEXT 外键→versions | 最早在哪一版本体下建出来 |
| `mention_count` | INTEGER | 被各文档提到的总次数（打分用） |
| `document_count` | INTEGER | 被多少篇**不同**文档提到（判全域共识） |
| *约束* | UNIQUE(domain_id, node_type, canonical_name) | 同领域同类型规范名唯一（防重复建档） |

#### `ontology_entity_relations` —— 事实边（领域级关系图，每条必带出处）
| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | TEXT 主键 | 边唯一编号 |
| `domain_id` | TEXT | 所属领域 |
| `head_entity_id` | TEXT 外键→entities | 头：关系从哪个对象出发 |
| `tail_entity_id` | TEXT 外键→entities | 尾：指向哪个对象 |
| `relation_type` | TEXT | 关系类型（对应 relation_types.name） |
| `confidence` | REAL | 置信度 0~1，默认 0.7 |
| `source_refs_json` | JSONB | **出处**：支撑这条边的 ontology_evidence_nodes 编号列表 |
| `ontology_version_id` | TEXT 外键→versions | 按哪一版本体抽的 |
| `has_conflict` | BOOLEAN | 是否有矛盾说法；**只标记不合并** |
| *约束* | CHECK(source_refs 非空) | **出处强制**，没出处不许落库 |
| *约束* | CHECK(head ≠ tail) | 禁止自环 |

#### `ontology_alias_dictionary` —— 别名词典（消歧用的"小名对照表"）
| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | TEXT 主键 | 唯一编号 |
| `domain_id` | TEXT | 所属领域 |
| `alias_normalized` | TEXT | 归一化别名（转小写、合并空格，便于精确匹配） |
| `canonical_name` | TEXT | 对应的规范名 |
| `node_type` | TEXT | 别名属于哪类（可空） |
| `source` | TEXT | 来源：`seed`种子 / `human`人录 / `auto`自动 |
| *约束* | UNIQUE(domain_id, alias_normalized) | 同领域同别名只映射一处 |

#### `ontology_evidence_nodes` —— 出处节点（"一切知识带路径"的核心）
| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | TEXT 主键 | 出处唯一编号 |
| `domain_id` | TEXT | 所属领域 |
| `document_snapshot_id` | TEXT 外键→文档快照 | 来自哪篇文档（哪个快照版本） |
| `segment_id` | TEXT 外键→原始片段 | 具体来自哪一段 |
| `quote` | TEXT | 支撑这条知识的**原文摘录** |
| `target_kind` | TEXT | 支撑对象类型：`entity` / `relation` / `mention` |
| `target_id` | TEXT | 支撑的那个对象/边/mention 编号（反向指回） |
| `created_at` | TIMESTAMPTZ | 创建时间 |
| *索引* | (target_kind,target_id)、(segment_id) | 加速"某对象的出处"和"某段产出了啥" |

#### `asset_segment_entity_mentions` —— 片段→领域实体的提及链接（文章级）
| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | TEXT 主键 | 唯一编号 |
| `document_snapshot_id` | TEXT 外键→文档快照 | 属于哪篇文档 |
| `segment_id` | TEXT 外键→原始片段 | 出现在哪一段 |
| `node_type` | TEXT | 被标成哪类对象 |
| `mention_text` | TEXT | 原文实际字面，如"会话管理功能" |
| `canonical_name` | TEXT | 归一后规范名（未拍板时可空） |
| `resolved_entity_id` | TEXT 外键→entities | 最终归到哪个 canonical 对象 |
| `resolve_status` | TEXT | `auto`自动归 / `human`人确认 / `pending`待人审 |
| `confidence` | REAL | 识别置信度，默认 1.0 |
| `metadata_json` | JSONB | 附加信息（位置、上下文等） |
| *索引* | canonical_name+node_type、segment_id、resolve_status | 加速聚合 / 按段查 / 捞 pending |

#### `ontology_candidates` —— 候选类型/关系（待人审的"建议箱"，进化轨）
| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | TEXT 主键 | 唯一编号 |
| `domain_id` | TEXT | 所属领域 |
| `kind` | TEXT | 候选是 `node_type`新类型 还是 `relation_type`新关系 |
| `layer` | TEXT | 归属层，MVP `concept` |
| `proposed_name` | TEXT | 建议名字 |
| `payload_json` | JSONB | 候选详细定义/示例/head-tail 配对 |
| `source` | TEXT | 来源：`escape_hatch`抽取逃生口 / `global_induction`全局归纳 |
| `evidence_json` | JSONB | 跨文档频次、出处等证据 |
| `score` | REAL | 打分（用 §6.5 指标算，给 本体确认 排序） |
| `status` | TEXT | `proposed`待审 / `queued`排队 / `accepted`通过 / `rejected`拒绝 |
| `review_note` | TEXT | 人审备注 |
| `created_at` | TIMESTAMPTZ | 创建时间 |
| *索引* | (domain_id, status) | 加速捞某领域待审候选 |

#### `mining_runs` 新增列（复用现有表，§3.6）
| 字段 | 类型 | 含义 |
|---|---|---|
| `subloop_stage` | TEXT | 人审检查点：`ontology_review` / `entity_review` / `done`，恢复时据此跳过已完成步骤 |
| `ontology_version_id` | TEXT 外键→versions | 本次挖掘所用的 active 本体版本 |
| `status`（既有，加值） | TEXT | 新增取值 `awaiting_review`（等待人审、暂停） |

---

## 4. 冷启动引种（种子文件 → 本体 v1）

> 对应 L1 §5.1：**不直接读 `domain_cloud_core.yaml`**，而是先把它的类型/例子沉淀成一份**独立、可复用的本体种子文件**，引种读这份种子文件。理由见 L1（domain.yaml 是运行配置，不该兼任本体真相；独立种子文件与将来"专家上传的本体文档"同格式、可复用）。

### 4.1 种子文件格式（= 将来上传的本体文档格式，复用 PRD `entity_schema` 结构）

新增文件 `scenario_packs/cloud_core_network/ontology_seed/concept.yaml`（一层一个文件，MVP 只有 `concept.yaml`）：

```yaml
# 本体种子文件：cloud_core_network 概念层
layer: concept
domain: cloud_core_network
node_types:
  - name: network_element
    is_strong: true
    definition: "核心网网元功能模块"
    examples: ["SMF", "UPF", "AMF", "PCF", "UDM"]
  - name: interface
    is_strong: false
    definition: "网元间标准化接口"
    examples: ["N4", "N3", "N6", "N7", "N11"]
  - name: protocol
    is_strong: true
    examples: ["PFCP", "GTP-U", "GTP-C", "DIAMETER"]
  # ... command / parameter / alarm / feature / concept（共 8 类，来自 domain.yaml）
aliases:                          # 喂 ontology_alias_dictionary 的种子
  UPF: ["用户面功能", "User Plane Function"]
  SMF: ["会话管理功能", "Session Management Function"]
relation_types:                   # §6.3 选定的概念层关系
  - name: connects_to
    is_directed: true
    patterns:
      - {head: network_element, tail: interface}
      - {head: network_element, tail: network_element}
  - name: uses_protocol
    patterns:
      - {head: interface, tail: protocol}
      - {head: network_element, tail: protocol}
  - name: part_of
    patterns: [{head: "*", tail: "*"}]
  - name: is_a
    patterns: [{head: "*", tail: "*"}]
```

- 这份文件由"从 domain.yaml 一次性导出 + 人工补 definition/examples/aliases"生成；之后**它就是种子的真相**，domain.yaml 不再参与本体。
- 与将来"专家上传本体文档"是**同一 schema**：上传走的就是解析这套 YAML 写 `ontology_*` 表。

### 4.2 引种函数（idempotent，不进逐文档流水线）

```
bootstrap_ontology(domain_id='cloud_core_network',
                   seed_dir='scenario_packs/cloud_core_network/ontology_seed/'):
    1. 若该 domain 已有 active version → 跳过（幂等）
    2. 读 seed_dir 下所有 *.yaml（MVP 只有 concept.yaml）
    3. 建 ontology_versions(version_no=1, status='active', source='seed_file')
    4. node_types → ontology_node_types(layer, is_strong, definition, examples_json)
    5. relation_types → ontology_relation_types(allowed_pairs_json=patterns)
    6. aliases → ontology_alias_dictionary(source='seed')
```

- 入口：kb-ui 设置页"引种本体"按钮，或 CLI `python -m knowledge_mining ... bootstrap-ontology`。
- 之后真相源 = DB；抽取轨改读 active version（见 §6.1），不再读 domain.yaml 或种子文件。

---

## 5. 管线可组合化（EPIC-8，先做，作为后续插桩的地基）

按 PRD EPIC-8：定义 `PipelineStage` Protocol + `StageWrapper`，`PipelineConfig.stages` 可配置。MVP 默认阶段列表：

```
parse → segment → enrich → resolve → discourse_relations
      → entity_relations → build_retrieval_units → embedding → db_write
```

- 新增的 `resolve` / `entity_relations` 用 feature flag 控制（`RetrievalPolicy` 加开关），默认关，逐个 EPIC 上线时打开。
- 全局阶段 `graph_write` 挂在 `publishing` / build 阶段（非逐文档）。

---

## 6. 抽取轨实现

### 6.1 entity_extract：受本体约束 + 逃生口（EPIC-1 改造）

> **实现演进（2026-06-11，L4 §15/§16）**：本节原标题为 "enrich：受本体约束 + 逃生口"，实体抽取曾**寄生在 enrich 同一次 LLM 调用**。现已拆为**独立 `entity_extract` 阶段**（独立 LLM、本体类型表喂进 prompt、**双通道**输出）；`enrich` 退回篇章本职（语义角色 + 内容质量），不再抽实体。下面描述的约束/逃生口逻辑整体迁移到新阶段，落点见 L4 §16。

- **改约束来源**：`DomainProfile.entity_types` 当前从 domain.yaml 读；改为**读 active `ontology_node_types`**（薄接口 `OntologyStore.active_node_types(domain)`）。domain.yaml / 种子文件只在冷启动引种时用，运行期不读。
- **双通道 prompt**：把 active 类型表喂进去，每个概念二选一——**通道 A** 对齐已知类型（带 `confidence`）；**通道 B** 清单装不下的重要概念进 `out_of_schema`，**模型自提议新类型名 + 理由**，不许硬塞、不许丢弃。低置信的 in-schema 实体也转逃生口复核。（取代旧的"单清单 + 特异性门槛"单通道写法，避免模型把新概念硬塞旧类型导致本体永不进化。）
- **逃生口**：`_apply_entity_result` 把 `out_of_schema` 项写进段 `meta["out_of_schema"]`（`{type, name, reason}` + proposed_reason/evidence），跨文档 DF 等 B5 全局聚合后落 `ontology_candidates(kind='node_type', source='escape_hatch')`，**不进 entity_refs**。
- **噪声剪枝**：见 §6.5——孤立低频实体过滤掉，不落库。

### 6.2 resolve：3 层归一 + 人审分流（EPIC-2 改造）
- **Tier1**（默认开）：精确归一化 + `ontology_alias_dictionary` 命中 → `resolve_status='auto'`。
- **Tier2**（默认关，留开关）：向量 cosine > 阈值 → 仍标 `pending` 交人确认（**消歧由人拍板**，原则要求）。
- **Tier3 LLM**：MVP 不做。
- 未命中/不确定 → `resolve_status='pending'`，触发 **实体确认**。
- 产物：每个 mention 带 `canonical_name`(可空) + `resolve_status`。

### 6.3 entity_relations：pattern 约束抽概念关系（EPIC-4 改造）
MVP 4 种关系 + allowed_pairs：

| relation | head → tail | 含义 |
|---|---|---|
| `connects_to` | network_element → interface / network_element | 网元经接口/直接连接 |
| `uses_protocol` | interface → protocol（及 network_element → protocol） | 接口/网元使用协议 |
| `part_of` | 任意 → 任意（同层） | 从属/组成 |
| `is_a` | 任意 → 任意（同类型族） | 类属 |

- 复用 PRD EPIC-4 的 `_find_pattern_matches`（同段实体对匹配 pattern）+ 可选 LLM 生成 fact。
- **软约束（分流器）**：实体对在同段高置信共现、但 (head,relation,tail) 不在 allowed_pairs → 写 `ontology_candidates(kind='relation_type', source='escape_hatch')`，**不落事实边**。

#### 6.3.1 边的质量门槛（落实 L1 §4.4 的"可断言性测试"）
pattern 匹配只解决"类型合不合法"，**不能**解决"这俩词是不是真有关系"。落边前按顺序过五道闸，**任一不过即丢或转候选**：

| 闸 | 判据 | 量化口径（MVP） |
|---|---|---|
| 1 端点合法 | head/tail 都是本段已抽 canonical 对象 | 硬规则 |
| 2 非自环 | head ≠ tail | 硬规则 |
| 3 类型合法 | (head,rel,tail) ∈ allowed_pairs | 命中→候选落边；不命中→关系候选（软约束） |
| 4 **关系强度** | **共现 ≠ 关系**：要有关联证据 | **NPMI(head,tail) ≥ 阈值**（默认 0.3）或 LLM 判定关系成立=true；都不满足→丢 |
| 5 去冗余 | 同事实不重复落 | 同 (head,rel,tail) 去重；属性矛盾→`has_conflict=true`（不合并） |

- 第 4 闸是关键：只在同段共现不够，必须 NPMI 关联强度过阈值，或 LLM 显式确认"这句话在断言这条关系"。NPMI 定义见 §6.5。
- LLM 增强版（PRD EPIC-4 Step3）：对过了 1–3 闸的实体对，让 LLM 回 `{关系是否存在, fact 描述, 置信度}`，置信度 < 阈值（默认 0.6）丢弃。

### 6.4 graph_write：全局落图 + 出处（我们方案特有，PRD 无）
全局阶段（每个 build 跑一次）：
1. 遍历本 build 所有 `asset_segment_entity_mentions`，写齐（EPIC-3）。
2. 按 (domain, node_type, canonical_name) **聚合 upsert** `ontology_entities`，累加 mention_count / document_count。
3. 把 6.3 的局部边对齐到 canonical 对象，**去重后 upsert** `ontology_entity_relations`。
4. **出处强制**：每个新 entity / relation / mention 至少建一条 `ontology_evidence_nodes`（segment_id + quote）。`ontology_entity_relations.source_refs_json` 填 evidence_node_id；DB CHECK 保证非空。
5. **冲突标记**：同 (head, relation, tail) 但属性矛盾（如默认值不同）→ 不合并，置 `has_conflict=true`，各记各的 evidence。

### 6.5 候选打分指标与噪声剪枝（量化口径，落实 L1 §5.3）
打分不能拍脑袋。下面是学界成熟、且在我们语料上可算的指标。**写进 `ontology_candidates.score` / `evidence_json` 与 `ontology_entities` 统计字段，作为 本体确认 排序与抽取轨剪枝依据**。

**A. 概念候选（要不要立成一个概念对象/类型）——termhood 系列**
| 指标 | 含义 | 计算（MVP 口径） |
|---|---|---|
| TF | 词频 | 该 mention 在语料出现次数 |
| DF / 跨文档分布 | 多少篇文档提到 | distinct document_count |
| **领域相关度 DR** | 是否本域特有（vs 通用语料） | **tf-dcf**：本域频次 ÷ 对比语料(通用)频次；越高越像术语 |
| **领域共识度 DC** | 是否被全域普遍使用（而非一篇里刷屏） | 跨文档**分布熵**：分布越均匀（多篇都用）DC 越高 |
| 区分度 | 是否其实已被现有类型覆盖 | 与现有 `ontology_node_types` 的语义相似度（低=新类型证据） |
| unithood（多词术语） | "用户面功能"是不是一个成词的整体 | **NPMI / 对数似然比**判搭配稳定性 |
| C-value/NC-value（多词术语） | 嵌套术语 + 上下文 | 可选，二期再上 |

> 立类型门槛（建议默认）：`DR > α 且 DF ≥ 2 篇 且 DC 不过低`。单篇刷屏（DF=1、DC 低）即便 TF 高也不立类型，只当本篇 mention。

**B. 关系候选 / 边可信度（这条边/这种关系靠不靠谱）**
| 指标 | 含义 | 计算 |
|---|---|---|
| 共现频次 | head、tail 同段/同篇共现次数 | 计数 |
| **NPMI 关联强度** | 区分"真关联"与"都高频碰巧共现" | `NPMI = ln(p(h,t)/(p(h)p(t))) / -ln p(h,t)`，范围[-1,1]，>0 才算正关联 |
| 三级可信度 | 实体级 / 关系级 / 全局 | 实体级=节点度&连通性；关系级=本条置信度；全局=在图中是否自洽 |
| 置信度阈值 | 低于即弃 | 边 confidence < 阈值（默认 0.6）丢弃或转候选 |

**C. 噪声剪枝（抽取轨，落库前）**
- **孤立实体**：TF 极低（仅 1 处）**且**图中度数=0（不连任何边）→ 判噪声，不落 `ontology_entities`（借鉴 GraphRAG 频率/度数排序）。
- **弱边**：NPMI < 阈值 且 LLM 未确认 → 丢（§6.3 第 4 闸）。
- **逃生口去噪**：`out_of_schema` 候选先按 A 表打分，DF=1 的低分项不进 本体确认，避免候选池被笔误/生僻词灌爆。

> 阈值（α、NPMI、置信度）全部做成可配，初值如上，**首波跑通后按实际数据回标**（属 L3 调优任务）。

---

## 7. Human-in-the-loop（异常触发，复用 mining_runs.status）

### 7.1 暂停/恢复机制
复用现有 `mining_runs.status` 协作式检查点：
- 全局阶段结束后检查：有 `ontology_candidates.status='proposed'` 或 `mentions.resolve_status='pending'` → 置 `mining_runs.status='awaiting_review'`，**暂停**（不 publish）。
- 无异常 → 直接 publish（**自动放行**，人审不是必经关卡）。

### 7.2 两道检查点（前端在 §8）
- **本体确认 本体评审**：列 `ontology_candidates`，人 通过(→升 v，写 ontology_node_types/relation_types)/改名/拒绝。通过后可触发**增量回灌**（仅重抽贡献过该候选的文档；MVP 可先全量重跑代表性子集）。
- **实体确认 实体确认**：列 `resolve_status='pending'` 的 mention，人 选择合并到已有 canonical / 新建 domain_entity / 标为非实体。回写后置 run 状态继续。

### 7.3 恢复
人在 kb-ui 点"继续" → API 置 `mining_runs.status='running'` → 后端从 graph_write 之后续跑 → publish。

---

## 8. kb-ui 透明前端（原则⑤：先把一切摊开）

新增"本体 / 知识图谱"模块。MVP 最小页面集：

| 页面 | 展示 | 交互 |
|---|---|---|
| **挖掘过程透视** | 每段：标到哪些类型、抽出哪些 mention、画了哪些边、哪些 pending、哪些冲突 | 只读，debug 用 |
| **本体确认 本体评审** | `ontology_candidates` 列表（候选类型/关系 + 证据 + 打分） | 通过/改名/拒绝 |
| **实体确认 实体确认** | pending mentions + 系统给的合并建议 | 合并/新建/丢弃 |
| **知识图谱浏览** | `ontology_entities` + `ontology_entity_relations`，点对象看邻域 + 出处回链 | 只读 |
| **本体版本** | `ontology_versions` 列表 + 当前 active 的类型/关系 | 查看/引种按钮 |

- 前端 API 走现有 `kb-ui/src/api/mining.ts` / `controlPlane.ts` 模式新增。
- 最小展示集（L1 §11.3 跟踪）：标签、边、冲突、不确定项四样必须可见。页面级排期与交互细化在 L3。

---

## 9. API 设计（草案）

```
POST /ontology/bootstrap            {domain}                 引种 v1
GET  /ontology/versions             ?domain                  版本列表
GET  /ontology/active               ?domain                  active 的类型+关系

GET  /mining/runs/{id}/trace                                 挖掘过程透视数据
GET  /ontology/candidates           ?domain&status           本体确认 列表
POST /ontology/candidates/{id}/review {action,new_name?}     通过/改名/拒绝 → 升 v
GET  /mentions/pending              ?run_id                  实体确认 列表
POST /mentions/{id}/resolve         {action,entity_id?}      合并/新建/丢弃
POST /mining/runs/{id}/resume                                人审后继续

GET  /graph/entities                ?domain&type&q           对象检索
GET  /graph/entities/{id}/neighbors ?hops=1                  邻域多跳
GET  /graph/evidence/{target_id}                             出处回链
```

图相关读写统一过 `OntologyStore` / `GraphStore` 薄接口（§10）。

---

## 10. 存储薄接口（为后期迁图留缝）

```
OntologyStore:   active_node_types / active_relation_types / bump_version / ...
GraphStore:      upsert_entity / upsert_relation / neighbors(entity_id, hops) /
                 add_evidence / get_evidence
```

- MVP 全用 PG 实现（neighbors 用递归 CTE）。
- 业务代码（抽取轨、检索侧）只调接口，不写裸 SQL。后期迁图库 = 换实现 + ETL，不动业务。

### 10.1 迁图风险分析（以 NebulaGraph 为例）
迁图最大的误区是以为"难在搬数据"。**搬数据是最不痛的一步**；真正的成本/风险按从大到小：

| 风险点 | 说明 | 缓解（靠薄接口） |
|---|---|---|
| **① 读路径查询重写**（最大） | 现在多跳 = PG **递归 CTE**；迁 Nebula 要重写成 **nGQL `GO`/`MATCH`**。检索侧若散落裸 SQL，等于重写一遍检索 | 所有遍历只走 `GraphStore.neighbors()`，迁移=换该方法实现，调用方零改动 |
| **② build/release 版本过滤** | 我们的图**绑 build_id / ontology_version_id** 做版本隔离；Nebula 无"版本视图"概念，需用 tag 属性 + 查询过滤或多 space 模拟 | 版本过滤逻辑收敛在 `GraphStore` 内部，不外泄到业务 |
| **③ 事务 / 一致性模型差异** | PG 强事务（一把写入 entity+relation+evidence）；Nebula 最终一致、无跨边事务，graph_write 的"原子落图"要改成补偿/幂等重试 | graph_write 设计成**幂等 upsert**（§14-1），天然适配最终一致 |
| **④ 出处强约束落地方式** | PG 用 `CHECK(source_refs 非空)` 保证；Nebula 无 CHECK，需在写入层校验 | 校验放 `GraphStore.upsert_relation()` 里，不依赖 DB 约束 |
| **⑤ 混合查询割裂** | 现在实体图与 `asset_*`（片段/向量）同库可 JOIN；迁图后图在 Nebula、文本在 PG，**出处回链变成跨库** | evidence 回链走接口 `get_evidence()`，内部可做两段式查询 |
| ⑥ 运维/选型 | 多一套有状态集群（部署、备份、监控） | 非代码风险，决策时计入 |

**结论**：只要守住"图读写全过 `GraphStore`/`OntologyStore`、绝不外泄裸 SQL"这道纪律，迁 Nebula 的工作量集中在**重写这两个接口的实现 + 一次性 ETL**，不波及挖掘/检索业务。**这正是 MVP 阶段就要付的"保险费"**——哪怕现在只用 PG，也别让递归 CTE 散进检索代码。

---

## 11. 检索侧消费（MVP）

1. **实体链接**：查询过 `ontology_alias_dictionary` + canonical 匹配，把口语映射到 `ontology_entities`。
2. **邻域召回**：命中对象 → `GraphStore.neighbors(id, hops=1~2)` 取概念邻域。
3. **出处回链**：召回的对象/边附 `ontology_evidence_nodes` → 片段 → 文档，给下游 LLM 与用户。
4. 接入现有 `agent_serving` 检索结果，作为新增一路通道（不替代 BM25/向量/RST）。

---

## 12. 分批实施计划 → 见 L3

分批次、依赖链、文件改动、每批验收**统一在 L3** `docs/plans/ontology/ontology-L3-impl-plan.md`。L3 的批次会引用本文的 §3（表）/§4（引种）/§6（阶段）/§10（接口）作为"每批要实现成什么样"的依据。本文不排执行顺序，避免 L2/L3 重叠。

---

## 13. 设计自洽性检查（本文蓝图是否站得住）
> 这是"设计层"的自检；**端到端落地验收（跑通三主题子集等）在 L3 按批次给**。

- 出处强约束：`ontology_entity_relations.source_refs_json` 有 DB CHECK 兜底，代码侧 `GraphStore` 再校验一道（迁图后仍生效）。
- 真相源唯一：运行期只读 active `ontology_*`，种子文件/domain.yaml 不在运行路径。
- 层可感知：所有类型/对象带 `layer`，加层不动表结构。
- 软约束闭环：非法但高置信的点/边都有去处（候选池），不静默丢弃。
- 迁图无死角：遍历/版本过滤/出处回链都在薄接口内（§10.1）。

---

## 14. 风险与开放项（实现层面）

1. **graph_write 全局阶段的事务/幂等**：重跑 build 要可重入（upsert + 按 build_id 清理旧版），需细化（也是迁图最终一致的前提，§10.1-③）。
2. **增量回灌粒度**：先用"全量重跑代表性子集"兜底，真增量（只重抽贡献文档）后置（排期见 L3）。
3. **Tier2 向量归一**：默认关；若 Tier1 召回率不足再开，且仍走人审。
4. **逃生口噪声**：已在 §6.5-C 用"DF=1 低分不进 本体确认 + 频率/度数剪枝"处理；阈值需首波后回标。
5. **本体升版后的历史数据**：旧 entity/relation 记 `ontology_version_id`，升版不追溯改写，靠回灌逐步刷新。

---

## 15. 本体归纳重排：实体先确认、再归纳本体（2026-06-11 提案，待审）

> **状态**：**已实施**（2026-06-12，N1–N5 全部落地，分批见 L3 §10）。对应 L1 §12。已取代 §6.1（逃生口直接产候选）、§7.1–§7.2（单检查点 + 本体确认 优先）的相应实现。

### 15.1 阶段重排（两个子循环、两个检查点）

把现行"逐段抽取 → 单次落图 → 一处检查（本体确认 优先）"重排成**两段**，中间各停一次：

```
逐段：parse → segment → enrich → entity_extract(LLM 调用 1)
全局A：聚合实体(去重 + 跨文档频率) → resolve 自动层(Tier1)
        ├─ 有歧义/暂无类型实体 → 检查 实体确认
        └─ ⏸ pause #1   subloop_stage='entity_review'
（人审 实体确认：确认实体，含"暂无类型"项）→ resume
全局B：ontology_induction(LLM 调用 2，仅吃"已确认·暂无类型"实体) → ontology_candidates
        ├─ 有候选 → 检查 本体确认
        └─ ⏸ pause #2   subloop_stage='ontology_review'
（人审 本体确认：确认新类型 → 升版）→ resume
收尾：给"暂无类型"实体补绑新类型 → entity_relations → graph_write(终) → build → publish
```

要点：
- **检查点从 1 个变 2 个**，顺序 **实体确认 在前、本体确认 在后**（现行 `_check_review_gate` 的"gate1 优先 + 单点检查"改为两段顺序检查）。
- **entity_relations 与终态落图后移到 本体确认 之后**：边只连"已确认且类型已定"的 canonical 对象——这是 L1 §4.2"消歧先于建边"原则的自然延伸（再加一条"定类型先于建边"）。

### 15.2 数据模型改动（最小）

- **"本体外概念"不再直接写候选**：`entity_extract` 把它写成 `asset_segment_entity_mentions` 的一条 **pending mention**，`node_type` 用哨兵值（如 `'__untyped__'`）、`metadata_json` 记 `{off_schema:true, llm_proposed_type?, reason}`。于是它**天然流进 实体确认 列表**。
- **"已确认·暂无类型"实体的暂存**：实体确认 确认后落 `ontology_entities`，`node_type='__untyped__'`（或加一个 `pending_type` 标记列；二选一在实现时定）。`ontology_induction` 就读这批。
- **归纳产物**：`ontology_candidates`（沿用现表，`source='global_induction'`），`payload_json` 列出归到该提议类型下的成员实体 id + 频率，`evidence_json` 带样例 quote。
- **本体确认 通过后的回贴**：升版 `add_node_type` 后，把这批成员实体的 `node_type` 从 `'__untyped__'` 改成新批准的类型名（一次 UPDATE）。

### 15.3 ontology_induction 阶段（LLM 调用 2）

- **输入**：一份紧凑清单——每个已确认·暂无类型实体的 `{canonical_name, aliases, df, tf, sample_quotes[≤3], llm_proposed_type?}`。**不是重扫全文**，故便宜。
- **prompt**：让模型对这批实体**聚类 + 为每簇提议类型名 + 写定义 + 给代表成员**；附 active 本体类型表作"已存在、勿重复提议"的参照。
- **输出 schema**：`proposed_types[]{name, definition, member_entity_ids[], examples[]}` → 落 `ontology_candidates`。
- **去噪**：沿用 §6.5——单簇成员跨文档 DF<2 或仅孤立出现的，不进 本体确认。

### 15.4 编排 / 恢复

- `subloop_stage` 取值扩成有序两档：`entity_review` → `ontology_review` → `done`。resume 时按当前档位决定从哪续跑（实体确认 后从 induction 续；本体确认 后从回贴+建边续）。
- **快速通道**：本轮无歧义 mention → 跳过 pause #1；无"暂无类型"实体或归纳无候选 → 跳过 induction + pause #2，直接收尾。稳态下两次暂停都不触发。

### 15.5 开放点（实现时定）
1. "暂无类型"用哨兵 `node_type` 还是新增 `pending_type` 列——倾向哨兵，零 DDL。
2. 实体确认 前端要加"确认为实体（暂无类型）"这一裁决动作（区别于"并到已有 / 新建有类型对象 / 丢弃"）。
3. 聚合实体 + 频率要在 实体确认 **之前**算出来（现行 graph_write 在最后，需前移一个"实体聚合"轻量步，或让 induction 前的全局A 承担）。
4. 与"增量回灌"（§14-2）的关系：升版后是否重抽，沿用现策略（影响后续 run）。
