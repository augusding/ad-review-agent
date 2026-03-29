# AGENTS.md — 广告素材合规审核 Agent 项目宪法（AHF 三段式）

> 每次启动 Claude Code 时自动读取本文件。
> 所有开发决策以本文件为准，与本文件冲突时以本文件优先。
> 原始版本见 `CLAUDE.md.bak`，本文件按 Agent Harness Framework（AHF）三段式重组。

---

# §1 身份与边界

## 1.1 项目概述

**项目名称**：Ad Creative Compliance Review Agent（广告素材合规审核 Agent）
**业务目标**：自动审核 vivo 广告平台上广告主提交的素材，替代 80% 的人工初审工作
**详细需求**：见 `@ADD.md`
**当前阶段**：Phase 0 地基建设

## 1.2 成功指标（上线后 4 周验收）

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 自动通过率 | ≥ 80% | 无需人工介入直接放行的比例 |
| 漏审率（False Negative） | ≤ 5% | 违规素材被放行的比例，**最关键指标** |
| 误拒率（False Positive） | ≤ 10% | 合规素材被错误拒绝的比例 |
| 平均审核时长 | ≤ 30s | 单条素材端到端处理时间 |
| 单条素材处理成本 | ≤ ¥0.05 | 含所有 API 调用 |
| 人工复核队列处理量 | 减少 60% | 相比纯人工基线 |

## 1.3 Agent 做什么（In Scope）

- 对提交的广告素材进行多维度合规初审
- 输出结构化审核结论（通过/人工复核/拒绝）
- 输出置信度评分（0-1）
- 输出具体违规项清单（违规内容/所违反的条款/严重程度）
- 输出面向广告主的说明文字（指出问题和修改建议）
- 将低置信度案例推送人工复核队列

## 1.4 Agent 不做什么（Out of Scope）

- ❌ 不做最终审核决定（最终决定权在人工审核员）
- ❌ 不操作广告主账户（封号/扣费/赔付）
- ❌ 不处理广告主申诉（申诉流程独立）
- ❌ 不审核广告的商业策略合理性（仅做合规审核）
- ❌ 不对视频内容做逐帧全量审核（采样审核+关键帧策略）

## 1.5 关键约束

- 每条素材处理成本目标：≤ ¥0.05（含 API 调用）
- 不得存储原始素材内容（隐私合规要求）
- 审核日志保留 90 天（供申诉溯源）
- **决策阈值定义在 `harness/constraints.yaml`，不得硬编码在 Python 代码中**
- **Tool 通过 `ModelRouter` 调用 LLM，不得直接构造 httpx 客户端调用**

## 1.6 技术栈（不得擅自更换）

```
语言：        Python 3.11
包管理：      uv（不用 pip/poetry）
主力 LLM：    DeepSeek（deepseek-chat）— 文案分析、语义理解、落地页分析
视觉 LLM：    Anthropic Claude claude-sonnet-4-20250514 — 图片/视频内容安全检测
数据验证：    Pydantic v2
HTTP 客户端： httpx（异步，不用 requests）
测试框架：    pytest + pytest-asyncio
日志：        loguru（不用 print/logging 标准库）
环境变量：    python-dotenv
任务队列：    Redis（人工复核队列）
数据库：      PostgreSQL（审核日志）
```

---

# §2 工作流程

## 2.1 开发工作流

**每次会话启动时，必须先读取以下文件了解项目进度：**
- `progress.txt` — 当前开发进度和待办事项
- `feature_list.json` — 功能清单及完成状态

## 2.2 Claude Code 使用约定

### 2.2.1 每次调用前必须引用相关文件

```
# 好的指令示例
「基于 @ADD.md 中第 4 节的审核维度定义，
  参考 @src/tools/base_tool.py 的接口规范，
  实现 TextViolationChecker：
  - 先写 tests/tools/test_text_checker.py（覆盖4种场景）
  - 再写 src/tools/text_checker.py
  - 只修改这两个文件，不改其他任何文件」
```

