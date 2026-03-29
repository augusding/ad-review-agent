# 素材接入对接指南

Ad Review Agent 支持三种素材接入方式，可同时启用。

---

## 方式A：HTTP Webhook 推送

外部系统主动推送素材审核请求到 Agent 的 Webhook 接口。

### 接口地址

```
POST /ingest/webhook
```

### 请求格式

```json
{
  "request_id": "ext-20260322-001",
  "advertiser_id": "adv-001",
  "ad_category": "game",
  "creative_type": "text",
  "title": "广告标题",
  "description": "广告描述文案",
  "cta_text": "立即下载",
  "image_urls": ["https://cdn.example.com/ad1.jpg"],
  "video_url": null,
  "landing_page_url": "https://example.com/landing"
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| request_id | string | 否 | 唯一标识，为空时自动生成 |
| advertiser_id | string | 是 | 广告主 ID |
| ad_category | string | 是 | game/tool_app/ecommerce/finance/health/education/other |
| creative_type | string | 是 | text/image/video/mixed |
| title | string | 否 | 广告标题 |
| description | string | 否 | 广告描述 |
| cta_text | string | 否 | 行动按钮文案 |
| image_urls | string[] | 否 | 图片 URL 列表 |
| video_url | string | 否 | 视频 URL |
| landing_page_url | string | 否 | 落地页 URL |

### 签名验证

如果配置了 `INGEST_WEBHOOK_SECRET`，请求需要携带签名 Header：

```
X-Webhook-Signature: <HMAC-SHA256 hex digest>
```

签名计算方式：

```python
import hmac, hashlib

signature = hmac.new(
    secret.encode(),
    request_body_bytes,
    hashlib.sha256
).hexdigest()
```

### 示例代码（Python）

```python
import httpx
import hmac
import hashlib
import json

SECRET = "your-webhook-secret"
URL = "http://ad-review.internal:8000/ingest/webhook"

payload = {
    "request_id": "ext-001",
    "advertiser_id": "adv-001",
    "ad_category": "game",
    "creative_type": "text",
    "title": "精彩手游",
    "description": "和好友一起冒险",
}

body = json.dumps(payload).encode()
signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

resp = httpx.post(
    URL,
    content=body,
    headers={
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
        "X-API-Key": "your-api-key",
    },
)
print(resp.json())
```

### 字段映射

如果外部系统的字段名与标准格式不同，可通过 `INGEST_FIELD_MAPPING` 配置映射：

```
INGEST_FIELD_MAPPING={"ext_ad_id": "request_id", "ext_category": "ad_category"}
```

---

## 方式B：消息队列

Agent 作为消费者从 Kafka 或 RocketMQ 拉取素材审核消息。

### 配置

```env
MQ_TYPE=kafka                    # kafka 或 rocketmq
MQ_BROKERS=kafka-01:9092,kafka-02:9092
MQ_TOPIC=ad_creative_submit
MQ_GROUP_ID=ad-review-agent
MQ_CONSUME_RATIO=0.1             # 灰度消费比例
```

### 消息格式

与 Webhook 请求格式一致的 JSON 字符串：

```json
{
  "request_id": "mq-20260322-001",
  "advertiser_id": "adv-001",
  "ad_category": "tool_app",
  "creative_type": "text",
  "title": "手机清理工具",
  "description": "一键释放存储空间"
}
```

### 灰度比例控制

`MQ_CONSUME_RATIO` 控制 Agent 实际处理的消息比例：

| 值 | 含义 |
|----|------|
| 0.1 | 10% 灰度，每 10 条消息处理 1 条 |
| 0.3 | 30% 灰度 |
| 1.0 | 全量消费 |

未处理的消息仍走原有人工审核流程。

### Kafka 依赖安装

```bash
uv add kafka-python
```

### RocketMQ 依赖安装

```bash
uv add rocketmq-client-python
```

---

## 方式C：手动上传

通过管理后台或 API 直接上传文件审核。

### API 方式

```bash
curl -X POST http://localhost:8000/review/upload \
  -H "X-API-Key: your-api-key" \
  -F "files=@ad_image.jpg" \
  -F "advertiser_id=adv-001" \
  -F "ad_category=tool_app" \
  -F "title=广告标题"
```

### 管理后台操作

1. 访问 http://localhost:8000/login，输入管理员账号密码登录
2. 进入管理后台首页，可查看待审核队列
3. 通过 API 提交素材后，审核结果自动出现在队列中
4. 对 verdict=review 的案例，审核员可一键确认或推翻

---

## 灰度期监控

### 查看审核统计

```bash
curl http://localhost:8000/stats
```

关注以下指标：

| 指标 | 含义 | 目标 |
|------|------|------|
| human_agreement_rate | Agent 与人工审核一致率 | > 95% |
| false_negative_rate | 漏审率 | < 5% |
| automation_rate | 自动化率 | > 80% |
| avg_processing_ms | 平均处理时长 | < 30000ms |

### 查看人工复核队列

```bash
curl http://localhost:8000/queue?status=pending
```

### 查看审核历史

```bash
curl "http://localhost:8000/records?verdict=reject&limit=20"
```

### 管理后台统计看板

访问 http://localhost:8000/admin/stats 查看可视化统计面板，
包含 verdict 分布、自动化率、人工一致率等核心指标。
