"""运行投递网关。

隔离 API 层与 Celery/事件总线的耦合:
- enqueue_run: 通过 Celery 投递 run_agent_task,失败时抛出明确异常。

依赖的外部符号(由 tasks 作者提供):app.tasks.celery_app.run_agent_task,
是一个支持 .delay(payload: dict) 的 Celery 任务。导入失败时给出清晰错误。
"""

from __future__ import annotations

import logging

from app.core.logging import get_logger, log_with_fields

logger = get_logger(__name__)


class RunnerUnavailableError(RuntimeError):
    """任务队列不可用(Celery 任务未实现或 broker 异常)。"""


def _load_run_task():
    """惰性加载 Celery run_agent_task,缺失时抛出可读异常。"""
    try:
        # run_agent_task 实际定义在 agent_tasks(celery_app 仅 include 它)
        from app.tasks.agent_tasks import run_agent_task
    except Exception as exc:  # 模块或符号缺失
        raise RunnerUnavailableError(f"无法加载 run_agent_task: {exc}") from exc
    return run_agent_task


def enqueue_run(payload: dict) -> None:
    """将一次运行投递到 Celery 队列。

    payload 至少包含 agent_run_id / conversation_id / trace_id / message。
    投递失败抛出 RunnerUnavailableError,由调用方转换为 503。
    """
    task = _load_run_task()
    try:
        # run_agent_task 的签名是 (agent_run_id, conversation_id, trace_id,
        # user_message) 四个位置参数;payload 用 message 承载用户消息,这里
        # 解包对齐(chat 路由约定的载荷字段)。
        task.delay(
            agent_run_id=payload["agent_run_id"],
            conversation_id=payload["conversation_id"],
            trace_id=payload["trace_id"],
            user_message=payload["message"],
            user_id=payload.get("user_id"),
            metadata=payload.get("metadata") or {},
            wc2026_context=payload.get("wc2026_context"),
        )
    except Exception as exc:
        log_with_fields(
            logger,
            logging.ERROR,
            "投递 run_agent_task 失败",
            agent_run_id=payload.get("agent_run_id"),
            error=str(exc),
        )
        raise RunnerUnavailableError(f"投递任务失败: {exc}") from exc
