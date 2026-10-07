# DeepSeek-V4-W4A8-DP8TP8：GPU-only 与 CPU 缓存

本页记录 **DeepSeek-V4-W4A8-DP8TP8** 在 PD 分离拓扑下的两种缓存部署形态：GPU-only（L1）与 CPU 内存（L1 + L3）。内容由已验证的自动化测试配置整理为可公开复用的 Recipe；节点、分布式初始化地址、日志和工作目录均使用占位符。

!!! warning "公开边界"

    本页不是内部启动脚本或配置文件的原样归档。请将占位符替换为目标环境的值，并在目标容器中重新核对软件栈、模型 revision、网卡和启动参数。

## 1. 共同拓扑与软件栈

| 项目 | 约定 |
| --- | --- |
| 模型 | `DeepSeek-V4-W4A8-DP8TP8` |
| 量化 | W4A8，`slimquant_marlin` |
| Prefill（P） | `TP8 / PP1 / DP1 / EP8`，启用 CP `interleave` |
| Decode（D） | `TP8 / PP1 / DP8 / EP8` |
| SGLang 端口 | P/D 均为 `30001` |
| Bootstrap 端口 | `8998` |
| Router 端口 | `10015` |
| 模型路径 | `/ai_data/models/DeepSeek-V4-W4A8-DP8TP8` |
| 共享配置 | `/guofy/packages/DS-V4-W4A8/config/topo.config`、`deepep_config.json` |

受控版本记录沿用站点级配置：

- SGLang DAS：`{{ stack.sglang_das.branch }}`，commit `{{ stack.sglang_das.validated_commit }}`
- Mooncake、wheel、容器和模型 revision：以目标环境重新采集的指纹为准

## 2. 自动化测试配置映射

自动化测试框架中的配置应使用独立的 DP8TP8 配置项，并将以下变量替换为目标环境值：

```ini
[common]
model_path = /ai_data/models/DeepSeek-V4-W4A8-DP8TP8
sglang_port = 30001
router_port = 10015
bootstrap_port = 8998
p_node_ip = <PREFILL_NODE_IP>
d_node_ip = <DECODE_NODE_IP>
mc_node_ip = <PREFILL_NODE_IP>
ib_devices = shca_0,shca_1,shca_2,shca_3
p_dist_init_addr = <PREFILL_DIST_INIT_ADDR>
d_dist_init_addr = <DECODE_DIST_INIT_ADDR>
topo_config = /guofy/packages/DS-V4-W4A8/config/topo.config
deepep_config = /guofy/packages/DS-V4-W4A8/config/deepep_config.json
```

两种形态的关键差异：

| 形态 | Mooncake 进程 | `global_segment_size` | DFS / Offload |
| --- | --- | --- | --- |
| GPU-only | 不启动 master/client | 不适用 | 关闭 |
| CPU | 启动 master/client | `200GB` | 关闭 |

共同的 SGLang 并行参数为：

```text
Prefill: --tp-size 8 --pp-size 1 --dp 1 --ep 8
         --enable-prefill-cp --cp-strategy interleave
Decode:  --tp-size 8 --pp-size 1 --dp 8 --ep 8
```

## 3. GPU-only（L1）

### 3.1 启动顺序

GPU-only 不启动 Mooncake master/client。按 **Prefill → Decode → Router** 顺序启动，并确认 P/D 的真实请求链路均已就绪后再开始压测。

### 3.2 Prefill

```bash
export SGLANG_SET_CPU_AFFINITY=1
export SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT=1200
export SGLANG_USE_LIGHTOP=1
export SGLANG_ROCM_USE_AITER_MOE=1
export SGLANG_W4A8_EP_USE_GROUPGEMM=1
export NCCL_IB_HCA=shca_0,shca_1,shca_2,shca_3
export NCCL_NET_PLUGIN=shca
export NCCL_PLUGIN_P2P=ib
export NCCL_SOCKET_IFNAME=ib0
export GLOO_SOCKET_IFNAME=ib0

sglang serve \
  --model-path /ai_data/models/DeepSeek-V4-W4A8-DP8TP8 \
  --trust-remote-code \
  --quantization slimquant_marlin \
  --host 0.0.0.0 --port 30001 \
  --tp-size 8 --pp-size 1 --dp 1 --ep 8 \
  --enable-prefill-cp --cp-strategy interleave \
  --moe-dense-tp-size 1 --moe-a2a-backend megamoe \
  --deepep-mode normal \
  --deepep-config /guofy/packages/DS-V4-W4A8/config/deepep_config.json \
  --init-expert-location /guofy/packages/DS-V4-W4A8/need/placement_candidate.json \
  --dist-init-addr <PREFILL_DIST_INIT_ADDR> --nnodes 1 --node-rank 0 \
  --disaggregation-mode prefill \
  --disaggregation-transfer-backend mooncake \
  --disaggregation-bootstrap-port 8998 \
  --disaggregation-ib-device shca_0,shca_1,shca_2,shca_3 \
  --enable-unified-cache-external-linker \
  --unified-cache-external-linker-backend mooncake \
  --disable-overlap-schedule \
  --enable-cache-report
```

### 3.3 Decode

