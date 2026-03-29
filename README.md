# Ad Creative Compliance Review Agent

自动审核 vivo 广告平台广告素材的合规性，替代 80% 的人工初审工作。

## 本地开发

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填写 API Key

# 3. 启动服务
python scripts/start_server.py

# 4. 验证
curl http://localhost:8000/health
```

## 生产部署（Docker Compose）

```bash
# 1. 配置环境变量
cp .env.production.example .env
# 编辑 .env 填写生产环境 API Key 和数据库密码

# 2. 构建并启动
docker compose up -d --build

# 3. 验证
curl http://localhost:8000/health
```

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `DEEPSEEK_API_KEY` | 是 | - | DeepSeek API 密钥 |
| `DEEPSEEK_BASE_URL` | 否 | `https://api.deepseek.com` | DeepSeek API 地址 |
| `DASHSCOPE_API_KEY` | 是 | - | 通义千问视觉模型 API 密钥 |
| `DASHSCOPE_BASE_URL` | 否 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | DashScope API 地址 |
| `QWEN_VL_MODEL` | 否 | `qwen-vl-max` | 视觉模型名称 |
| `DATABASE_URL` | 是 | `sqlite+aiosqlite:///ad_review.db` | 数据库连接串 |
| `REDIS_URL` | 否 | `redis://localhost:6379/0` | Redis 连接串 |
| `AUTO_PASS_THRESHOLD` | 否 | `0.92` | 自动放行置信度阈值 |
| `AUTO_REJECT_THRESHOLD` | 否 | `0.92` | 自动拒审置信度阈值 |
| `HUMAN_REVIEW_LOWER` | 否 | `0.70` | 强制人工审核阈值 |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |

## API 接口

### POST /review

JSON 方式提交审核请求，素材通过 URL 引用。

```bash
curl -X POST http://localhost:8000/review \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "test-001",
    "advertiser_id": "adv-001",
    "ad_category": "tool_app",
    "creative_type": "image",
    "content": {
      "title": "应用标题",
      "description": "应用描述",
      "image_urls": ["https://example.com/ad.jpg"]
    }
  }'
```

### POST /review/upload

Multipart 方式直接上传图片/视频文件审核（最多 5 个文件）。

```bash
curl -X POST http://localhost:8000/review/upload \
  -F "files=@ad_image.jpg" \
  -F "advertiser_id=adv-001" \
  -F "ad_category=tool_app" \
  -F "title=应用标题"
```

### 其他接口

| 接口 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `GET /queue` | 待审核队列 |
| `GET /records` | 审核历史 |
| `GET /stats` | 统计数据 |
| `GET /admin/` | 管理后台 |

## 已知限制

- 视频审核耗时约 26 秒（下载 + 关键帧抽取 + 逐帧视觉分析），生产环境建议接入异步任务队列
- 视频文件大小上限 500MB，超大视频仅采样审核
- 落地页检测依赖目标页可访问性，反爬严格的页面会降级到人工复核
