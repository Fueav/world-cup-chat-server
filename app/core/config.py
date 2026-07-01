"""应用配置。

基于 pydantic-settings 从环境变量 / .env 读取配置,提供带 lru_cache
的 get_settings() 单例访问器。所有配置项均有默认值,保证零外部依赖即可启动。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置。字段名对应大写下划线环境变量(大小写不敏感)。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- 数据库 / 中间件 ---
    db_url: str = "postgresql+asyncpg://postgres:postgres@localhost:55432/agent"
    db_pool_size: int = 20
    db_max_overflow: int = 0
    db_pool_pre_ping: bool = False
    db_pool_recycle_s: int = 1800
    redis_url: str = "redis://localhost:55379/0"

    # --- Celery ---
    celery_broker_url: str = "redis://localhost:55379/1"
    celery_result_backend: str = "redis://localhost:55379/2"

    # --- LLM provider 选择 ---
    # 运行时(app.runtime)由 PydanticAI 驱动 agentic loop,按下方选择原生 model:
    #   mock      -> FunctionModel(零 key 确定性中文回答,无需任何外部依赖)
    #   openai    -> OpenAIChatModel(openai_base_url/openai_model)
    #   qwen      -> OpenAIChatModel(qwen_base_url/qwen_model,DashScope 兼容端点)
    #   zai       -> OpenAIChatModel(zai_base_url/zai_model,Z.AI GLM-5.2)
    #   anthropic -> AnthropicModel(anthropic_model)
    #   gemini    -> GoogleModel(gemini_model)
    llm_provider: str = "mock"  # mock | openai | qwen | zai | anthropic | gemini
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_api_key_file: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_api_key_file: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    # 通义千问 Qwen:走 DashScope 的 OpenAI 兼容端点(key 见 dashscope_api_key)
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-plus"
    # Z.AI GLM:走 OpenAI 兼容端点(key 见 zai_api_key)
    zai_base_url: str = "https://api.z.ai/api/paas/v4/"
    zai_model: str = "glm-5.2"
    zai_api_key: str = ""
    zai_api_key_file: str = ""
    zai_thinking_type: str = "enabled"  # enabled | disabled
    zai_reasoning_effort: str = "medium"
    zai_tool_stream: bool = True
    # Google Gemini(key 见 gemini_api_key)
    gemini_model: str = "gemini-2.5-flash"

    # --- LiteLLM 统一网关(多厂商:OpenAI / Claude / Qwen / Gemini ...) ---
    # 主模型必须带 provider 前缀,如 openai/gpt-4o、anthropic/claude-sonnet-4-6、
    # gemini/gemini-2.5-flash、dashscope/qwen-plus。
    litellm_model: str = "openai/gpt-4o"
    # 跨厂商 fallback 链:逗号分隔,主模型失败时依次降级。
    # 例:anthropic/claude-sonnet-4-6,gemini/gemini-2.5-flash,dashscope/qwen-plus
    litellm_fallbacks: str = ""
    # 各厂商 key(litellm 通过标准环境变量读取;留空则沿用已注入 os.environ 的值)
    gemini_api_key: str = ""
    gemini_api_key_file: str = ""
    dashscope_api_key: str = ""  # 通义千问 Qwen (DashScope)
    dashscope_api_key_file: str = ""
    provider_secret_strict: bool = True

    # --- WC2026 internal agent data API ---
    wc2026_agent_api_base_url: str = ""
    wc2026_agent_api_key: str = ""
    wc2026_agent_api_timeout_s: float = 3.0

    @property
    def litellm_fallback_list(self) -> list[str]:
        """把逗号分隔的 fallback 配置解析为去空白的模型列表。"""
        return [m.strip() for m in self.litellm_fallbacks.split(",") if m.strip()]

    # --- 运行参数 ---
    rate_limit_per_min: int = 60
    max_turns: int = 10
    retrieval_top_k: int = 5
    embedding_dim: int = 256
    request_timeout_s: float = 60.0
    chat_runtime_mode: str = "auto"  # celery | realtime | auto
    realtime_runner_max_concurrency: int = 1000
    provider_rate_limit_enabled: bool = True
    provider_rate_limit_fail_open: bool = False
    provider_rate_limits_json: str = "{}"
    provider_default_rpm: int = 100000
    provider_default_tpm: int = 1000000
    provider_default_max_output_tokens: int = 8192
    provider_realtime_preflight_timeout_ms: int = 100
    provider_realtime_gate_wait_budget_ms: int = 1000
    provider_realtime_degrade_to_batch: bool = True
    run_max_runtime_s: float = 300.0
    stream_maxlen: int = 1000
    metrics_enabled: bool = True
    reaper_enabled: bool = True
    reaper_interval_s: float = 30.0
    reaper_stale_after_s: int = 300
    reaper_max_attempts: int = 3
    worker_pool: str = "prefork"
    worker_concurrency: int = 2

    # --- RAG / Embedding ---
    rag_enabled: bool = True
    rag_vector_store: str = "memory"  # memory | pgvector
    rag_default_top_k: int = 5
    rag_max_top_k: int = 10
    rag_query_timeout_ms: int = 1500
    rag_score_threshold: float = 0.0
    rag_max_context_chars: int = 6000
    rag_chunk_size: int = 400
    rag_chunk_overlap: int = 80
    rag_index_version: str = "v1"
    rag_metadata_max_bytes: int = 8192
    rag_admin_user_ids: str = ""
    rag_default_knowledge_base_id: str = ""
    rag_internal_owner_user_id: str = ""
    rag_allow_client_knowledge_base_id: bool = False
    embedding_provider: str = "hash"  # hash | openai | gemini
    embedding_model: str = "hash"
    embedding_api_key: str = ""
    embedding_api_key_file: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_batch_size: int = 64
    embedding_timeout_s: float = 30.0

    # --- 服务 ---
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """返回进程内缓存的 Settings 单例。"""
    return Settings()