### 2.2.2 每次指令只做一件事

```
# ❌ 错误：一次指令要求做太多事
「帮我实现整个审核 Agent，包括所有 Tool 和主循环」

# ✅ 正确：单一职责
「实现 TextViolationChecker，先测试后实现」
```

### 2.2.3 修改任务的标准格式

```
「先读 @src/tools/text_checker.py 理解现有实现，
  当前问题：[描述问题]，
  期望行为：[描述期望]，
  修改要求：改动最小化，只修改必要的代码，
  不要修改接口签名（测试依赖它）」
```

### 2.2.4 调试任务的标准格式

```
「@tests/tools/test_text_checker.py 中以下测试失败：
  测试名：test_text_checker__forbidden_word__returns_reject
  错误信息：[粘贴完整报错]
  先分析原因（不要直接改代码），
  确认分析后再修改 @src/tools/text_checker.py」
```

## 2.3 项目目录结构（目标结构）

```
ad-review-agent/
├── AGENTS.md                      ← 你正在读的文件（AHF 三段式）
├── CLAUDE.md                      ← 指向 AGENTS.md 的 symlink
├── ADD.md                         ← Agent 需求设计文档
├── progress.txt                   ← 当前开发进度
├── feature_list.json              ← 功能清单及完成状态
├── .claude/
│   └── commands/                  ← 自定义 Slash Commands
│       ├── new-tool.md
│       ├── run-eval.md
│       ├── add-case.md
│       ├── debug-tool.md
│       └── update-prompt.md
├── harness/                       ← Agent Harness 配置层
│   ├── constraints.yaml           ← 决策阈值、成本限制等约束配置
│   ├── routing.yaml               ← ModelRouter 路由规则
│   └── tools.yaml                 ← Tool 注册与编排配置
├── src/
│   ├── __init__.py
│   ├── config.py                  ← 配置管理（从环境变量读取）
│   ├── harness/                   ← Harness 运行时实现
│   │   ├── __init__.py
│   │   ├── constraints.py         ← 约束配置加载器
│   │   ├── model_router.py        ← ModelRouter：LLM 调用统一路由
│   │   └── tool_registry.py       ← Tool 动态注册与发现
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── review_agent.py        ← Agent 主循环和编排逻辑
│   │   └── tool_executor.py       ← Tool 调用封装（超时/重试/日志）
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── base_tool.py           ← BaseTool 抽象类（所有 Tool 必须继承）
│   │   ├── text_checker.py        ← 文案违禁词检测
│   │   ├── image_checker.py       ← 图片内容安全
│   │   ├── landing_page.py        ← 落地页一致性核查
│   │   ├── qualification.py       ← 行业资质匹配
│   │   └── platform_rule.py       ← vivo 平台专项规范
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── request.py             ← ReviewRequest Pydantic Model
│   │   ├── result.py              ← ReviewResult Pydantic Model
│   │   ├── violation.py           ← ViolationItem Pydantic Model
│   │   └── tool_io.py             ← 各 Tool 的输入输出 Schema
│   ├── prompts/
│   │   ├── system_prompt.txt      ← 主 System Prompt（不写死在代码里）
│   │   ├── text_checker.txt       ← TextChecker 专用 Prompt
│   │   ├── image_checker.txt      ← ImageChecker 专用 Prompt
│   │   └── few_shot_examples.txt  ← Few-shot 示例
│   └── rules/
│       ├── forbidden_words.json   ← 违禁词库（热更新，有版本号）
│       ├── category_rules.json    ← 品类专项规则
│       ├── qualification_map.json ← 行业×资质要求映射
│       └── vivo_platform.json     ← vivo 平台专项规范
├── evals/
│   ├── golden_dataset/
│   │   ├── pass_cases.jsonl       ← 合规案例（目标 50 条）
│   │   ├── reject_cases.jsonl     ← 违规案例（目标 50 条）
│   │   ├── review_cases.jsonl     ← 边界案例（目标 30 条）
│   │   └── adversarial_cases.jsonl← 对抗性案例（目标 20 条）
│   ├── run_eval.py                ← 一键执行评估
│   ├── metrics.py                 ← 指标计算
│   └── results/                   ← 每次 Eval 结果存档（不提交 git）
├── tests/
│   ├── conftest.py
│   ├── schemas/
│   │   └── test_schemas.py
│   ├── tools/
│   │   ├── test_text_checker.py
│   │   ├── test_image_checker.py
│   │   ├── test_landing_page.py
│   │   └── test_qualification.py
│   └── agents/
│       └── test_review_agent.py
├── scripts/
│   └── seed_rules.py              ← 初始化规则库数据
├── .env.example
├── .env                           ← 不提交 git
├── pyproject.toml
└── README.md
```