```bash
export SGLANG_SET_CPU_AFFINITY=1
export SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT=1200
export SGLANG_DISAGGREGATION_ALL_CP_RANKS_TRANSFER=1
export SGLANG_USE_LIGHTOP=1
export NCCL_IB_HCA=shca_0,shca_1,shca_2,shca_3
export NCCL_NET_PLUGIN=shca
export NCCL_PLUGIN_P2P=ib
export NCCL_SOCKET_IFNAME=ib0
export GLOO_SOCKET_IFNAME=ib0

sglang serve \
  --model-path /ai_data/models/DeepSeek-V4-W4A8-DP8TP8 \
  --trust-remote-code \
  --quantization slimquant_marlin \
  --host 0.0.0.0 --port 30001 \
  --tp-size 8 --pp-size 1 --dp 8 --ep 8 \
  --enable-dp-attention --enable-dp-lm-head \
  --load-balance-method auto \
  --moe-a2a-backend deepep --moe-runner-backend deep_gemm \
  --deepep-mode low_latency \
  --dist-init-addr <DECODE_DIST_INIT_ADDR> --nnodes 1 --node-rank 0 \
  --disaggregation-mode decode \
  --disaggregation-bootstrap-port 8998 \
  --disaggregation-ib-device shca_0,shca_1,shca_2,shca_3 \
  --skip-server-warmup \
  --enable-cache-report
```

### 3.4 Router

```bash
python3 -m sglang_router.launch_router \
  --prefill http://<PREFILL_NODE_IP>:30001 8998 \
  --decode http://<DECODE_NODE_IP>:30001 \
  --pd-disaggregation --host 0.0.0.0 --port 10015 \
  --policy round_robin \
  --worker-startup-timeout-secs 18000 \
  --worker-startup-check-interval 10 \
  --health-check-endpoint /v1/models \
  --request-timeout-secs 18000
```

## 4. CPU（L1 + L3）

### 4.1 启动顺序

按 **Mooncake master → Mooncake client → Prefill → Decode → Router** 启动。CPU 方案只使用 Mooncake CPU 内存段，不启用 DFS 或 SSD Offload。

### 4.2 Mooncake master 与 client

在 P 节点启动 master：

```bash
mooncake_master \
  --logtostderr \
  --eviction_high_watermark_ratio=0.8 \
  --enable_http_metadata_server \
  --eviction_ratio=0.1
```

在 P 节点启动 client：

```bash
export MOONCAKE_LOCAL_HOSTNAME=<PREFILL_NODE_IP>
export MC_STORE_CLIENT_METRIC=1
export MC_STORE_ENABLE_SESSION_CACHE=0
export MC_STORE_ENABLE_DFS_PREFETCH=0

mooncake_client \
  --host=<PREFILL_NODE_IP> \
  --global_segment_size=200GB \
  --master_server_address=<PREFILL_NODE_IP>:50051 \
  --metadata_server=P2PHANDSHAKE \
  --protocol=rdma \
  --device_names=shca_0,shca_1,shca_2,shca_3 \
  --port=50052 \
  --logtostderr --enable_http_server --http_port=9300
```

### 4.3 Prefill、Decode 与 Router

Prefill、Decode 和 Router 的 SGLang 参数与 GPU-only 相同，另需确保两端使用以下 CPU 缓存设置：

```bash
export MOONCAKE_MASTER=<PREFILL_NODE_IP>:50051
export MOONCAKE_PROTOCOL=rdma
export MOONCAKE_LOCAL_HOSTNAME=<PREFILL_NODE_IP>
export MC_STORE_ENABLE_SESSION_CACHE=0
export MC_STORE_ENABLE_DFS_PREFETCH=0
export MC_STORE_MEMCPY=1
export MC_STORE_PINNED_RESTORE_ARENA_SIZE_BYTES=6442450944
export SGLANG_MOONCAKE_READ_PLAN=1
export SGLANG_MOONCAKE_FINAL_POLL_TIMEOUT_MS=400
```

然后按[第 3 节](#3-gpu-onlyl1)的 Prefill、Decode、Router 命令启动服务；将模型路径、初始化地址和节点占位符替换为目标环境值。

## 5. 服务验证

`/v1/models` 只能作为基础检查，不能作为唯一就绪条件。至少完成以下检查：

```bash
curl -sf http://<PREFILL_NODE_IP>:30001/v1/models
curl -sf http://<DECODE_NODE_IP>:30001/v1/models
curl -sf http://<PREFILL_NODE_IP>:10015/v1/models
curl -sS http://<PREFILL_NODE_IP>:10015/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"DeepSeek-V4-W4A8-DP8TP8","messages":[{"role":"user","content":"简短回答：缓存测试就绪吗？"}],"max_tokens":16}'
```

预期结果：P/D 服务均存活，Router 能完成一次真实 PD 请求；随后再运行自动化 case，并从请求侧结果、服务日志和 cache report 交叉核对命中率。

## 6. 故障排查

| 现象 | 首先检查 | 处理 |
| --- | --- | --- |
| Router 已返回但真实请求失败 | P/D 日志、bootstrap 连接和实际请求 | 不要把 `/v1/models` 当作唯一就绪门 |
| CPU 方案启动后无缓存命中 | client 的 `200GB` segment、master 地址和 RDMA 设备 | 先确认 client 已注册，再检查 linker 日志 |
| DP8 启动显存或通信异常 | `--tp-size/--dp/--ep` 与 P/D 初始化地址 | P 为 DP1，D 为 DP8；逐项核对节点角色 |
| worker 被误判退出 | `SGLANG_UVICORN_WORKER_HEALTHCHECK_TIMEOUT` | 按目标镜像实际情况设置并重新验证 |
| 命中率与预期不符 | `--enable-cache-report`、请求结果和服务日志 | 仅使用完整请求链路数据下结论 |
