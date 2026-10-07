# DeepSeek-V4-W4A8-DP8TP8

本页按缓存层级维护 **PD 分离** 的 GPU-only（L1）、Mooncake CPU（L1 + L3）和 Mooncake DFS（L1 + L3 + L4）Recipe。名称中的 DP8TP8 表示 Prefill 与 Decode 均为 `TP8 / DP8 / EP8`；GPU-only 与 CPU 的 Prefill 均不启用 CP。

## 1. 受控软件栈

| 组件 | 当前记录 |
| --- | --- |
| SGLang DAS 分支 | `{{ stack.sglang_das.branch }}` |
| SGLang DAS 已验证 commit | `{{ stack.sglang_das.validated_commit }}` |
| Mooncake commit | `{{ stack.mooncake.commit }}` |
| wheel SHA256 | `{{ stack.wheel.sha256 }}` |
| 镜像 digest | `{{ stack.container_image.digest }}` |
| 模型 revision | `{{ stack.model.revision }}` |

!!! warning "版本记录边界"

    上表是站点级待补全的受控版本记录，并不等同于历史 Run 的完整环境指纹。复现前应在目标容器内重新采集 wheel、镜像和 Mooncake 的实际版本与哈希。

以下部署内容均使用[部署占位符约定](../reference/deployment-placeholders.md)。不提交真实节点、RDMA 初始化地址、内部工作目录或历史日志文件名。

## 2. PD 分离 GPU-only（L1）

### 2.1 已验证形态

| 项目 | 值 |
| --- | --- |
| 缓存层级 | GPU L1；不启动 Mooncake master/client |
| Prefill（P） | `<PREFILL_NODE_IP>`，`TP8 / PP1 / DP8 / EP8` |
| Decode（D） | `<DECODE_NODE_IP>`，`TP8 / PP1 / DP8 / EP8` |
| 模型 | `DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel` |
| 服务端口 | P/D `30001`；bootstrap `8998`；Router `10015` |

### 2.2 自动化配置

```ini
[common]
model_path = /ai_data/models/DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel
model_loader_config = {"enable_multithread_load":"true","num_threads":64}
sglang_port = 30001
router_port = 10015
bootstrap_port = 8998
p_node_ip = <PREFILL_NODE_IP>
d_node_ip = <DECODE_NODE_IP>
ib_devices = shca_0,shca_1,shca_2,shca_3
p_dist_init_addr = <PREFILL_DIST_INIT_ADDR>
d_dist_init_addr = <DECODE_DIST_INIT_ADDR>
topo_config = /guofy/packages/DS-V4-W4A8/config/topo.config
deepep_config = /guofy/packages/DS-V4-W4A8/config/deepep_config.json

[pd_disagg]
prefill_nodes = ${common:p_node_ip}
decode_nodes = ${common:d_node_ip}

[router]
pd-disaggregation = true
prefill_port = ${common:sglang_port}
decode_port = ${common:sglang_port}
bootstrap_port = ${common:bootstrap_port}
host = 0.0.0.0
port = ${common:router_port}
policy = round_robin
worker-startup-timeout-secs = 18000
worker-startup-check-interval = 10
health-check-endpoint = /v1/models
request-timeout-secs = 18000

[sglang_prefill]
reasoning-parser = deepseek-v4
enable-strict-thinking = true
tool-call-parser = deepseekv4
model-path = ${common:model_path}
model-loader-extra-config = ${common:model_loader_config}
quantization = slimquant_marlin
tp-size = 8
pp-size = 1
dp = 8
ep = 8
enable-dp-attention = true
enable-dp-lm-head = true
enable-dp-attention-local-control-broadcast = true
dist-init-addr = ${common:p_dist_init_addr}
disaggregation-mode = prefill
disaggregation-transfer-backend = mooncake
disaggregation-bootstrap-port = ${common:bootstrap_port}
disaggregation-ib-device = ${common:ib_devices}
chunked-prefill-size = 32768
max-prefill-tokens = 131072
mem-fraction-static = 0.85
max-running-requests = 48
enable-cache-report = true
tokenizer-backend = fastokens

[sglang_decode]
reasoning-parser = deepseek-v4
tool-call-parser = deepseekv4
model-path = ${common:model_path}
model-loader-extra-config = ${common:model_loader_config}
quantization = slimquant_marlin
tp-size = 8
pp-size = 1
dp = 8
ep = 8
dist-init-addr = ${common:d_dist_init_addr}
disaggregation-mode = decode
disaggregation-bootstrap-port = ${common:bootstrap_port}
disaggregation-ib-device = ${common:ib_devices}
chunked-prefill-size = 32768
max-total-tokens = 950000
mem-fraction-static = 0.88
max-running-requests = 160
enable-dp-attention = true
enable-dp-lm-head = true
enable-dp-attention-local-control-broadcast = true
enable-cache-report = true
tokenizer-backend = fastokens
```

### 2.3 服务脚本

启动顺序为 **Prefill → Decode → Router**。二者的公共运行环境包括 `NCCL_IB_HCA=shca_0,shca_1,shca_2,shca_3`、`NCCL_NET_PLUGIN=shca`、`NCCL_PLUGIN_P2P=ib`、`NCCL_SOCKET_IFNAME=ib0`、`GLOO_SOCKET_IFNAME=ib0` 和 `ROCSHMEM_TOPO_FILE_FORCE=/guofy/packages/DS-V4-W4A8/config/topo.config`。

```bash
# Prefill 的核心启动参数；环境变量按 2.2 的 global_prefill 等价项设置。
sglang serve \
  --reasoning-parser deepseek-v4 --enable-strict-thinking --tool-call-parser deepseekv4 \
  --model-path /ai_data/models/DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel \
  --model-loader-extra-config '{"enable_multithread_load":"true","num_threads":64}' \
  --quantization slimquant_marlin --host 0.0.0.0 --port 30001 \
  --tp-size 8 --pp-size 1 --dp 8 --ep 8 \
  --enable-dp-attention --enable-dp-lm-head --enable-dp-attention-local-control-broadcast \
  --dist-init-addr <PREFILL_DIST_INIT_ADDR> --nnodes 1 --node-rank 0 \
  --disaggregation-mode prefill --disaggregation-transfer-backend mooncake \
  --disaggregation-bootstrap-port 8998 \
  --disaggregation-ib-device shca_0,shca_1,shca_2,shca_3 \
  --max-prefill-tokens 131072 --max-running-requests 48 \
  --enable-cache-report --tokenizer-backend fastokens

# Decode 使用相同模型；DP8 的关键启动参数如下。
sglang serve \
  --reasoning-parser deepseek-v4 --tool-call-parser deepseekv4 \
  --model-path /ai_data/models/DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel \
  --tp-size 8 --pp-size 1 --dp 8 --ep 8 \
  --dist-init-addr <DECODE_DIST_INIT_ADDR> --nnodes 1 --node-rank 0 \
  --disaggregation-mode decode --skip-server-warmup \
  --disaggregation-bootstrap-port 8998 \
  --disaggregation-ib-device shca_0,shca_1,shca_2,shca_3 \
  --enable-dp-attention --enable-dp-lm-head \
  --enable-dp-attention-local-control-broadcast \
  --enable-cache-report --tokenizer-backend fastokens

python3 -m sglang_router.launch_router \
  --prefill http://<PREFILL_NODE_IP>:30001 8998 \
  --decode http://<DECODE_NODE_IP>:30001 \
  --pd-disaggregation --host 0.0.0.0 --port 10015 \
  --policy round_robin --worker-startup-timeout-secs 18000 \
  --worker-startup-check-interval 10 --health-check-endpoint /v1/models \
  --request-timeout-secs 18000 --log-level info
```