## 2.4 代码规范（每一条都是强制要求）

### 2.4.1 通用规范

```python
# ✅ 正确：所有函数必须有类型注解和 docstring
async def check_text(content: str, category: AdCategory) -> TextCheckResult:
    """
    检测广告文案中的违禁词和违禁表述。

    Args:
        content: 广告文案全文
        category: 广告品类枚举值

    Returns:
        TextCheckResult 包含检测结论和违规项列表

    Raises:
        ToolExecutionError: LLM 调用失败时抛出
    """

# ❌ 错误：无注解，无 docstring
def check_text(content, category):
    pass
```

### 2.4.2 LLM 调用规范

**注意：未来所有 LLM 调用应通过 `ModelRouter` 统一路由，不直接构造客户端。**

```python
# ✅ 正确：必须有超时、重试、日志（未来通过 ModelRouter 调用）
response = await call_llm_with_retry(
    messages=messages,
    timeout=30.0,
    max_retries=3,
    retry_delay=1.0,
)

# ❌ 错误：裸调用，无保护
response = client.chat.completions.create(messages=messages)

# ❌ 错误：Tool 中直接构造 httpx 客户端调 LLM
client = httpx.AsyncClient(base_url="https://api.deepseek.com")
```

### 2.4.3 输出验证规范

```python
# ✅ 正确：所有 LLM 输出必须经过 Pydantic 验证
result = ReviewResult.model_validate_json(raw_output)

# ❌ 错误：直接使用裸字符串或 dict
result = json.loads(raw_output)  # 不允许
```

### 2.4.4 错误处理规范

```python
# ✅ 正确：每个异常路径都有明确降级动作
try:
    result = await tool.execute(input_data)
except ToolTimeoutError:
    logger.warning(f"Tool {tool.name} timeout, routing to human queue")
    return FallbackResult.human_review_required(reason="tool_timeout")
except ToolExecutionError as e:
    logger.error(f"Tool {tool.name} failed: {e}")
    return FallbackResult.human_review_required(reason="tool_error")

# ❌ 错误：空 except 或只 raise
try:
    result = await tool.execute(input_data)
except Exception:
    raise  # 不允许
```

### 2.4.5 日志规范

```python
# ✅ 正确：使用 loguru，包含 request_id，结构化
logger.info(
    "Tool execution completed",
    tool=tool.name,
    request_id=request_id,
    duration_ms=duration,
    verdict=result.verdict,
)

# ❌ 错误：print 调试
print(f"result: {result}")  # 绝对禁止
```

### 2.4.6 配置管理规范

```python
# ✅ 正确：从 config.py 统一读取
from src.config import settings
api_key = settings.deepseek_api_key

# ❌ 错误：硬编码或直接读 os.environ
api_key = "sk-xxx"  # 绝对禁止
api_key = os.environ["DEEPSEEK_API_KEY"]  # 不允许，统一走 config
```

## 2.5 Tool 开发规范

每个 Tool 必须：

1. **继承 BaseTool**（`src/tools/base_tool.py`）
2. **有独立的输入输出 Schema**（在 `src/schemas/tool_io.py` 定义）
3. **有完整的单元测试**（在 `tests/tools/` 下）
4. **处理所有失败路径**（超时/API错误/格式错误）
5. **不在 Tool 内部直接调用其他 Tool**（编排由 agent 层负责）
6. **通过 ModelRouter 调用 LLM**（不直接构造 httpx 客户端）

