# 多轮对话（mxh 版）

`bench_multiturn_mxh.py` 是 [多轮对话](multi-turn-dialogue.md) 标准版的扩展变体，在相同的多轮会话负载框架上增加了 **DP 亲和**、**轮间同步增强**（轮间隔控制、心跳抑制、轮间缓存诊断/清空）与**精确长度 prompt 构造**，适合围绕 L1/L3 外部缓存做逐轮对照实验。与标准版共用 24 个参数且语义一致；标准版参数说明见其页面，本页只列差异。

## 下载

[下载 bench_multiturn_mxh.py](../downloads/bench_multiturn_mxh.py){ .md-button download="bench_multiturn_mxh.py" }

下载文件是经过公开审查的客户端副本，保留命令行接口和运行逻辑；不包含部署配置、节点拓扑、存储根目录或历史运行归档。运行环境要求与标准版一致（兼容的 SGLang Python 包、numpy、requests、tqdm）。

## 与标准版的差异

| 类别 | 内容 |
| --- | --- |
| 新增参数 | DP 亲和 2 个、轮间同步与缓存控制 5 个、prompt 复用与精确长度 3 个（见下方参数说明） |
| 移除参数 | `--repeat-count`、`--cache-size-path`（冷/热多遍重复与缓存目录采样由上层自动化框架负责，单跑脚本不再内置） |
| 依赖提示 | `--exact-chat-length` 模式额外 import 脚本同目录的 `repeat_prompt.py` 辅助模块；该模块不在下载包中，未开启此模式时不影响运行 |

## 调用示例

100 客户端、15 轮、首轮 65536 token、每轮追加 512 token 的轮栅栏负载；`--round-gap-s 0` 表示轮与轮之间不额外等待，`--disable-heartbeat` 避免栅栏后的心跳请求在随机 rank 上留下额外 KV。

```bash
python -u bench_multiturn_mxh.py \
  --host <HOST> \
  --port 10015 \
  --model-path <MODEL_PATH> \
  --api-format sglang \
  --num-clients 100 \
  --max-parallel 32 \
  --request-length 65536 \
  --sub-question-input-length 512 \
  --output-length 1 \
  --num-rounds 15 \
  --distribution uniform \
  --request-rate 100 \
  --disable-auto-run \
  --disable-random-sample \
  --enable-round-barrier \
  --round-gap-s 0 \
  --ready-queue-policy random \
  --range-ratio 1 \
  --seed 42 \
  --tag l1_l3 \
  --disable-heartbeat \
  --round-cache-control-url http://<PREFILL_NODE_IP>:30001
```

`--round-cache-control-url` 指向某个 SGLang worker 的服务地址。只有同时开启 `--flush-cache-between-rounds` 或 `--round-cache-metrics-dir` 时，脚本才会在轮栅栏处实际访问该地址（拉取 `/metrics` 或调用 `/flush_cache`）；仅传 URL 而不开这两个开关时，它只被记录进结果 JSONL，不产生请求。

## 参数说明（仅新增参数）

### DP 亲和

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--enable-dp-affinity` | 关闭 | 将 client *i* 固定路由到 `routed_dp_rank = i % dp-size`，使同一会话始终落在同一 DP rank，保证 L1 前缀复用不被轮询打散。 |
| `--dp-size` | `8` | 配合 `--enable-dp-affinity` 使用的 DP world size。 |

### 轮间同步与缓存控制

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--round-gap-s` | `10.0` | 轮栅栏释放下一轮前的等待秒数；`0` 表示不等待。 |
| `--disable-heartbeat` | 关闭 | 跳过栅栏后的心跳请求，避免在随机 rank 上追加 KV 影响下一轮命中。 |
| `--round-cache-control-url` | 空 | worker 服务地址，用于轮间缓存诊断或清空（见调用示例说明）。 |
| `--flush-cache-between-rounds` | 关闭 | 每个非末轮栅栏处对该 worker 调用 `/flush_cache`。 |
| `--round-cache-metrics-dir` | 空 | 每个栅栏处（任何 flush 之前）拉取该 worker 的 `/metrics` 存为 `p_round_<r>.prom`。 |

### prompt 复用与精确长度

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--repeat-prompt` | 关闭 | 每轮原样重发首轮请求，不追加模型输出或后续问题；用于构造确定性的重复命中负载。 |
| `--exact-chat-length` | 关闭 | 按模型 chat 模板构造**恰好** `--request-length` token 的 prompt（含模板 token），避免模板带来的长度漂移。 |
| `--prepared-cache-dir` | 空 | 从该目录复用 exact-length 的 chat/ID 结果缓存，跳过重复构造。 |

!!! note "参数组合约束"

    - `--prepared-cache-dir` 需要 `--exact-chat-length`；
    - `--exact-chat-length` 需要 `--repeat-prompt` 且 `--range-ratio 1`；
    - `--flush-cache-between-rounds` / `--round-cache-metrics-dir` 需要 `--enable-round-barrier` 和 `--round-cache-control-url`；
    - `--round-gap-s` 必须为有限且非负的数。
    违反组合时脚本会直接以参数错误退出。

## 结果解读（相对标准版的增量）

- `summary` 中额外记录 `dp_affinity`、`dp_size`、`heartbeat_disabled`、`round_gap_s`、`flush_cache_between_rounds`、`round_cache_control_url` 与 `cache_maintenance`（每轮缓存操作耗时）。
- 开启轮栅栏时输出逐轮指标：每轮时长、输入吞吐、平均 TTFT、命中率、请求/客户端数，以及 `repeat-prompt` 模式下的 prompt/cached token 最小最大值；逐轮命中率是观察外部缓存逐轮累积的主要指标。
- 其余指标语义（`input_token_throughput`、`cache_hit_rate` 的加权口径等）与标准版一致，见[标准版的结果解读](multi-turn-dialogue.md)。

## 运行前检查

- 目标服务（`<HOST>:<PORT>`）就绪；`<MODEL_PATH>` 在客户端可读并与目标模型 tokenizer 兼容；
- 使用 `--enable-dp-affinity` 时确认 `--dp-size` 与服务端实际 DP 并行度一致；
- 使用缓存诊断/清空时确认 worker 地址可直连（不经 Router 的 `pd-disaggregation` 代理）；
- 与标准版相同：服务端需开启 cache-report，客户端的 `cache_hit_rate` 才有实际意义。