## 3. PD 分离 CPU（L1 + L3）

### 3.1 已验证形态

| 项目 | 值 |
| --- | --- |
| 缓存层级 | GPU L1 + Mooncake CPU L3；不启用 DFS / SSD Offload |
| Prefill（P） | `<PREFILL_NODE_IP>`，`TP8 / PP1 / DP8 / EP8` |
| Decode（D） | `<DECODE_NODE_IP>`，`TP8 / PP1 / DP8 / EP8` |
| Mooncake client | P 节点，`global_segment_size=240GB` |
| 模型 | `DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel` |

### 3.2 自动化配置

CPU 配置继承第 2.2 节中的 P/D、Router 和公共网络参数，并增加 Mooncake 与 external linker：

```ini
[pd_disagg]
prefill_nodes = ${common:p_node_ip}
decode_nodes = ${common:d_node_ip}
mooncake_clients = ${common:p_node_ip}

[mooncake_master]
logtostderr = true
eviction_high_watermark_ratio = 0.8
enable_http_metadata_server = true
eviction_ratio = 0.1

[mooncake_client]
host = ${common:p_node_ip}
global_segment_size = 240GB
master_server_address = ${common:p_node_ip}:50051
metadata_server = P2PHANDSHAKE
protocol = rdma
device_names = ${common:ib_devices}
port = 50052
logtostderr = true
enable_http_server = true
http_port = 9300

[mooncake_client_global]
MOONCAKE_LOCAL_HOSTNAME = ${common:p_node_ip}
MC_STORE_CLIENT_METRIC = 1
MC_STORE_ENABLE_SESSION_CACHE = 0
MC_STORE_ENABLE_DFS_PREFETCH = 0

[sglang_prefill]
# 与 GPU-only 同为 TP8 / DP8 / EP8；额外启用外部缓存 linker。
enable-unified-cache-external-linker = true
unified-cache-external-linker-backend = mooncake
```

### 3.3 服务脚本

启动顺序为 **Mooncake master → Mooncake client → Prefill → Decode → Router**。

```bash
mooncake_master \
  --logtostderr --eviction_high_watermark_ratio=0.8 \
  --enable_http_metadata_server --eviction_ratio=0.1

export MOONCAKE_LOCAL_HOSTNAME=<PREFILL_NODE_IP>
export MC_STORE_CLIENT_METRIC=1
export MC_STORE_ENABLE_SESSION_CACHE=0
export MC_STORE_ENABLE_DFS_PREFETCH=0
mooncake_client \
  --host=<PREFILL_NODE_IP> --global_segment_size=240GB \
  --master_server_address=<PREFILL_NODE_IP>:50051 \
  --metadata_server=P2PHANDSHAKE --protocol=rdma \
  --device_names=shca_0,shca_1,shca_2,shca_3 --port=50052 \
  --logtostderr --enable_http_server --http_port=9300
```

CPU Prefill 在第 2.3 节 GPU-only Prefill 的基础上增加以下环境变量和参数；Decode 与 Router 使用第 2.3 节的 DP8 脚本。

```bash
export MOONCAKE_MASTER=<PREFILL_NODE_IP>:50051
export MOONCAKE_PROTOCOL=rdma
export MOONCAKE_GLOBAL_SEGMENT_SIZE=0
export MOONCAKE_LOCAL_HOSTNAME=<PREFILL_NODE_IP>
export MC_TE_FILTERS=shca_0,shca_1,shca_2,shca_3
export SGLANG_HOST_IP=<PREFILL_NODE_IP>
export MC_MS_AUTO_DISC=1
export MC_TRANSFER_TIMEOUT=30
export SGLANG_UVICORN_WORKER_HEALTHCHECK_TIMEOUT=60
export SGLANG_MOONCAKE_READ_PLAN=1
export MC_STORE_MEMCPY=1
export MC_STORE_PINNED_RESTORE_ARENA_SIZE_BYTES=8589934592
export SGLANG_MOONCAKE_FINAL_POLL_TIMEOUT_MS=400

# 在第 2.3 节 Prefill 命令中追加：
# --enable-unified-cache-external-linker
# --unified-cache-external-linker-backend mooncake
```

### 3.4 服务验证与运行边界

`/v1/models` 只能作为基础存活检查，必须在开始压测前验证 Router 的真实 PD 请求。P/D 两端均需配置 `--enable-cache-report`，再结合请求侧 usage 与服务端日志解释命中率。

```bash
curl -sf http://<PREFILL_NODE_IP>:30001/v1/models
curl -sf http://<DECODE_NODE_IP>:30001/v1/models
curl -sf http://<PREFILL_NODE_IP>:10015/v1/models
```

## 4. PD 分离（DFS：L1 + L3 + L4）

### 2.1 已归档 Run

| 项目 | 值 |
| --- | --- |
| 证据 | 已验证 Run 快照（内部路径已脱敏） |
| 部署形态 | Prefill / Decode 分离，Mooncake RDMA + DFS + SSD Offload |
| Prefill（P） | `<PREFILL_NODE_IP>`，`TP8 / PP1 / DP1 / EP8`，开启 CP（`interleave`） |
| Decode（D） | `<DECODE_NODE_IP>`，`TP8 / PP1 / DP8 / EP8` |
| Mooncake client | P 节点；`global_segment_size=8GB` |
| DFS 根目录 | `<DFS_ROOT>` |
| Router | P 节点，监听 `10015` |
| SGLang | P/D 均监听 `30001`；bootstrap 端口 `8998` |
| 模型 | `DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel` |

### 2.2 自动化配置

