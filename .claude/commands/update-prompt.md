# /project:debug-tool — 诊断 Tool 测试失败

## 使用方式
```
/project:debug-tool TextViolationChecker
```
然后粘贴失败的测试输出。

## 执行步骤

1. 读取 @tests/tools/test_{snake_case}.py 中的失败测试用例
2. 读取 @src/tools/{snake_case}.py 当前实现
3. 分析失败原因（先分析，不急着改代码）
4. 给出3种可能的原因假设，按可能性排序
5. 等用户确认分析方向后再动手修改
6. 修改范围：只改 src/tools/ 对应文件，不改测试文件
7. 修改后说明改了什么、为什么这样改

---

# /project:update-prompt — 修改 Prompt 并验证效果

## 使用方式
```
/project:update-prompt system_prompt
/project:update-prompt text_checker
```

## 执行步骤

### Step 1：说明修改意图
告知 Claude Code：
- 当前 Prompt 存在什么问题（附上具体 Eval 失败案例）
- 期望修改方向

### Step 2：读取当前版本
读取 @src/prompts/{name}.txt，确认当前内容和版本号。

### Step 3：提出修改方案
Claude Code 给出具体修改建议（diff 形式展示），
说明修改的理由和预期效果，等用户确认。

### Step 4：执行修改
确认后更新文件，版本号 +0.1，在文件头部注释中记录变更原因。

### Step 5：触发 Eval
自动执行 /project:run-eval，对比修改前后指标。
如果核心指标变差（漏审率上升），建议回滚并分析原因。

## 重要约束
- Prompt 修改和代码修改必须分开提交（不在同一次操作中）
- 每次 Prompt 修改后必须跑 Eval，结果必须记录
- 漏审率（False Negative）是红线指标，任何导致漏审率上升的修改都不允许合入