```python
# Tool 标准结构模板
class TextViolationChecker(BaseTool):
    name = "text_violation_checker"
    description = "检测广告文案违禁词和违禁表述"

    async def execute(self, input: TextCheckerInput) -> TextCheckerOutput:
        ...

    async def _fallback(self, error: Exception) -> TextCheckerOutput:
        """降级处理，必须实现"""
        ...
```

## 2.6 Prompt 管理规范

- **所有 Prompt 存放在 `src/prompts/` 目录，使用 `.txt` 文件**
- 不得将长 Prompt 字符串写死在 Python 代码里
- Prompt 修改需要更新版本注释（文件头部 `# Version: x.x`）
- Prompt 修改后必须重新运行 Eval，对比指标变化

```
# 错误示范
system_prompt = """
你是一个广告审核专家...
（大段 Prompt）
"""  # ❌ 不允许写在代码里

# 正确做法
system_prompt = load_prompt("system_prompt.txt")  # ✅
```

## 2.7 规则库管理规范

`src/rules/` 下的 JSON 文件支持热更新，不需要重启服务。

规则文件必须包含版本号和最后更新时间：

```json
{
  "version": "1.0.0",
  "updated_at": "2026-03-01",
  "updated_by": "运营团队",
  "rules": [...]
}
```

修改规则后：
1. 更新 `version` 字段
2. 在 Git commit message 中注明变更原因
3. 重新运行 Eval 验证规则变更无副作用

## 2.8 测试规范

- **新功能先写测试再写实现（TDD）**
- 每个 Tool 的测试必须覆盖：正常输入 / 边界输入 / 错误输入 / 降级路径
- 禁止 Mock LLM 调用（使用真实 API，测试账号隔离）
- 禁止在测试中使用真实广告主数据（使用构造的测试数据）
- 每次提交前必须运行 `pytest tests/` 全部通过

```python
# 测试文件命名规范
tests/tools/test_{tool_name}.py

# 测试函数命名规范
def test_{tool_name}__{scenario}__returns_{expected}():
    # 示例：
    # test_text_checker__forbidden_word__returns_reject
    # test_text_checker__empty_input__returns_pass
    # test_text_checker__api_timeout__returns_fallback
```

## 2.9 Git 规范

```
# Commit message 格式
<type>(<scope>): <description>

# type 枚举
feat:     新功能
fix:      Bug 修复
test:     添加或修改测试
prompt:   Prompt 修改（需注明 Eval 影响）
rules:    规则库更新（需注明变更原因）
refactor: 代码重构（不改功能）
docs:     文档更新
harness:  Harness 配置或框架层变更

# 示例
feat(tools): implement TextViolationChecker with TDD
fix(agent): handle LLM timeout in review loop
prompt(text_checker): improve boundary case instructions, eval +2.3% accuracy
rules(forbidden_words): add new financial sector restrictions per 2026-03 regulation update
harness(router): add ModelRouter with DeepSeek/Claude routing
```

---

# §3 领域启发

## 3.1 审核哲学

> **核心产品理念：帮助广告主修改素材，而非简单拒绝。**

审核结论中的 `reason` 字段不仅要指出问题，更要给出具体的修改方向。拒审不是目的，让广告主高效完成合规投放才是目的。每一条拒审理由都应该让广告主读完后知道"怎么改才能通过"。

## 3.2 品类特殊性