```ini
# PD 分离部署配置（内部工作路径已脱敏）
# 拓扑: P=<PREFILL_NODE_IP>（CP8EP8，8 卡）；D=<DECODE_NODE_IP>（TP8DP8，8 卡）；Router 位于 P 节点的 10015 端口
# 变量引用语法: ${common:xxx} 由框架在解析时展开;值内 bash 语法($VAR/$((..)))原样透传
#脚本状态:kaiqi读聚合; 桶大小4G ;8G内存 ; OVERLAP -

[common]
# --- 模型与端口 ---
model_path = /ai_data/models/DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel
# JSON值写裸JSON(不带引号/反斜杠),生成器会自动双引号包裹+转义
model_loader_config = {"enable_multithread_load": "true","num_threads": 64}
sglang_port = 30001
router_port = 10015
bootstrap_port = 8998
# --- 网络 ---
# 各角色节点IP,换节点只改对应一行;dist_init_addr的ib地址与以太地址无固定规则,需手动对应改
p_node_ip = <PREFILL_NODE_IP>
d_node_ip = <DECODE_NODE_IP>
# mooncake client所在节点(当前与P同节点;client不必须在P上)
mc_node_ip = <PREFILL_NODE_IP>
ib_devices = shca_0,shca_1,shca_2,shca_3
p_dist_init_addr = <PREFILL_DIST_INIT_ADDR>
d_dist_init_addr = <DECODE_DIST_INIT_ADDR>
topo_config = /guofy/packages/DS-V4-W4A8/config/topo.config
deepep_config = /guofy/packages/DS-V4-W4A8/config/deepep_config.json
# --- DFS 存储（已脱敏的根目录；容量按当前配置计算）
dfs_root = <DFS_ROOT>
dfs_bucket_capacity = 4294967296
dfs_max_bucket_count = 5120
local_buffer_bytes = 21474836480

[pd_disagg]
prefill_nodes = ${common:p_node_ip}
decode_nodes = ${common:d_node_ip}
mooncake_clients = ${common:mc_node_ip}

[mooncake_master]
logtostderr = true
eviction_high_watermark_ratio = 0.8
enable_http_metadata_server = true
eviction_ratio = 0.1
enable_offload = true

[mooncake_master_global]
MOONCAKE_ENABLE_DFS = 1
MOONCAKE_DFS_FS_ADAPTER = posix
MOONCAKE_DFS_ROOT_DIR = ${common:dfs_root}
MOONCAKE_DFS_ALLOCATOR_TYPE = bucket
MOONCAKE_DFS_BUCKET_CAPACITY = ${common:dfs_bucket_capacity}
MOONCAKE_DFS_MAX_BUCKET_COUNT = ${common:dfs_max_bucket_count}
MOONCAKE_DFS_ALIGNMENT = 4096
MOONCAKE_DFS_SINGLE_TENANT = True
MOONCAKE_DFS_BUCKET_META_LOG_THRESHOLD = 4194304
MOONCAKE_DFS_EVICTION_ENABLED = True
MOONCAKE_DFS_EVICTION_HIGH_WATERMARK = 0.80
MOONCAKE_DFS_EVICTION_LOW_WATERMARK = 0.60
MOONCAKE_DFS_EVICTION_CHECK_INTERVAL = 2
MOONCAKE_DFS_DEFERRED_FREE_SECONDS = 10
MC_STORE_ENABLE_SESSION_CACHE = 1
MC_STORE_ENABLE_DFS_PREFETCH = 0

[mooncake_client]
host = ${common:mc_node_ip}
global_segment_size = 8GB
local_buffer_size = 4GB
master_server_address = ${common:p_node_ip}:50051
metadata_server = P2PHANDSHAKE
protocol = rdma
device_names = ${common:ib_devices}
port = 50052
logtostderr = true
enable_http_server = true
http_port = 9300
enable_offload = true

[mooncake_client_global]
MOONCAKE_OFFLOAD_FILE_STORAGE_PATH = ${common:dfs_root}
#MOONCAKE_OFFLOAD_LOCAL_BUFFER_SIZE_BYTES = ${common:local_buffer_bytes}
MOONCAKE_LOCAL_HOSTNAME = ${common:mc_node_ip}
MC_STORE_CLIENT_METRIC = 1
MOONCAKE_ENABLE_DFS = 1
MOONCAKE_DFS_FS_ADAPTER = posix
MOONCAKE_DFS_ROOT_DIR = ${common:dfs_root}
MOONCAKE_DFS_ALLOCATOR_TYPE = bucket
MOONCAKE_DFS_BUCKET_CAPACITY = ${common:dfs_bucket_capacity}
MOONCAKE_DFS_MAX_BUCKET_COUNT = ${common:dfs_max_bucket_count}
MOONCAKE_DFS_ALIGNMENT = 4096
MOONCAKE_DFS_SINGLE_TENANT = True
MOONCAKE_OFFLOAD_ENABLED = True
MOONCAKE_OFFLOAD_STORAGE_BACKEND_DESCRIPTOR = distributed_storage_backend
MC_STORE_ENABLE_SESSION_CACHE = 1
MC_STORE_ENABLE_DFS_PREFETCH = 0
MC_STORE_MEMCPY=1
MC_STORE_PINNED_RESTORE_ARENA_SIZE_BYTES=8589934592

[router]
pd-disaggregation = true
# prefill/decode 的URL由框架按 pd_disagg 节点拼接,这里只给端口
prefill_port = ${common:sglang_port}
decode_port = ${common:sglang_port}
bootstrap_port = ${common:bootstrap_port}
host = 0.0.0.0
port = ${common:router_port}
policy = round_robin
worker-startup-timeout-secs = 18000
worker-startup-check-interval = 10
health-check-endpoint = /v1/models
request-timeout-secs = 18000
log-level = info

[sglang_prefill]
reasoning-parser = deepseek-v4
enable-strict-thinking = true
tool-call-parser = deepseekv4
model-path = ${common:model_path}
trust-remote-code = True
model-loader-extra-config = ${common:model_loader_config}
quantization = slimquant_marlin
host = 0.0.0.0
port = ${common:sglang_port}
tp-size = 8
pp-size = 1
dp = 8
ep = 8
enable-dp-attention = true
enable-dp-lm-head = true
enable-dp-attention-local-control-broadcast= true
moe-dense-tp-size = 1
moe-a2a-backend = megamoe
moe-runner-backend = auto
init-expert-location =/guofy/packages/DS-V4-W4A8/need/placement_candidate.json
deepep-mode = normal
deepep-config = ${common:deepep_config}
dist-init-addr = ${common:p_dist_init_addr}
nnodes = 1
node-rank = 0
dist-timeout = 10000
watchdog-timeout = 3600
#max-total-tokens = 1788928
disaggregation-mode = prefill
disaggregation-transfer-backend = mooncake
disaggregation-bootstrap-port = ${common:bootstrap_port}
disaggregation-ib-device = ${common:ib_devices}
disable-cuda-graph = true
speculative-algorithm = DSPARK
speculative-num-steps = 1
speculative-eagle-topk = 1
speculative-moe-a2a-backend = deepep
speculative-moe-runner-backend = deep_gemm
chunked-prefill-size = 32768
max-prefill-tokens = 131072
mem-fraction-static = 0.85
swa-full-tokens-ratio = 0.15
max-running-requests = 48

kv-cache-dtype = auto
disable-flashinfer-autotune = true
tokenizer-worker-num = 8
enable-metrics = true
enable-request-time-stats-logging = true
enable-unified-cache-external-linker = true
unified-cache-external-linker-backend = mooncake

mooncake-enable-page-wise-load = true
disable-overlap-schedule = true

# 恒远新增优化0921_1520
#mooncake-enable-waiting-queue-dfs-prefetch = true
#mooncake-waiting-queue-dfs-prefetch-workers = 4
#mooncake-waiting-queue-dfs-prefetch-max-requests = 48
#mooncake-waiting-queue-dfs-prefetch-max-bytes= 4294967296

[sglang_decode]
# --- Decode: TP8DP8 EP8 + DSPARK + LL deepep (04_d_10.sh) ---
reasoning-parser = deepseek-v4
tool-call-parser = deepseekv4
tp-size = 8
pp-size = 1
dp = 8
ep = 8
dist-init-addr = ${common:d_dist_init_addr}
nnodes = 1
node-rank = 0
dist-timeout = 10000
watchdog-timeout = 3600
host = 0.0.0.0
port = ${common:sglang_port}
model-path = ${common:model_path}
model-loader-extra-config = ${common:model_loader_config}
trust-remote-code = true
chunked-prefill-size = 32768
max-total-tokens = 950000
disable-flashinfer-autotune = true
cuda-graph-max-bs = 8
mem-fraction-static = 0.88
speculative-algorithm = DSPARK
speculative-num-steps = 1
speculative-eagle-topk = 1
speculative-moe-a2a-backend = deepep
speculative-moe-runner-backend = deep_gemm
max-running-requests = 160
enable-metrics = true
swa-full-tokens-ratio = 0.15
quantization = slimquant_marlin
enable-dp-attention = true
enable-dp-lm-head = true
load-balance-method = auto
moe-a2a-backend = deepep
moe-runner-backend = deep_gemm
deepep-mode = low_latency
disaggregation-mode = decode
tokenizer-worker-num = 8
skip-server-warmup = true
disaggregation-bootstrap-port = ${common:bootstrap_port}
disaggregation-ib-device = ${common:ib_devices}
enable-dp-attention-local-control-broadcast = true

[global_prefill]
# 不继承的脏环境(原脚本的 unset)
unset_envs = SGLANG_PD_HIDDEN_POOL_TOKENS,SGLANG_PD_HIDDEN_RECV_POOL_TOKENS,PYTHONPYCACHEPREFIX,SGLANG_DEEPEP_BF16_DISPATCH

# --- pytorch / allocator ---
PYTORCH_ALLOC_CONF = expandable_segments:True

SGLANG_HCU_MEGA_MOE_RUNTIME=megamoe
SGLANG_OPT_DEEPGEMM_MEGA_MOE_NUM_MAX_TOKENS_PER_RANK=4096
SGLANG_MOE_COPY_WEIGHT_VIEWS_BEFORE_H2D=1
PYTHONDONTWRITEBYTECODE=1
# --- sglang runtime ---
SGLANG_SET_CPU_AFFINITY = 1
SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT = 1200
SGLANG_USE_LIGHTOP = 1
SGLANG_ROCM_USE_AITER_MOE = 1
SGLANG_ROCM_USE_AITER_TILELANG_MHC = 1
SGLANG_GROUPGEMM = True
SGLANG_USE_FP8_W8A8_MOE = 0
SGLANG_USE_LIGHTOP_EP_MOE_ALIGN = 1
SGLANG_USE_LIGHTOP_EP_SCATTER = 1
SGLANG_USE_LIGHTOP_EP_GATHER = 1
SGLANG_USE_LIGHTOP_TOPK_IDS_POSTPROCESS = 1
SGLANG_USE_LIGHTOP_GROUP_FP8_QUANT = 0
SGLANG_OPT_USE_FUSED_HASH_TOPK = True
SGLANG_OPT_USE_JIT_KERNEL_FUSED_TOPK = True
SGLANG_TOPK_TRANSFORM_512_TORCH = false
SGLANG_OPT_SWIGLU_CLAMP_FUSION = false
SGLANG_JIT_DEEPGEMM_PRECOMPILE = 0
USE_DCU_CUSTOM_ALLREDUCE = 0
SGLANG_OPT_FP8_WO_A_GEMM = 0
SGLANG_USE_LINEAR_BF16_FP32_USE_BLASLT = 1
SGLANG_USE_OPT_CAT = 1
SGLANG_USE_FUSED_MLA_CAT = 1
SGLANG_USE_DPSKV4_LIGHTOP_QUANT_K_CACHE = 1
SGLANG_USE_DPSKV4_LIGHTOP_RMSNORM = 1
SGLANG_USE_FUSED_DPSKV4_QNORM_ROPE_KV_ROPE_QUANT = 1
SGLANG_USE_FUSED_DPSKV4_SILU_MUL_FP8_QUANT = 0
SGLANG_APPLY_CONFIG_BACKUP = none
SGLANG_USE_AITER_AG = 0
SGLANG_W4A8_EP_USE_GROUPGEMM = True
SGLANG_DSV4_HCU_INT8_INDEX_K_CACHE = 1
SGLANG_OPT_FLASHMLA_SPARSE_PREFILL = 1
SGLANG_DSV4_SPLIT_PREFILL_DECODE_MLA = 1
SGLANG_DSV4_HCU_USE_BF16_FLASH_MLA = 0
SGLANG_DSV4_HCU_USE_LIGHTOP_BF16_GATHER = 0
SGLANG_USE_W4A8_CONTIGUOUS_HIPC = 1
SGLANG_USE_LIGHTOP_W4A8_MARLIN_MOE = false
#SGLANG_DISAGGREGATION_ALL_CP_RANKS_TRANSFER = 1

SGLANG_LIGHTOP_TOPK = 1
SGL_USE_LIGHTOP_TOPK_BACKAND = 2
SGLANG_LIGHTOP_KVALLOC_KERNEL = 1
W8A8_SUPPORT_METHODS = 3

# --- nccl / rocmshmem ---
NCCL_IB_HCA = ${common:ib_devices}
NCCL_NET_PLUGIN = shca
NCCL_PLUGIN_P2P = ib
NCCL_SOCKET_IFNAME = ib0
GLOO_SOCKET_IFNAME = ib0
ROCSHMEM_MAX_NUM_CONTEXTS = 48
ROCSHMEM_ALLOWED_IBV_DEVICES = ${common:ib_devices}
ROCSHMEM_TOPO_FILE_FORCE = ${common:topo_config}
MC_ENABLE_DEST_DEVICE_AFFINITY = 1
LD_LIBRARY_PATH = /usr/lib64:/usr/local/lib/python3.10/dist-packages/mooncake:/usr/local/lib/python3.10/dist-packages/mooncake_transfer_engine_shca.libs:$LD_LIBRARY_PATH

# --- mooncake(挂在P上,master/client同节点) ---
MOONCAKE_MASTER = ${common:p_node_ip}:50051
MOONCAKE_PROTOCOL = rdma

MOONCAKE_GLOBAL_SEGMENT_SIZE = 0
MOONCAKE_LOCAL_HOSTNAME = ${common:p_node_ip}
MC_TE_FILTERS = ${common:ib_devices}
SGLANG_HOST_IP = ${common:p_node_ip}
SGLANG_KV_LAYOUT_HCU_FA = 0
MC_MS_AUTO_DISC = 1
MC_IB_PCI_RELAXED_ORDERING = 0
MC_TRANSFER_TIMEOUT = 30
MC_SLICE_SIZE = 1048576
#MOONCAKE_OFFLOAD_LOCAL_BUFFER_SIZE_BYTES = ${common:local_buffer_bytes}
MOONCAKE_ENABLE_DFS = 1
MOONCAKE_DFS_FS_ADAPTER = posix
MOONCAKE_DFS_ROOT_DIR = ${common:dfs_root}
MOONCAKE_DFS_ALLOCATOR_TYPE = bucket
MOONCAKE_DFS_BUCKET_CAPACITY = ${common:dfs_bucket_capacity}
MOONCAKE_DFS_MAX_BUCKET_COUNT = ${common:dfs_max_bucket_count}
MOONCAKE_DFS_ALIGNMENT = 4096
MOONCAKE_DFS_SINGLE_TENANT = True
MOONCAKE_DFS_BUCKET_META_LOG_THRESHOLD = 4194304
MOONCAKE_DFS_EVICTION_ENABLED = True
MOONCAKE_ENABLE_SSD_OFFLOAD = 1
MOONCAKE_OFFLOAD_STORAGE_BACKEND_DESCRIPTOR = distributed_storage_backend
MOONCAKE_OFFLOAD_FILE_STORAGE_PATH = ${common:dfs_root}
MC_STORE_ENABLE_SESSION_CACHE = 1
MC_STORE_DFS_READ_TRACE = 1
MOONCAKE_DFS_BATCH_READ_THREADS = 32
MC_STORE_ENABLE_DFS_PREFETCH = 0
SGLANG_UVICORN_WORKER_HEALTHCHECK_TIMEOUT = 60
MOONCAKE_DFS_FORCE_ONE_REPLICA = True

MC_STORE_DFS_H2D_KERNEL=1
MC_STORE_DFS_PINNED_POOL_BYTES=8589934592
MOONCAKE_DFS_BATCH_READ_MERGE_ENABLED=1

SGLANG_MOONCAKE_READ_PLAN=1
SGLANG_LIGHTOP_DEQUANTIZE_K_CACHE_PAGED=1

MC_STORE_CLIENT_METRIC=0

MC_STORE_MEMCPY=1
MC_STORE_PINNED_RESTORE_ARENA_SIZE_BYTES=8589934592
MC_STORE_DFS_PREFETCH_ARENA_SIZE_BYTES=6442450944

SGLANG_MOONCAKE_FINAL_POLL_TIMEOUT_MS = 400

[global_decode]
# 不继承的脏环境
unset_envs = SGLANG_PD_HIDDEN_POOL_TOKENS,SGLANG_PD_HIDDEN_RECV_POOL_TOKENS

SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT = 1200
SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION = 0
SGLANG_UVICORN_WORKER_HEALTHCHECK_TIMEOUT = 120
SGLANG_SET_CPU_AFFINITY = 1
SGLANG_DISAGGREGATION_ALL_CP_RANKS_TRANSFER = 1
GLIBC_TUNABLES = glibc.rtld.optional_static_tls=0x40000
SGLANG_LIGHTOP_TOPK = 1
SGLANG_OPT_USE_FUSED_STORE_CACHE = false
SGLANG_OPT_USE_FUSED_HASH_TOPK = True
SGLANG_OPT_SWIGLU_CLAMP_FUSION = false
SGLANG_TOPK_TRANSFORM_512_TORCH = false
SGLANG_OPT_USE_JIT_KERNEL_FUSED_TOPK = True
SGLANG_JIT_DEEPGEMM_PRECOMPILE = 0
SGLANG_USE_AITER_AG = 0
ROCSHMEM_DISABLE_HDP_FLUSH = 1
ROCSHMEM_GDA_NUM_QPS_DEFAULT_CTX = 288
ROCSHMEM_HEAP_SIZE = 1610612736
SGLANG_DEEPEP_NUM_MAX_DISPATCH_TOKENS_PER_RANK = 64
SGLANG_ROCM_USE_AITER_MOE = 1
SGLANG_USE_OPT_CAT = 1
SGLANG_USE_FUSED_MLA_CAT = 1
SGLANG_USE_LIGHTOP_GROUP_FP8_QUANT = 0
SGLANG_USE_FUSED_DPSKV4_SILU_MUL_FP8_QUANT = 0
SGLANG_USE_LINEAR_BF16_FP32_USE_BLASLT = 1
SGLANG_APPLY_CONFIG_BACKUP = none
SGLANG_ROCM_USE_AITER_TILELANG_MHC = 1
SGLANG_DSV4_SPLIT_PREFILL_DECODE_MLA = 0
SGLANG_OPT_FLASHMLA_SPARSE_PREFILL = 0
SGLANG_USE_LIGHTOP = 1
SGLANG_USE_DPSKV4_LIGHTOP_QUANT_K_CACHE = 1
SGLANG_USE_DPSKV4_LIGHTOP_RMSNORM = 1
SGLANG_USE_FUSED_DPSKV4_QNORM_ROPE_KV_ROPE_QUANT = 1
SGLANG_USE_LIGHTOP_EP_MOE_ALIGN = 1
SGLANG_USE_LIGHTOP_EP_SCATTER = 1
SGLANG_USE_LIGHTOP_EP_GATHER = 1
SGLANG_USE_LIGHTOP_TOPK_IDS_POSTPROCESS = 1
SGLANG_OPT_FP8_WO_A_GEMM = 0
SGLANG_RAGGED_VERIFY_MODE = static
SGLANG_DSPARK_CONFIDENCE_RELAY_LAG_STEPS = 2
SGLANG_DSPARK_OPT_MARKOV_W2_TP_SHARD = 1
SGLANG_DSPARK_ENABLE_MULTI_STREAM = 1
SGLANG_DSPARK_FAST_KERNEL = 1
SGLANG_DSPARK_FAST_SAMPLING = 1
DEEPEP_ENABLE_LL_LAYERED_OPT = 1
SGLANG_DSV4_HCU_INT8_INDEX_K_CACHE = 1
SGLANG_USE_W4A8_MASKED_HIPC = 1
SGLANG_USE_LIGHTOP_W4A8_MARLIN_MOE = false

NCCL_IB_HCA = ${common:ib_devices}
NCCL_NET_PLUGIN = shca
NCCL_PLUGIN_P2P = ib
NCCL_SOCKET_IFNAME = ib0
GLOO_SOCKET_IFNAME = ib0
MC_ENABLE_DEST_DEVICE_AFFINITY = 1
HSA_USE_SVM = 0
SGLANG_GROUPGEMM = True
ROCSHMEM_TOPO_FILE_FORCE = ${common:topo_config}
LD_LIBRARY_PATH = /usr/lib64:/usr/local/lib/python3.10/dist-packages/mooncake:/usr/local/lib/python3.10/dist-packages/mooncake_transfer_engine_shca.libs:$LD_LIBRARY_PATH

SGLANG_ENABLE_UNIFIED_RADIX_TREE = 1
SGLANG_EXPERIMENTAL_DSV4_DECODE_RADIX_CACHE = 1

```

