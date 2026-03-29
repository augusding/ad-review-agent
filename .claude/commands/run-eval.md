# /project:run-eval — 运行评估并对比基线

## 使用方式
```
/project:run-eval              # 运行全量 Eval
/project:run-eval text_only    # 只跑文案检测相关案例
/project:run-eval quick        # 只跑 pass/reject 各10条快速验证
```

## 执行步骤

### Step 1：检查 Golden Dataset
确认 @evals/golden_dataset/ 下的 .jsonl 文件存在且格式正确。
如果文件为空或不存在，告知用户需要先执行 /project:add-case 添加案例。

### Step 2：运行评估
执行 `python evals/run_eval.py --mode $ARGUMENTS`

### Step 3：输出指标对比
展示以下指标，并与上一次 Eval 结果（@evals/results/ 最新文件）对比：

| 指标 | 上次 | 本次 | 变化 |
|------|------|------|------|
| 整体准确率 | - | - | - |
| 漏审率（最重要） | - | - | - |
| 误拒率 | - | - | - |
| 自动化率（confidence>0.92） | - | - | - |
| 平均处理时长 | - | - | - |
| 平均 Token 用量 | - | - | - |

### Step 4：分析退步原因
如果任何核心指标（漏审率/误拒率/准确率）相比上次变差，
自动分析哪些案例从正确变为错误，找共同特征，给出优化建议。

### Step 5：保存结果
将本次结果保存到 `evals/results/eval_{timestamp}.json`。

## 目标指标（Phase 1 验收标准）
- 整体准确率 ≥ 90%
- 漏审率 ≤ 5%（最关键，违规放行）
- 误拒率 ≤ 10%（合规被拒）
- 自动化率 ≥ 80%
