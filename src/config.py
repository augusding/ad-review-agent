"""
项目配置管理，所有配置从环境变量读取，不硬编码。
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # LLM API Keys
    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com"
    dashscope_api_key: str
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # 模型配置
    deepseek_model: str = "deepseek-chat"
    qwen_vl_model: str = "qwen-vl-max"

    # 超时配置（秒）
    llm_timeout: float = 30.0
    http_timeout: float = 10.0

    # 重试配置
    llm_max_retries: int = 3
    llm_retry_delay: float = 1.0

    # 决策阈值
    auto_pass_threshold: float = 0.92
    auto_reject_threshold: float = 0.92
    human_review_lower: float = 0.70
    image_auto_threshold: float = 0.92

    # Redis（人工复核队列）
    redis_url: str = "redis://localhost:6379/0"

    # PostgreSQL（审核日志）
    database_url: str = "postgresql://localhost:5432/ad_review"

    # 文件上传配置
    upload_dir: str = "uploads"
    max_image_size_mb: int = 10
    max_video_size_mb: int = 500

    # 规则库路径
    rules_dir: str = "src/rules"

    # API 认证
    api_keys: str = ""
    rate_limit_per_minute: int = 100

    # 管理后台认证（默认管理员，首次启动自动创建）
    default_admin_username: str = "admin"
    default_admin_password: str = "Admin@123456"
    bcrypt_rounds: int = 12

    # Session
    session_secret: str = "change-this-secret-in-production"

    # 阿里云内容安全
    aliyun_access_key_id: str = ""
    aliyun_access_key_secret: str = ""
    aliyun_green_endpoint: str = "green-cip.cn-shanghai.aliyuncs.com"
    aliyun_green_enabled: bool = True

    # 音频检测
    audio_check_enabled: bool = True
    audio_max_duration_seconds: int = 300

    # 素材接入 — HTTP Webhook
    ingest_webhook_secret: str = ""
    ingest_field_mapping: str = "{}"

    # 素材接入 — 消息队列
    mq_type: str = "none"
    mq_brokers: str = "localhost:9092"
    mq_topic: str = "ad_creative_submit"
    mq_group_id: str = "ad-review-agent"
    mq_consume_ratio: float = 0.1

    # 日志级别
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