**启动顺序：** Mooncake master → Mooncake client → Prefill SGLang → Decode SGLang → PD Router。以下为经过脱敏后的部署示例；请在目标环境按占位符替换网络和存储变量。

### 2.3 Mooncake master（P）

```bash
export MOONCAKE_ENABLE_DFS=1
export MOONCAKE_DFS_FS_ADAPTER=posix
export MOONCAKE_DFS_ROOT_DIR=<DFS_ROOT>
export MOONCAKE_DFS_ALLOCATOR_TYPE=bucket
export MOONCAKE_DFS_BUCKET_CAPACITY=4294967296
export MOONCAKE_DFS_MAX_BUCKET_COUNT=5120
export MOONCAKE_DFS_ALIGNMENT=4096
export MOONCAKE_DFS_SINGLE_TENANT=True
export MOONCAKE_DFS_BUCKET_META_LOG_THRESHOLD=4194304
export MOONCAKE_DFS_EVICTION_ENABLED=True
export MOONCAKE_DFS_EVICTION_HIGH_WATERMARK=0.80
export MOONCAKE_DFS_EVICTION_LOW_WATERMARK=0.60
export MOONCAKE_DFS_EVICTION_CHECK_INTERVAL=2
export MOONCAKE_DFS_DEFERRED_FREE_SECONDS=10
export MC_STORE_ENABLE_SESSION_CACHE=1
export MC_STORE_ENABLE_DFS_PREFETCH=0
nohup mooncake_master \
    --logtostderr \
    --eviction_high_watermark_ratio=0.8 \
    --enable_http_metadata_server \
    --eviction_ratio=0.1 \
    --enable_offload=true \
> 20260929_192911_mooncake_master.log 2>&1 &

```

