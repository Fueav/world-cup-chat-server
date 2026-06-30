# 2026-06-30 WC2026 效果评估根因与修复报告

## 最终结论

- 测试环境: `https://api-chris-world-cup-chat-server-concise-zai.dkhost.vixmk-yo.org`
- 服务端 commit: `5ad98d91354a635b2d2805e66493fa179b20e150`
- 最终评估结果: `16/16` deterministic pass, high-risk `8/8`, LLM judge `16/16`
- 自动报告: `docs/evaluations/2026-06-30-dockerhost-worldcup-effect-report-20260630T110211Z.md`
- 结果 JSON: `docs/evaluations/2026-06-30-dockerhost-worldcup-effect-results-20260630T110211Z.json`

## 主要问题与代码原因

1. 中心化数据未配置导致工具退化
   - 现象: 已解锁当前比赛问题拿不到推荐规则、实力指数、k/rho/stage 口径。
   - 代码原因: `WC2026_AGENT_API_BASE_URL` 为空时仍构造 `Wc2026CentralDataClient`; 工具调用时再抛 `WC2026_AGENT_API_NOT_CONFIGURED`, 被统一转为 `central_unavailable`。
   - 修复: `build_wc2026_agent_data_service()` 在 base URL 为空时返回无 client service; 当前比赛数值继续 fail-closed, 公开方法论走本地 fallback, 只提供 4pp、1.70-2.40、9D/0-100、k=0.943、rho=-0.15 和赛段校准等公开口径。

2. 仅靠 prompt 约束无法稳定控制输出风格
   - 现象: 模型仍输出 Markdown 表格和长答案, live eval 出现 style table/length failures。
   - 代码原因: 原先 `finalize_assistant_answer()` 主要追加风险提示; 对已流式发出的表格/冗长 token 无法回收。
   - 修复: 在 `StreamingOutputGuardrail` 中提前处理用户可见 token: 表格行转换为普通短行、删除表格脚手架/横线、默认冗长回答做长度钳制。

3. 风格钳制一度遮蔽截断检测
   - 现象: 长输出被裁短后可能以句号结尾, 使 `is_likely_truncated_answer()` 无法识别原始 provider cutoff。
   - 代码原因: 完整性检查只看过滤后的 `emitted_chunks`。
   - 修复: `AgentOrchestrator` 同时保留 raw model text; 截断判断使用 raw text, 展示和持久化使用过滤后的简洁文本。

4. DockerHost API/worker provider 默认值不一致
   - 现象: API 已配置 `medium/2048`, worker 仍可能走 `max/1024`, 造成异步路径与实时路径行为不一致。
   - 代码原因: `dockerhost/compose.yaml` worker/reaper 默认值未同步。
   - 修复: 统一 DockerHost provider 默认值, 并在 `scripts/check_dockerhost_production_config.py` 增加漂移检查。

5. 评测规则存在等价格式误判
   - 现象: `1.7–2.4`、`0–0`、`0–100`、"当前绑定的比赛" 等正确表达被旧正则判失败。
   - 修复: 只放宽等价格式匹配, 不放宽安全边界、必需工具或禁止模式。

## 剩余观察

- 最终报告仍记录 `stream_error_count=1`, 但 `recovered_run_completed_count=1`; 即 SSE 传输曾出现 incomplete chunked read, 恢复查询拿到 `RUN_COMPLETED=SUCCEEDED`, 没有造成内容失败。
- 测试环境 `WC2026_AGENT_API_BASE_URL` 仍为空; 因此当前比赛具体数值仍不可用。当前修复只保证数值 fail-closed、公开方法论可解释, 不伪造当前比赛付费/私有数值。

## 验证证据

- Focused tests: `tests/test_chat_behavior_policy.py tests/test_wc2026_agent_data.py tests/test_production_readiness.py tests/live_eval/test_wc2026_effect_cases.py tests/test_agent_factory.py tests/test_orchestrator.py`
- Release gate: `AI_BOUNDARY_APPROVED=1 SPEC_CONTRACT_APPROVED=1 make verify-release`
- Final live eval: `docs/evaluations/2026-06-30-dockerhost-worldcup-effect-report-20260630T110211Z.md`
