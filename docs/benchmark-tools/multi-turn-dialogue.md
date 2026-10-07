# 多轮对话

`bench_multiturn.py` 用于构造多个并发客户端的多轮会话负载，测量服务在会话历史持续增长时的延迟、吞吐和前缀缓存命中。默认请求 SGLang 原生 `/generate`；传入 `--api-format openai` 后请求 OpenAI 兼容的 `/v1/chat/completions`。

## 下载

[下载 bench_multiturn.py](../downloads/bench_multiturn.py){ .md-button download="bench_multiturn.py" }

下载文件是经过公开审查的客户端副本，保留命令行接口和运行逻辑；不包含部署配置、节点拓扑、存储根目录或历史运行归档。请在安装了兼容 SGLang Python 依赖的环境中运行。

## 调用示例

下面示例构造 32 个客户端、每客户端 15 轮的固定内容工作负载。`--disable-random-sample` 与固定 `--seed` 使后续复跑生成相同请求内容，适合观察外部缓存的冷/热差异。

```bash
python3 bench_multiturn.py \
  --disable-random-sample --seed 1 \
  --num-clients 32 --num-rounds 15 --max-parallel 32 \
  --request-length 65536 --sub-question-input-length 512 \
  --output-length 1 --request-rate 16 \
  --ready-queue-policy random --enable-round-barrier --disable-auto-run \
  --model-path <MODEL_PATH> \
  --host <HOST> --port <PORT> \
  --log-file multi_turn_result.jsonl
```

!!! note "长度边界"

    若第 0 轮的基础长度为 `base`，第 `r` 轮请求长度约为
    `base + r × (sub-question-input-length + 2)`。`+2` 来自会话追加结构。运行前应按最大轮次确认该长度低于服务端的实际输入上限。

## 参数说明

### 并发、轮次与调度

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--num-clients` | `256` | 并发逻辑客户端数。每个客户端维护独立会话历史。 |
| `--num-rounds` | `5` | 每客户端固定轮数。 |
| `--min-rounds` / `--max-rounds` | `0` / `0` | 为每客户端指定轮数范围；两者均为 `0` 时使用 `--num-rounds`。 |
| `--max-parallel` | `128` | 同时在途的最大请求数。 |
| `--enable-round-barrier` | 关闭 | 只有所有客户端的第 *i-1* 轮完成后，才发送第 *i* 轮；适合逐轮比较缓存状态。 |
| `--ready-queue-policy` | `random` | ready 队列弹出策略：`random` 或 `fifo`。 |
| `--request-rate` | `1.0` | 平均发请求速率（请求/秒）。 |
| `--distribution` | `poisson` | 请求间隔分布：`poisson` 或 `uniform`。 |
| `--disable-auto-run` | 关闭 | 禁止脚本自动测试一组请求速率；固定实验形状时通常应开启。 |

### 请求形状与可复现性

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--request-length` | `512` | 首轮基础输入长度（token）。 |
| `--sub-question-input-length` | `0` | 每一后续轮追加问题的长度；为 `0` 时使用 `--request-length`。 |
| `--output-length` | `64` | 每次请求的最大生成 token 数。 |
| `--range-ratio` | `1.0` | prompt 与输出长度的波动比例；`1.0` 表示不引入波动。 |
| `--seed` | `1` | 随机种子。 |
| `--dataset-path` | 空 | 本地数据集路径；为空时使用合成 token 内容。 |
| `--disable-random-sample` | 关闭 | 禁止从 ShareGPT 样本随机抽取。缓存复现实验应开启，并固定 `--seed`，以保证 prompt 可逐 token 重现。 |

### 服务端与 API

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--host` / `--port` | `localhost` / `30000` | 目标服务地址与端口。 |
| `--model-path` | `meta-llama/Llama-3.1-8B-Instruct` | 客户端 tokenizer 所用的 Hugging Face 兼容模型路径。 |
| `--api-format` | `sglang` | `sglang` 使用原生 `/generate`；`openai` 使用 `/v1/chat/completions`。 |
| `--lora-path` | 空 | 单个 LoRA 适配器路径；仅在目标服务和运行环境均支持时使用。 |

### 重复、缓存采样与结果

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--repeat-count` | `1` | 重复整个工作负载的次数。首遍前调用服务的 `/flush_cache`；后续遍不重启服务、不清外部缓存，适合冷/热对比。 |
| `--cache-size-path` | 未设置 | 每遍结束执行 `du -sh` 采样的目录。仅在本地有权读取且该目录确实属于目标缓存时设置。 |
| `--log-file` | `performance_metrics.jsonl` | 结果 JSONL；每个 repeat 追加一行。 |
| `--tag` | 空 | 写入 JSONL 的运行标记，便于区分实验。 |

## 结果解读

- `input_token_throughput` 是完整逻辑会话上下文的输入 token 总数除以 benchmark 墙钟时间。多轮热命中时，分子仍包含完整历史，因此它不等同于 GPU 实际 Prefill 吞吐。
- `output_token_throughput` 是完成 token 数除以墙钟时间；当输出固定为 1 token 时，可近似观察完成 QPS。
- `cache_hit_rate` 是服务端返回的 `usage.prompt_tokens_details.cached_tokens` 按输入 token 加权后的比例。若服务端/Router 未返回该字段，客户端结果为 `0` 并不能证明未命中，应先确认服务端的 cache-report 配置。
- 结果还包括 TTFT、ITL、总延迟及其平均值、P90、P99、中位数和最大值。输出仅有 1 token 时没有 token 间间隔，TPOT/ITL 为 0 是正常现象。

## 运行前检查

- 目标服务已就绪，且 `<HOST>:<PORT>` 对客户端可达；
- `<MODEL_PATH>` 在客户端容器/主机可读，并与目标模型 tokenizer 兼容；
- `--log-file` 的父目录可写；
- 复跑前确认没有残留 benchmark 进程；服务退出时，客户端可能继续重试或空转，应及时终止并先排查服务端。
