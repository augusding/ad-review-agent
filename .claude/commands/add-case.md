# /project:add-case — 向 Golden Dataset 添加标注案例

## 使用方式
```
/project:add-case pass      # 添加合规案例
/project:add-case reject    # 添加违规案例
/project:add-case review    # 添加边界案例
/project:add-case adversarial  # 添加对抗性案例
```

## 执行步骤

### Step 1：读取用户提供的案例内容
提示用户提供：
1. 素材内容（文案/图片描述/落地页URL）
2. 广告品类（game/finance/health/tool_app/other）
3. 预期判断结论（pass/reject/review）
4. 如果是 reject/review：违规维度和违规原因
5. 参考的规则条款（选填）

### Step 2：格式化为标准 JSONL
按以下格式追加到对应文件：
```json
{
  "case_id": "auto_generated_uuid",
  "created_at": "ISO8601",
  "category": "game",
  "creative_type": "text",
  "content": {...},
  "expected_verdict": "reject",
  "expected_violations": [
    {
      "dimension": "text_violation",
      "regulation_ref": "《广告法》第九条",
      "severity": "high"
    }
  ],
  "notes": "案例说明（可选）",
  "source": "manual_annotation"
}
```

### Step 3：确认并写入
展示格式化后的案例请用户确认，确认后追加到对应 .jsonl 文件。
同时输出当前各类别案例数量统计。