### 2.4 Mooncake client（P）

```bash
export MOONCAKE_OFFLOAD_FILE_STORAGE_PATH=<DFS_ROOT>
export MOONCAKE_LOCAL_HOSTNAME=<PREFILL_NODE_IP>
export MC_STORE_CLIENT_METRIC=1
export MOONCAKE_ENABLE_DFS=1
export MOONCAKE_DFS_FS_ADAPTER=posix
export MOONCAKE_DFS_ROOT_DIR=<DFS_ROOT>
export MOONCAKE_DFS_ALLOCATOR_TYPE=bucket
export MOONCAKE_DFS_BUCKET_CAPACITY=4294967296
export MOONCAKE_DFS_MAX_BUCKET_COUNT=5120
export MOONCAKE_DFS_ALIGNMENT=4096
export MOONCAKE_DFS_SINGLE_TENANT=True
export MOONCAKE_OFFLOAD_ENABLED=True
export MOONCAKE_OFFLOAD_STORAGE_BACKEND_DESCRIPTOR=distributed_storage_backend
export MC_STORE_ENABLE_SESSION_CACHE=1
export MC_STORE_ENABLE_DFS_PREFETCH=0
export MC_STORE_MEMCPY=1
export MC_STORE_PINNED_RESTORE_ARENA_SIZE_BYTES=8589934592
nohup mooncake_client \
    --host=<PREFILL_NODE_IP> \
    --global_segment_size=8GB \
    --local_buffer_size=4GB \
    --master_server_address=<PREFILL_NODE_IP>:50051 \
    --metadata_server=P2PHANDSHAKE \
    --protocol=rdma \
    --device_names=shca_0,shca_1,shca_2,shca_3 \
    --port=50052 \
    --logtostderr \
    --enable_http_server \
    --http_port=9300 \
    --enable_offload=true \
> 20260929_192911_mooncake_client_<PREFILL_NODE_IP>.log 2>&1 &

```

