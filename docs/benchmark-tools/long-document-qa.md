# 长文档 QA

`long_doc_qa.py` 先对每篇文档执行 warmup，再发送 query 轮，用于测量长上下文的缓存加载、全命中/未命中混合和部分前缀命中。它是 OpenAI 兼容 HTTP 客户端：可用于 SGLang，也可用于其他兼容服务；它本身不启动服务。

## 下载

[下载 long_doc_qa.py](../downloads/long_doc_qa.py){ .md-button download="long_doc_qa.py" }

该文件是经过公开审查的客户端副本，保留原有命令行接口和 Apache-2.0 版权标识；不包含内部部署信息、运行目录或数据归档。

## 调用示例

以下是常用的长文档请求级 `9:1` 命中设计：生成 100 篇 64K 文档，先 warmup，再以顺序重复方式发送单遍 query；每 10 个 query 中 9 个复用其文档，1 个注入随机前缀形成全未命中。

```bash
python3 long_doc_qa.py \
  --model <MODEL_NAME> \
  --host <HOST> --port <PORT> \
  --document-length 65536 --num-documents 100 \
  --output-len 1 --repeat-count 1 --repeat-mode tile \
  --max-inflight-requests 32 --hit-miss-ratio 9:1 \
  --text-source random \
  --tokenizer /ai_data/models/<MODEL_DIRECTORY> \
  --json-output --output long_doc_responses.jsonl
```

也可用完整 URL 指向服务。`--base-url` 与 `--host`/`--port` **互斥**：

```bash
python3 long_doc_qa.py \
  --model <MODEL_NAME> --base-url http://<HOST>:<PORT>/v1 \
  --document-length 65536 --num-documents 100 \
  --output-len 1 --repeat-count 1 --repeat-mode tile \
  --max-inflight-requests 32 --hit-miss-ratio 9:1
```

## 参数说明

### 文档与负载

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--document-length` | `20000` | 每篇文档的目标 token 数。chat 请求还会加入模板 token；在 `<h>` 总长度部分命中模式中，它表示 query 总长度。 |
| `--num-documents` | `8` | 生成并 warmup 的文档数。 |
| `--output-len` | `100` | 每个 prompt 的最大输出 token 数。 |
| `--repeat-count` | `2` | 每个 query prompt 的重复次数。 |
| `--repeat-mode` | `random` | `random` 打乱 prompt；`tile` 按完整文档列表顺序重复；`interleave` 连续重复同一 prompt 后再处理下一篇。 |
| `--shuffle-seed` | `0` | `random` 重复模式的随机种子。 |
| `--max-inflight-requests` | `2` | 最大在途请求数。 |
| `--sleep-time-after-warmup` | `0.0` | warmup 与 query 轮之间的等待秒数。 |

### 服务端、API 与输出

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--host` / `--port` | 未设置 | 目标服务地址与端口。必须与 `--base-url` 二选一。 |
| `--base-url` | 未设置 | OpenAI 兼容服务基地址；与 `--host`/`--port` 互斥。 |
| `--model` | `auto` | 模型名；端点支持 OpenAI `/models` 时可使用 `auto`。 |
| `--completions` | 关闭 | 改用 completions API；默认使用 chat completions。 |
| `--eos-token-id` | 未设置 | 对该 EOS token 施加 bias，尽量保证指定的输出长度。 |
| `--output` | stdout | 将每个响应写入的文件；未设置时输出到标准输出。 |
| `--json-output` | 关闭 | 将 benchmark 汇总打印为一行 JSON，便于上层自动化采集。 |
| `--visualize` | 关闭 | 使用 matplotlib 可视化结果。 |

### 请求级命中设计：`hit:miss`

`--hit-miss-ratio 9:1` 采用请求级混合：每个周期的前 9 个 query 完整复用 warmup 文档，最后 1 个 query 注入随机前缀而成为全未命中。

| 形式 | 含义 |
| --- | --- |
| 不传、`1:0` 或 `100:0` | query 原样复用 warmup 文档，设计上全命中。 |
| `0:1` | 所有 query 都注入随机前缀，设计上全未命中。 |
| `9:1`、`99:1` | 请求级全命中/全未命中混合。 |

### 部分前缀命中：`<h>` 与 `append:<h>`

| 形式 | 含义 |
| --- | --- |
| `--hit-miss-ratio 0.99` | **总长度模式**。每个 query 固定为 `--document-length` token；warmup 是该 query 页对齐、较短的前缀。 |
| `--hit-miss-ratio append:0.99` | **追加模式**。warmup 保持文档原长度，query 在其后追加新文本，使 warmup 约占 query 的 99%。 |

部分命中模式的限制：

- 仅适用于 `--text-source random`，且必须使用 chat API（不能传 `--completions`）；
- `<h>` 必须在 `0 < h < 1` 范围内；`1` 或 `1.00` 无法构造尾部文本，需改用 `1:0` 表示全命中；
- `--page-size` 必须等于服务端真实 KV page size。工具据此向下对齐可恢复 warmup 前缀，并保证每篇文档首页唯一；
- 该工具会输出 nominal 与实际构建的设计命中率。部分命中请求会关闭 thinking，以避免多轮模板渲染破坏前缀关系。

### 文本生成与页对齐

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--text-source` | `random` | `random`：生成 token-id 序列，decode 成随机文本；`hi`：使用传统重复 `hi` 文档，适合作为对照。 |
| `--tokenizer` | `/ai_data/models/DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel` | `random` 文本使用的 tokenizer.json 或包含它的模型目录。应替换为当前模型的可读路径。 |
| `--token-id-pattern` | `sequential` | `sequential`：循环 token-id 段并用不同起点；`random`：每个 id 独立均匀采样；`repeat`：唯一首 id 后重复 `--repeat-token-id`。 |
| `--token-seed` | `1` | 文档生成 RNG 种子，不影响 `--shuffle-seed` 控制的 query 打乱/未命中注入序列。 |
| `--vocab-size` | `128000` | token-id 的抽取上限；应与 tokenizer 词表匹配。 |
| `--repeat-token-id` | `19346` | `repeat` 模式的正文 token id。 |
| `--text-length-tolerance` | `8` | 随机文本 decode 后再 encode 的目标长度容差。总长度部分命中强制精确构造。 |
| `--page-size` | `256` | 服务器 KV page size；部分命中设计依赖它进行页对齐。 |

## 结果与失败语义

- 汇总提供输入、输出和总 token 吞吐，以及 `prompt_tokens / TTFT` 的平均/峰值输入速率。
- 工具从服务端 `usage.prompt_tokens_details.cached_tokens` 读取实际缓存 token。服务端应启用 cache-report；如果所有响应都不提供该字段，结果会标为未知并给出警告，不能把缺失解释为 0 命中。
- 成功响应必须具有有效 `usage`、`finish_reason`（`stop` 或 `length`）、正的 prompt token 和至少一个 completion token。任一请求不满足时，工具仍会写出结果，但进程以退出码 `2` 结束，便于自动化拒收不完整 run。

## 运行前检查

- 服务健康、客户端可访问 `<HOST>:<PORT>` 或 `<base-url>`；
- 模型名可由服务接受，随机文本模式的 tokenizer 在客户端环境可读；
- 部分命中实验前核对服务端 page size，并确认服务端已开启 cache-report；
- 结果输出路径可写，且没有把内部路径、地址或生产日志名复制进公开命令或结果文件名。