| 品类 | 核心风险 | 审核重点 |
|------|---------|---------|
| 游戏 | 版号合规、未成年人保护、博彩擦边 | 版号核验、暗示高额回报的买量话术、博彩视觉元素、过度血腥画面、未成年人引导内容 |
| 金融 | 收益承诺、无牌照经营 | 金融牌照校验、保本保息表述、收益承诺用语、年化收益率展示规范 |
| 医疗健康 | 疗效保证、替代医疗 | 医疗器械/药品批准文号、疗效保证表述、替代正规医疗的暗示 |
| 教育 | 资质合规、虚假宣传 | 办学许可证、学科类培训资质、"包就业""包过"等承诺 |
| 电商 | 虚假促销、价格欺诈 | "限时免费"实为付费、虚假折扣、伪造销量数据 |
| 工具应用 | 诱导下载、虚假功能 | 伪造系统通知诱导下载、"手机检测到病毒"等恐吓、虚假界面截图 |

## 3.3 常见边界案例处理策略

### 绝对化用语的上下文判断

- "最新版本" — **通过**：描述事实（软件确实有版本迭代）
- "最强性能" — **拒绝**：无法核实的绝对化用语
- "第一时间响应" — **通过**：时间描述而非排名
- "行业第一" — **拒绝**：排名声明需要权威数据源支撑

### 数据宣传核实标准

- "下载量超1亿" — 如广告主无法提供第三方数据来源，标记为**待复核**
- "好评率99%" — 平台可验证的数据允许，自行声称的需标记为**待复核**
- "用户满意度行业领先" — **拒绝**：模糊的比较级声明

### 图片/视觉边界

- 游戏战斗画面 — 适度战斗效果**通过**，过度血腥**拒绝**（依赖视觉模型判断）
- 模拟系统界面 — 如包含明确的"广告"标识**通过**，否则**拒绝**
- 竞品对比图 — 客观参数对比**通过**，贬损性语言**拒绝**

## 3.4 置信度解读

| 置信度区间 | 含义 | 系统行为 |
|-----------|------|---------|
| ≥ 0.95 | Agent 高度确信判断正确，证据充分 | 自动执行（放行或拒审） |
| 0.85 - 0.95 | Agent 较有把握，但存在少量不确定性 | 自动执行，但标记供抽检 |
| 0.70 - 0.85 | 边界案例，Agent 识别到模糊因素 | 推送人工复核队列，附 Agent 分析 |
| < 0.70 | Agent 无法做出可靠判断 | 强制人工审核，标注"低置信度" |

**注意**：精确的决策阈值（如 0.92）定义在 `harness/constraints.yaml` 中，上表仅为语义理解参考。

## 3.5 成本控制要点

- **文案分析用 DeepSeek**：成本低，中文理解强，单次调用约 ¥0.001-0.005
- **图片分析用 Claude claude-sonnet-4-20250514**：多模态能力强，单次调用约 ¥0.01-0.03
- **提前终止策略**：发现 `high` 严重度违规且置信度 > 0.9 时，可跳过后续低优先级检测维度
- **视频采样策略**：不逐帧审核，采用首帧/尾帧/随机3帧的关键帧抽样
- **落地页缓存**：同一落地页 URL 短期内多次提交时可复用分析结果

---

# §4 禁止行为清单（Claude Code 执行时严格遵守）

- ❌ 不许用 `print()` 调试，用 `logger`
- ❌ 不许把 API Key 写在任何代码或配置文件中
- ❌ 不许在没有测试的情况下修改 Tool 的返回格式
- ❌ 不许在 Tool 内部调用其他 Tool（编排由 agent 层负责）
- ❌ 不许使用 `except Exception: pass` 或空 except
- ❌ 不许把 Prompt 字符串硬编码在 Python 文件里
- ❌ 不许在一次 PR 中同时修改代码逻辑和 Prompt（分开提交，方便定位问题）
- ❌ 不许在未运行 Eval 的情况下修改 System Prompt 后提交
- ❌ 不许删除或修改 `evals/results/` 下的历史 Eval 结果（只追加）
- ❌ 不许在一次 Claude Code 指令中要求完成超过一个文件的新功能开发
- ❌ 不许硬编码决策阈值在 Python 代码中（必须在 `harness/constraints.yaml` 定义）
- ❌ 不许在 Tool 中直接构造 httpx 客户端调用 LLM（必须通过 `ModelRouter`）