### 2.5 Prefill SGLang（P）

```bash
unset SGLANG_PD_HIDDEN_POOL_TOKENS
unset SGLANG_PD_HIDDEN_RECV_POOL_TOKENS
unset PYTHONPYCACHEPREFIX
unset SGLANG_DEEPEP_BF16_DISPATCH
export PYTORCH_ALLOC_CONF=expandable_segments:True
export SGLANG_HCU_MEGA_MOE_RUNTIME=megamoe
export SGLANG_OPT_DEEPGEMM_MEGA_MOE_NUM_MAX_TOKENS_PER_RANK=4096
export SGLANG_MOE_COPY_WEIGHT_VIEWS_BEFORE_H2D=1
export PYTHONDONTWRITEBYTECODE=1
export SGLANG_SET_CPU_AFFINITY=1
export SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT=1200
export SGLANG_USE_LIGHTOP=1
export SGLANG_ROCM_USE_AITER_MOE=1
export SGLANG_ROCM_USE_AITER_TILELANG_MHC=1
export SGLANG_GROUPGEMM=True
export SGLANG_USE_FP8_W8A8_MOE=0
export SGLANG_USE_LIGHTOP_EP_MOE_ALIGN=1
export SGLANG_USE_LIGHTOP_EP_SCATTER=1
export SGLANG_USE_LIGHTOP_EP_GATHER=1
export SGLANG_USE_LIGHTOP_TOPK_IDS_POSTPROCESS=1
export SGLANG_USE_LIGHTOP_GROUP_FP8_QUANT=0
export SGLANG_OPT_USE_FUSED_HASH_TOPK=True
export SGLANG_OPT_USE_JIT_KERNEL_FUSED_TOPK=True
export SGLANG_TOPK_TRANSFORM_512_TORCH=False
export SGLANG_OPT_SWIGLU_CLAMP_FUSION=False
export SGLANG_JIT_DEEPGEMM_PRECOMPILE=0
export USE_DCU_CUSTOM_ALLREDUCE=0
export SGLANG_OPT_FP8_WO_A_GEMM=0
export SGLANG_USE_LINEAR_BF16_FP32_USE_BLASLT=1
export SGLANG_USE_OPT_CAT=1
export SGLANG_USE_FUSED_MLA_CAT=1
export SGLANG_USE_DPSKV4_LIGHTOP_QUANT_K_CACHE=1
export SGLANG_USE_DPSKV4_LIGHTOP_RMSNORM=1
export SGLANG_USE_FUSED_DPSKV4_QNORM_ROPE_KV_ROPE_QUANT=1
export SGLANG_USE_FUSED_DPSKV4_SILU_MUL_FP8_QUANT=0
export SGLANG_APPLY_CONFIG_BACKUP=none
export SGLANG_USE_AITER_AG=0
export SGLANG_W4A8_EP_USE_GROUPGEMM=True
export SGLANG_DSV4_HCU_INT8_INDEX_K_CACHE=1
export SGLANG_OPT_FLASHMLA_SPARSE_PREFILL=1
export SGLANG_DSV4_SPLIT_PREFILL_DECODE_MLA=1
export SGLANG_DSV4_HCU_USE_BF16_FLASH_MLA=0
export SGLANG_DSV4_HCU_USE_LIGHTOP_BF16_GATHER=0
export SGLANG_USE_W4A8_CONTIGUOUS_HIPC=1
export SGLANG_USE_LIGHTOP_W4A8_MARLIN_MOE=False
export SGLANG_LIGHTOP_TOPK=1
export SGL_USE_LIGHTOP_TOPK_BACKAND=2
export SGLANG_LIGHTOP_KVALLOC_KERNEL=1
export W8A8_SUPPORT_METHODS=3
export NCCL_IB_HCA=shca_0,shca_1,shca_2,shca_3
export NCCL_NET_PLUGIN=shca
export NCCL_PLUGIN_P2P=ib
export NCCL_SOCKET_IFNAME=ib0
export GLOO_SOCKET_IFNAME=ib0
export ROCSHMEM_MAX_NUM_CONTEXTS=48
export ROCSHMEM_ALLOWED_IBV_DEVICES=shca_0,shca_1,shca_2,shca_3
export ROCSHMEM_TOPO_FILE_FORCE=/guofy/packages/DS-V4-W4A8/config/topo.config
export MC_ENABLE_DEST_DEVICE_AFFINITY=1
export LD_LIBRARY_PATH=/usr/lib64:/usr/local/lib/python3.10/dist-packages/mooncake:/usr/local/lib/python3.10/dist-packages/mooncake_transfer_engine_shca.libs:$LD_LIBRARY_PATH
export MOONCAKE_MASTER=<PREFILL_NODE_IP>:50051
export MOONCAKE_PROTOCOL=rdma
export MOONCAKE_GLOBAL_SEGMENT_SIZE=0
export MOONCAKE_LOCAL_HOSTNAME=<PREFILL_NODE_IP>
export MC_TE_FILTERS=shca_0,shca_1,shca_2,shca_3
export SGLANG_HOST_IP=<PREFILL_NODE_IP>
export SGLANG_KV_LAYOUT_HCU_FA=0
export MC_MS_AUTO_DISC=1
export MC_IB_PCI_RELAXED_ORDERING=0
export MC_TRANSFER_TIMEOUT=30
export MC_SLICE_SIZE=1048576
export MOONCAKE_ENABLE_DFS=1
export MOONCAKE_DFS_FS_ADAPTER=posix
export MOONCAKE_DFS_ROOT_DIR=<DFS_ROOT>
export MOONCAKE_DFS_ALLOCATOR_TYPE=bucket
export MOONCAKE_DFS_BUCKET_CAPACITY=4294967296
export MOONCAKE_DFS_MAX_BUCKET_COUNT=5120
export MOONCAKE_DFS_ALIGNMENT=4096
export MOONCAKE_DFS_SINGLE_TENANT=True
export MOONCAKE_DFS_BUCKET_META_LOG_THRESHOLD=4194304
export MOONCAKE_DFS_EVICTION_ENABLED=True
export MOONCAKE_ENABLE_SSD_OFFLOAD=1
export MOONCAKE_OFFLOAD_STORAGE_BACKEND_DESCRIPTOR=distributed_storage_backend
export MOONCAKE_OFFLOAD_FILE_STORAGE_PATH=<DFS_ROOT>
export MC_STORE_ENABLE_SESSION_CACHE=1
export MC_STORE_DFS_READ_TRACE=1
export MOONCAKE_DFS_BATCH_READ_THREADS=32
export MC_STORE_ENABLE_DFS_PREFETCH=0
export SGLANG_UVICORN_WORKER_HEALTHCHECK_TIMEOUT=60
export MOONCAKE_DFS_FORCE_ONE_REPLICA=True
export MC_STORE_DFS_H2D_KERNEL=1
export MC_STORE_DFS_PINNED_POOL_BYTES=8589934592
export MOONCAKE_DFS_BATCH_READ_MERGE_ENABLED=1
export SGLANG_MOONCAKE_READ_PLAN=1
export SGLANG_LIGHTOP_DEQUANTIZE_K_CACHE_PAGED=1
export MC_STORE_CLIENT_METRIC=0
export MC_STORE_MEMCPY=1
export MC_STORE_PINNED_RESTORE_ARENA_SIZE_BYTES=8589934592
export MC_STORE_DFS_PREFETCH_ARENA_SIZE_BYTES=6442450944
export SGLANG_MOONCAKE_FINAL_POLL_TIMEOUT_MS=400
nohup sglang serve \
    --reasoning-parser deepseek-v4 \
    --enable-strict-thinking \
    --tool-call-parser deepseekv4 \
    --model-path /ai_data/models/DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel \
    --trust-remote-code \
    --model-loader-extra-config "{\"enable_multithread_load\": \"true\",\"num_threads\": 64}" \
    --quantization slimquant_marlin \
    --host 0.0.0.0 \
    --port 30001 \
    --tp-size 8 \
    --pp-size 1 \
    --dp 8 \
    --ep 8 \
    --enable-dp-attention \
    --enable-dp-lm-head \
    --enable-dp-attention-local-control-broadcast \
    --moe-dense-tp-size 1 \
    --moe-a2a-backend megamoe \
    --moe-runner-backend auto \
    --init-expert-location /guofy/packages/DS-V4-W4A8/need/placement_candidate.json \
    --deepep-mode normal \
    --deepep-config /guofy/packages/DS-V4-W4A8/config/deepep_config.json \
    --dist-init-addr <PREFILL_DIST_INIT_ADDR> \
    --nnodes 1 \
    --node-rank 0 \
    --dist-timeout 10000 \
    --watchdog-timeout 3600 \
    --disaggregation-mode prefill \
    --disaggregation-transfer-backend mooncake \
    --disaggregation-bootstrap-port 8998 \
    --disaggregation-ib-device shca_0,shca_1,shca_2,shca_3 \
    --disable-cuda-graph \
    --speculative-algorithm DSPARK \
    --speculative-num-steps 1 \
    --speculative-eagle-topk 1 \
    --speculative-moe-a2a-backend deepep \
    --speculative-moe-runner-backend deep_gemm \
    --chunked-prefill-size 32768 \
    --max-prefill-tokens 131072 \
    --mem-fraction-static 0.85 \
    --swa-full-tokens-ratio 0.15 \
    --max-running-requests 48 \
    --kv-cache-dtype auto \
    --disable-flashinfer-autotune \
    --tokenizer-worker-num 8 \
    --enable-metrics \
    --enable-request-time-stats-logging \
    --enable-unified-cache-external-linker \
    --unified-cache-external-linker-backend mooncake \
    --mooncake-enable-page-wise-load \
    --disable-overlap-schedule \
> 20260929_192911_sglang_running_prefill_<PREFILL_NODE_IP>.log 2>&1 &

```

