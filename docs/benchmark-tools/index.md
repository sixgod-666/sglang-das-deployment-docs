# Benchmark Tools

本节提供两个经过公开审查的 **HTTP 基准客户端**。它们向已经启动的推理服务发送请求，不负责拉起模型、SGLang、Router 或缓存服务。

| 目标场景 | 工具 | 适用情况 |
| --- | --- | --- |
| 多用户多轮对话、会话历史前缀复用、轮间同步 | [多轮对话](multi-turn-dialogue.md) | SGLang 原生 `/generate` 或 OpenAI 兼容 `/v1/chat/completions` |
| 长文档 warmup/query、全量或部分前缀命中 | [长文档 QA](long-document-qa.md) | OpenAI 兼容服务；可用于 SGLang 或其他兼容服务 |

## 使用前提

1. 确认目标服务已健康并能接收请求；工具只负责压测客户端侧的请求生成、并发控制和结果统计。
2. 在具有相应 Python 依赖、且可访问 tokenizer/模型目录的运行环境中执行。多轮工具依赖兼容的 SGLang Python 包；长文档工具依赖其脚本列出的 Python 包。
3. 将示例中的 `<HOST>`、`<PORT>`、`<MODEL_NAME>`、`<MODEL_PATH>` 替换为当前环境值。不要将内部地址、工作目录、存储路径或日志归档名提交到公开 issue、文档或命令示例。
4. 需要比较缓存命中时，应让服务端启用相应的 cache-report 能力；否则客户端无法可靠获得 `cached_tokens`。

关于通用部署占位符的说明见[部署占位符约定](../reference/deployment-placeholders.md)。
