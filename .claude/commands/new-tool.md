# /project:new-tool — 标准化创建新 Tool

## 使用方式
```
/project:new-tool TextViolationChecker
```

## 执行步骤

基于 $ARGUMENTS（Tool 名称），按以下步骤创建新 Tool：

### Step 1：确认设计
先读 @ADD.md 第 4 节，确认该 Tool 负责的审核维度和检测范围。
如果 ADD.md 中没有该 Tool 的描述，先停下来告诉用户需要补充 ADD.md。

### Step 2：在 schemas/tool_io.py 添加 Schema
在 @src/schemas/tool_io.py 中追加该 Tool 的输入输出 Pydantic Model：
- `{ToolName}Input`：包含所有输入字段，每个字段有类型注解和 Field 描述
- `{ToolName}Output`：包含 verdict/confidence/violations/fallback_reason 字段
不要修改文件中已有的其他 Schema。

### Step 3：创建测试文件（先于实现）
创建 `tests/tools/test_{snake_case_name}.py`，覆盖以下4类测试场景：
1. 正常输入 - 应返回 reject（有明确违规）
2. 正常输入 - 应返回 pass（合规内容）
3. 边界输入 - 应返回 review（灰色地带）
4. 错误输入 - API 超时时应返回 fallback（不能抛异常）

使用真实 API 调用（不 mock），测试数据使用构造的虚假内容。

### Step 4：创建 Tool 实现
创建 `src/tools/{snake_case_name}.py`：
- 继承 @src/tools/base_tool.py 中的 BaseTool
- 实现 `execute()` 异步方法
- 实现 `_fallback()` 降级方法（必须，不能抛异常）
- 所有 LLM 调用使用 @src/agents/tool_executor.py 中的封装方法
- Prompt 存放在 `src/prompts/{snake_case_name}.txt`（同步创建）

### Step 5：更新导出
在 @src/tools/__init__.py 中添加该 Tool 的导出。

### Step 6：运行测试
运行 `pytest tests/tools/test_{snake_case_name}.py -v`，确认全部通过后报告结果。

## 注意事项
- 每次只创建一个 Tool
- 不修改其他已有 Tool 文件
- 如果发现 base_tool.py 需要修改，先停下来告知用户，不要自行修改