### 2.6 Decode SGLang（D）

```bash
unset SGLANG_PD_HIDDEN_POOL_TOKENS
unset SGLANG_PD_HIDDEN_RECV_POOL_TOKENS
export SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT=1200
export SGLANG_ENABLE_HEALTH_ENDPOINT_GENERATION=0
export SGLANG_UVICORN_WORKER_HEALTHCHECK_TIMEOUT=120
export SGLANG_SET_CPU_AFFINITY=1
export SGLANG_DISAGGREGATION_ALL_CP_RANKS_TRANSFER=1
export GLIBC_TUNABLES=glibc.rtld.optional_static_tls=0x40000
export SGLANG_LIGHTOP_TOPK=1
export SGLANG_OPT_USE_FUSED_STORE_CACHE=False
export SGLANG_OPT_USE_FUSED_HASH_TOPK=True
export SGLANG_OPT_SWIGLU_CLAMP_FUSION=False
export SGLANG_TOPK_TRANSFORM_512_TORCH=False
export SGLANG_OPT_USE_JIT_KERNEL_FUSED_TOPK=True
export SGLANG_JIT_DEEPGEMM_PRECOMPILE=0
export SGLANG_USE_AITER_AG=0
export ROCSHMEM_DISABLE_HDP_FLUSH=1
export ROCSHMEM_GDA_NUM_QPS_DEFAULT_CTX=288
export ROCSHMEM_HEAP_SIZE=1610612736
export SGLANG_DEEPEP_NUM_MAX_DISPATCH_TOKENS_PER_RANK=64
export SGLANG_ROCM_USE_AITER_MOE=1
export SGLANG_USE_OPT_CAT=1
export SGLANG_USE_FUSED_MLA_CAT=1
export SGLANG_USE_LIGHTOP_GROUP_FP8_QUANT=0
export SGLANG_USE_FUSED_DPSKV4_SILU_MUL_FP8_QUANT=0
export SGLANG_USE_LINEAR_BF16_FP32_USE_BLASLT=1
export SGLANG_APPLY_CONFIG_BACKUP=none
export SGLANG_ROCM_USE_AITER_TILELANG_MHC=1
export SGLANG_DSV4_SPLIT_PREFILL_DECODE_MLA=0
export SGLANG_OPT_FLASHMLA_SPARSE_PREFILL=0
export SGLANG_USE_LIGHTOP=1
export SGLANG_USE_DPSKV4_LIGHTOP_QUANT_K_CACHE=1
export SGLANG_USE_DPSKV4_LIGHTOP_RMSNORM=1
export SGLANG_USE_FUSED_DPSKV4_QNORM_ROPE_KV_ROPE_QUANT=1
export SGLANG_USE_LIGHTOP_EP_MOE_ALIGN=1
export SGLANG_USE_LIGHTOP_EP_SCATTER=1
export SGLANG_USE_LIGHTOP_EP_GATHER=1
export SGLANG_USE_LIGHTOP_TOPK_IDS_POSTPROCESS=1
export SGLANG_OPT_FP8_WO_A_GEMM=0
export SGLANG_RAGGED_VERIFY_MODE=static
export SGLANG_DSPARK_CONFIDENCE_RELAY_LAG_STEPS=2
export SGLANG_DSPARK_OPT_MARKOV_W2_TP_SHARD=1
export SGLANG_DSPARK_ENABLE_MULTI_STREAM=1
export SGLANG_DSPARK_FAST_KERNEL=1
export SGLANG_DSPARK_FAST_SAMPLING=1
export DEEPEP_ENABLE_LL_LAYERED_OPT=1
export SGLANG_DSV4_HCU_INT8_INDEX_K_CACHE=1
export SGLANG_USE_W4A8_MASKED_HIPC=1
export SGLANG_USE_LIGHTOP_W4A8_MARLIN_MOE=False
export NCCL_IB_HCA=shca_0,shca_1,shca_2,shca_3
export NCCL_NET_PLUGIN=shca
export NCCL_PLUGIN_P2P=ib
export NCCL_SOCKET_IFNAME=ib0
export GLOO_SOCKET_IFNAME=ib0
export MC_ENABLE_DEST_DEVICE_AFFINITY=1
export HSA_USE_SVM=0
export SGLANG_GROUPGEMM=True
export ROCSHMEM_TOPO_FILE_FORCE=/guofy/packages/DS-V4-W4A8/config/topo.config
export LD_LIBRARY_PATH=/usr/lib64:/usr/local/lib/python3.10/dist-packages/mooncake:/usr/local/lib/python3.10/dist-packages/mooncake_transfer_engine_shca.libs:$LD_LIBRARY_PATH
export SGLANG_ENABLE_UNIFIED_RADIX_TREE=1
export SGLANG_EXPERIMENTAL_DSV4_DECODE_RADIX_CACHE=1
nohup sglang serve \
    --reasoning-parser deepseek-v4 \
    --tool-call-parser deepseekv4 \
    --tp-size 8 \
    --pp-size 1 \
    --dp 8 \
    --ep 8 \
    --dist-init-addr <DECODE_DIST_INIT_ADDR> \
    --nnodes 1 \
    --node-rank 0 \
    --dist-timeout 10000 \
    --watchdog-timeout 3600 \
    --host 0.0.0.0 \
    --port 30001 \
    --model-path /ai_data/models/DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel \
    --model-loader-extra-config "{\"enable_multithread_load\": \"true\",\"num_threads\": 64}" \
    --trust-remote-code \
    --chunked-prefill-size 32768 \
    --max-total-tokens 950000 \
    --disable-flashinfer-autotune \
    --cuda-graph-max-bs 8 \
    --mem-fraction-static 0.88 \
    --speculative-algorithm DSPARK \
    --speculative-num-steps 1 \
    --speculative-eagle-topk 1 \
    --speculative-moe-a2a-backend deepep \
    --speculative-moe-runner-backend deep_gemm \
    --max-running-requests 160 \
    --enable-metrics \
    --swa-full-tokens-ratio 0.15 \
    --quantization slimquant_marlin \
    --enable-dp-attention \
    --enable-dp-lm-head \
    --load-balance-method auto \
    --moe-a2a-backend deepep \
    --moe-runner-backend deep_gemm \
    --deepep-mode low_latency \
    --disaggregation-mode decode \
    --tokenizer-worker-num 8 \
    --skip-server-warmup \
    --disaggregation-bootstrap-port 8998 \
    --disaggregation-ib-device shca_0,shca_1,shca_2,shca_3 \
    --enable-dp-attention-local-control-broadcast \
> 20260929_192911_sglang_running_decode_<DECODE_NODE_IP>.log 2>&1 &

```

### 2.7 PD Router（P）

```bash
nohup python3 -m sglang_router.launch_router \
--prefill http://<PREFILL_NODE_IP>:30001 8998 \
--decode http://<DECODE_NODE_IP>:30001 \
--pd-disaggregation \
--host=0.0.0.0 \
--port=10015 \
--policy=round_robin \
--worker-startup-timeout-secs=18000 \
--worker-startup-check-interval=10 \
--health-check-endpoint=/v1/models \
--request-timeout-secs=18000 \
--log-level=info \
> 20260929_192911_router.log 2>&1 &

```

### 2.8 运行边界与已知问题

- 本页展示的是脱敏后的配置与脚本示例，不保证其可以在当前环境直接复现。
- DFS 根目录、NIC 名、容器动态库、模型路径与 IP 均为 目标 P / D 节点 环境专用配置，迁移前必须逐项替换并验证。
- Router 的 `/v1/models` 就绪检查可能先于真正可用状态返回；跑工作负载前需额外确认 P/D 服务、bootstrap 和实际请求链路均已就绪。
- `--enable-cache-report` 已同时出现在 P 与 D 服务脚本中；命中率解释仍应结合请求侧结果与服务端日志核对。
