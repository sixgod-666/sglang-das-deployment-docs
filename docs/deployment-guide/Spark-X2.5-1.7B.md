# Spark-X2.5-1.7B

本页合并 `02-cpu-13-yb.conf` 与 `04-dfs-13-yb.conf` 两份配置快照，按 CPU（L1 + L3）和 DFS（L1 + L3 + L4）两种形态维护。以下配置段落、键名和参数值均与所给原始配置一致；仅将内部地址、路径和日志文件替换为公开占位符。

!!! warning "使用边界"

    代码块是脱敏后的配置快照，不是可直接执行的公开脚本。部署前仅替换占位符，并重新核对目标节点、目录权限、软件包和 NIC 拓扑。不要把真实值提交回公开仓库。

## 1. 配置总览

| 项目 | CPU（L1 + L3） | DFS（L1 + L3 + L4） |
| --- | --- | --- |
| 原始配置 | `02-cpu-13-yb.conf` | `04-dfs-13-yb.conf` |
| Prefill / Decode | 两个 TP2 Prefill + TP2 Decode | 两个 TP2 Prefill + TP2 Decode |
| Mooncake segment | 300GB | 150GB |
| local buffer | 未配置 | 4GB |
| DFS / SSD Offload | 未启用 | 启用 |
| Router | PD disaggregation + mini-lb | PD disaggregation + mini-lb |

## 2. 脱敏约定

- `<NODE_IP>`：单节点 P/D/Mooncake/Router 地址。
- `<MODEL_ROOT>`：模型目录根路径。
- `<DFS_ROOT>`：DFS / Offload 根路径。
- `<PACKAGE_ROOT>`：拓扑、模板和环境准备脚本根路径。
- `<WRAPPER_ROOT>`：Python wrapper 根路径。
- `<WORKSPACE_ROOT>`：内部配置或运行工作空间根路径。
- `<LOG_FILE>`：服务日志文件路径。

## 3. 配置落地前的 Vim 替换

以下命令应在复制到内部受控文件后执行；公开站点只保留占位符。

### 3.1 模型服务名

```vim
:%s/spark-x2.5-1.7b/spark-x2.5-4b/g
```

### 3.2 占位符示例

示例值为文档地址或虚构路径，不代表真实环境：

```vim
:%s#<NODE_IP>#192.0.2.13#g
:%s#<MODEL_ROOT>#/srv/models#g
:%s#<DFS_ROOT>#/srv/kvcache/spark-dfs#g
:%s#<PACKAGE_ROOT>#/opt/sglang-packages#g
:%s#<WRAPPER_ROOT>#/opt/nlp-wrappers#g
:%s#<WORKSPACE_ROOT>#/srv/workspace/kvcache-runs#g
:%s#<LOG_FILE>#/var/log/sglang/spark-pd.log#g
```

本配置没有 `p_dist_init_addr` / `d_dist_init_addr` 字段，不要额外添加分布式初始化端口替换命令。Prefill bootstrap 端口是 `31820`、`31821`，Decode 服务端口是 `30001`。

替换后可检查遗留占位符：

```vim
:vimgrep /<[^>]*>/j %
:copen
```

## 4. CPU（L1 + L3）

原始配置：`02-cpu-13-yb.conf`。

```ini
# 由启动脚本反向生成的 CPU 配置；内部地址、路径已脱敏。

[common]
model_path = <MODEL_ROOT>/Spark-X2.5-1.7B
bootstrap_port = 31820
p_node_ip = <NODE_IP>
d_node_ip = <NODE_IP>
mc_node_ip = <NODE_IP>
ib_devices = shca_0,shca_1,shca_2,shca_3
topo_config = <PACKAGE_ROOT>/Spark_config/deep_topo.config

[pd_disagg]
prefill_nodes = ${common:p_node_ip}
decode_nodes = ${common:d_node_ip}
mooncake_clients = ${common:mc_node_ip}
router_ip = ${common:p_node_ip}

[mooncake_master]
rpc_port = 50156
rpc_address = <NODE_IP>
enable_http_metadata_server = true
http_metadata_server_host = <NODE_IP>
http_metadata_server_port = 8095
rpc_enable_tcp_no_delay = true
enable_metric_reporting = true
metrics_port = 9014
default_kv_lease_ttl = 30000
allocation_strategy = free_ratio_first
eviction_ratio = 0.1
eviction_high_watermark_ratio = 0.9
minloglevel = 0
logtostderr = false
alsologtostderr = true

[mooncake_master_global]
NODE_IP = <NODE_IP>
MASTER_BIND_HOST = <NODE_IP>
METADATA_BIND_HOST = <NODE_IP>

[mooncake_client]
master_server_address = ${common:p_node_ip}:50156
metadata_server = http://<NODE_IP>:8095/metadata
host = ${common:mc_node_ip}
port = 50052
enable_http_server = true
http_port = 9300
protocol = rdma
device_names = shca_0
global_segment_size = 300GB
minloglevel = 0
logtostderr = false
alsologtostderr = true

[mooncake_client_global]
MOONCAKE_LOCAL_HOSTNAME = ${common:mc_node_ip}
MOONCAKE_TE_META_DATA_SERVER = http://<NODE_IP>:8080/metadata
MASTER_HOST = <NODE_IP>
STORE_BIND_HOST = <NODE_IP>
MC_ENABLE_DEST_DEVICE_AFFINITY = 1
MC_IB_GID_INDEX = 0
MC_IB_PCI_RELAXED_ORDERING = 0

[router]
pd-disaggregation = true
prefill_endpoints = http://<NODE_IP>:20001 31821,http://<NODE_IP>:30000 31820
decode_port = 30001
mini-lb = true
request-timeout-secs = 21600
host = <NODE_IP>
port = 30320
log-level = info

[sglang_prefill]
swa-full-tokens-ratio = 0.15
hicache-storage-backend-extra-config = {"extra_backend_tag": "spark-x2.5-1.7b"}
extra-metric-labels = {"model_name":"spark-x2.5-1.7b"}
disaggregation-bootstrap-port = ${common:bootstrap_port}
enable-unified-cache-external-linker = true
tp = 2
model-path = ${common:model_path}
mem-fraction-static = 0.85
enable-metrics = true
served-model-name = spark-x2.5-1.7b
max-running-requests = 12
tool-call-parser = spark
reasoning-parser = qwen3
allow-auto-truncate = true
enable-cache-report = true
chat-template = <PACKAGE_ROOT>/Spark_config/chat_template.jinja
enable-strict-thinking = true
attention-backend = triton
prefill-attention-backend = triton
decode-attention-backend = triton
disaggregation-mode = prefill
disaggregation-transfer-backend = mooncake
disaggregation-ib-device = ${common:ib_devices}
chunked-prefill-size = 16384
log-level = info
page-size = 64
watchdog-timeout = 300
soft-watchdog-timeout = 120
host = <NODE_IP>
tokenizer-backend = fastokens

[global_prefill]
source_envs = <PACKAGE_ROOT>/Spark_config/dcu_host_prepare.sh
unset_envs = MC_FORCE_TCP,LD_PRELOAD
SGLANG_CUDA_COREDUMP = 1
SGLANG_CUDA_COREDUMP_DIR = /tmp/cuda_coredump
CUDA_ENABLE_USER_TRIGGERED_COREDUMP = 1
SGLANG_HEALTH_CHECK_TIMEOUT = 120
SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT = 1200
SGLANG_DISAGGREGATION_WAITING_TIMEOUT = 1000
PYTHONPATH = <WRAPPER_ROOT>/
SGLANG_KV_LAYOUT_HCU_FA = 0
TORCH_DEVICE_BACKEND_AUTOLOAD = 0
MC_MS_AUTO_DISC = 1
MC_ENABLE_DEST_DEVICE_AFFINITY = 1
MC_TRANSFER_TIMEOUT = 120
MC_ALLOWED_IBV_DEVICES = shca_0,shca_1,shca_2,shca_3
MC_TCP_ENABLE_CONNECTION_POOL = true
MC_SLICE_SIZE = 1048576
NCCL_IB_DISABLE = 0
NCCL_IB_HCA = ${common:ib_devices}
NCCL_PLUGIN_P2P = ib
NCCL_NET_PLUGIN = shca
NCCL_PXN_DISABLE = 0
RCCL_NET_PLANE = shca_0,shca_3|shca_1,shca_2
ROCSHMEM_ALLOWED_IBV_DEVICES = ${common:ib_devices}
ROCSHMEM_TOPO_FILE_FORCE = ${common:topo_config}
HSA_ENABLE_COREDUMP = 1
USE_DCU_CUSTOM_ALLREDUCE = 0
ALLREDUCE_STREAM_WITH_COMPUTE = 1
HIP_KERNEL_EVENT_SYSTENFENCE = 1
NCCL_MAX_NCHANNELS = 16
NCCL_MIN_NCHANNELS = 16
HIP_H2D_DISABLE_COPY_BUFFER = 0
HIP_D2H_DISABLE_COPY_BUFFER = 0
HIP_H2D_DIRECT_COPY_THRESHOLD = 32768
HIP_H2D_HSAAPI_COPY_THRESHOLD = 32768
HIP_D2H_DIRECT_COPY_THRESHOLD = 512
HIP_D2H_HSAAPI_COPY_THRESHOLD = 512
SGLANG_USE_AITER_AR = 0
PYTORCH_ALLOC_CONF = expandable_segments:True
MC_IB_PCI_RELAXED_ORDERING = 0
NCCL_SOCKET_IFNAME = ib0
GLOO_SOCKET_IFNAME = ib0
MOONCAKE_LOCAL_HOSTNAME = ${common:p_node_ip}
MOONCAKE_MASTER = ${common:p_node_ip}:50156
MOONCAKE_TE_META_DATA_SERVER = http://<NODE_IP>:8095/metadata
MOONCAKE_PROTOCOL = rdma
MOONCAKE_DEVICE = shca_0,shca_1,shca_2,shca_3
MOONCAKE_GLOBAL_SEGMENT_SIZE = 0gb
FASTOKENS_BPE_THREADS=1

[sglang_prefill_2]
swa-full-tokens-ratio = 0.15
hicache-storage-backend-extra-config = {"extra_backend_tag": "spark-x2.5-1.7b"}
extra-metric-labels = {"model_name":"spark-x2.5-1.7b"}
disaggregation-bootstrap-port = 31821
enable-unified-cache-external-linker = true
tp = 2
model-path = ${common:model_path}
mem-fraction-static = 0.85
enable-metrics = true
served-model-name = spark-x2.5-1.7b
max-running-requests = 12
tool-call-parser = spark
reasoning-parser = qwen3
allow-auto-truncate = true
enable-cache-report = true
chat-template = <PACKAGE_ROOT>/Spark_config/chat_template.jinja
enable-strict-thinking = true
attention-backend = triton
prefill-attention-backend = triton
decode-attention-backend = triton
disaggregation-mode = prefill
disaggregation-transfer-backend = mooncake
disaggregation-ib-device = ${common:ib_devices}
chunked-prefill-size = 16384
log-level = info
page-size = 64
watchdog-timeout = 300
soft-watchdog-timeout = 120
host = <NODE_IP>
port = 20001
tokenizer-backend = fastokens

[global_prefill_2]
source_envs = <PACKAGE_ROOT>/Spark_config/dcu_host_prepare.sh
unset_envs = MC_FORCE_TCP,LD_PRELOAD
SGLANG_CUDA_COREDUMP = 1
SGLANG_CUDA_COREDUMP_DIR = /tmp/cuda_coredump
CUDA_ENABLE_USER_TRIGGERED_COREDUMP = 1
SGLANG_HEALTH_CHECK_TIMEOUT = 120
SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT = 1200
SGLANG_DISAGGREGATION_WAITING_TIMEOUT = 1000
PYTHONPATH = <WRAPPER_ROOT>/
SGLANG_KV_LAYOUT_HCU_FA = 0
TORCH_DEVICE_BACKEND_AUTOLOAD = 0
MC_MS_AUTO_DISC = 1
MC_ENABLE_DEST_DEVICE_AFFINITY = 1
MC_TRANSFER_TIMEOUT = 120
MC_ALLOWED_IBV_DEVICES = shca_0,shca_1,shca_2,shca_3
MC_TCP_ENABLE_CONNECTION_POOL = true
MC_SLICE_SIZE = 1048576
NCCL_IB_DISABLE = 0
NCCL_IB_HCA = ${common:ib_devices}
NCCL_PLUGIN_P2P = ib
NCCL_NET_PLUGIN = shca
NCCL_PXN_DISABLE = 0
RCCL_NET_PLANE = shca_0,shca_3|shca_1,shca_2
ROCSHMEM_ALLOWED_IBV_DEVICES = ${common:ib_devices}
ROCSHMEM_TOPO_FILE_FORCE = ${common:topo_config}
HSA_ENABLE_COREDUMP = 1
USE_DCU_CUSTOM_ALLREDUCE = 0
ALLREDUCE_STREAM_WITH_COMPUTE = 1
HIP_KERNEL_EVENT_SYSTENFENCE = 1
NCCL_MAX_NCHANNELS = 16
NCCL_MIN_NCHANNELS = 16
HIP_H2D_DISABLE_COPY_BUFFER = 0
HIP_D2H_DISABLE_COPY_BUFFER = 0
HIP_H2D_DIRECT_COPY_THRESHOLD = 32768
HIP_H2D_HSAAPI_COPY_THRESHOLD = 32768
HIP_D2H_DIRECT_COPY_THRESHOLD = 512
HIP_D2H_HSAAPI_COPY_THRESHOLD = 512
SGLANG_USE_AITER_AR = 0
PYTORCH_ALLOC_CONF = expandable_segments:True
MC_IB_PCI_RELAXED_ORDERING = 0
NCCL_SOCKET_IFNAME = ib0
GLOO_SOCKET_IFNAME = ib0
MOONCAKE_LOCAL_HOSTNAME = ${common:p_node_ip}
MOONCAKE_MASTER = ${common:p_node_ip}:50156
MOONCAKE_TE_META_DATA_SERVER = http://<NODE_IP>:8095/metadata
MOONCAKE_PROTOCOL = rdma
MOONCAKE_DEVICE = shca_0,shca_1,shca_2,shca_3
MOONCAKE_GLOBAL_SEGMENT_SIZE = 0gb
HIP_VISIBLE_DEVICES = 4,5
FASTOKENS_BPE_THREADS=1

[sglang_decode]
tp = 2
model-path = ${common:model_path}
mem-fraction-static = 0.85
enable-metrics = true
served-model-name = spark-x2.5-1.7b
max-running-requests = 12
context-length = 1048576
tool-call-parser = spark
reasoning-parser = qwen3
allow-auto-truncate = true
enable-cache-report = true
chat-template = <PACKAGE_ROOT>/Spark_config/chat_template.jinja
enable-strict-thinking = true
attention-backend = triton
prefill-attention-backend = triton
decode-attention-backend = triton
disaggregation-mode = decode
disaggregation-transfer-backend = mooncake
chunked-prefill-size = 16384
extra-metric-labels = {"model_name":"spark-x2.5-1.7b"}
disaggregation-ib-device = ${common:ib_devices}
page-size = 64
hicache-storage-backend-extra-config = {"extra_backend_tag": "spark-x2.5-1.7b"}
disaggregation-decode-retraction-backup = host_pool
hicache-ratio = 0.4
triton-attention-num-kv-splits = 128
log-level = info
host = <NODE_IP>
port = 30001
tokenizer-backend = fastokens

[global_decode]
source_envs = <PACKAGE_ROOT>/Spark_config/dcu_host_prepare.sh
SGLANG_HEALTH_CHECK_TIMEOUT = 120
SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT = 1200
SGLANG_DISAGGREGATION_WAITING_TIMEOUT = 1000
PYTHONPATH = <WRAPPER_ROOT>/
MOONCAKE_PROTOCOL = rdma
RDMA_DEVICES = shca_0,shca_1,shca_2,shca_3
MOONCAKE_DEVICE = shca_0,shca_1,shca_2,shca_3
LD_LIBRARY_PATH = /opt/dtk/hip/lib:/opt/dtk/lib:/opt/dtk/dcc/lib:/opt/hyhal/lib:/usr/local/lib/python3.10/dist-packages/amdsmi:$LD_LIBRARY_PATH
SGLANG_KV_LAYOUT_HCU_FA = 0
TORCH_DEVICE_BACKEND_AUTOLOAD = 0
MC_MS_AUTO_DISC = 1
MC_ENABLE_DEST_DEVICE_AFFINITY = 1
MC_TRANSFER_TIMEOUT = 120
MC_ALLOWED_IBV_DEVICES = shca_0,shca_1,shca_2,shca_3
MC_TCP_ENABLE_CONNECTION_POOL = true
MC_SLICE_SIZE = 1048576
NCCL_IB_DISABLE = 0
NCCL_IB_HCA = ${common:ib_devices}
NCCL_PLUGIN_P2P = ib
NCCL_NET_PLUGIN = shca
NCCL_PXN_DISABLE = 0
RCCL_NET_PLANE = shca_0,shca_3|shca_1,shca_2
ROCSHMEM_ALLOWED_IBV_DEVICES = ${common:ib_devices}
HSA_ENABLE_COREDUMP = 1
USE_DCU_CUSTOM_ALLREDUCE = 0
ALLREDUCE_STREAM_WITH_COMPUTE = 1
HIP_KERNEL_EVENT_SYSTENFENCE = 1
NCCL_MAX_NCHANNELS = 16
NCCL_MIN_NCHANNELS = 16
HIP_H2D_DISABLE_COPY_BUFFER = 0
HIP_D2H_DISABLE_COPY_BUFFER = 0
HIP_H2D_DIRECT_COPY_THRESHOLD = 32768
HIP_H2D_HSAAPI_COPY_THRESHOLD = 32768
HIP_D2H_DIRECT_COPY_THRESHOLD = 512
HIP_D2H_HSAAPI_COPY_THRESHOLD = 512
SGLANG_USE_AITER_AR = 0
ROCSHMEM_TOPO_FILE_FORCE = ${common:topo_config}
ROCSHMEM_MAX_NUM_CONTEXTS = 60
PYTORCH_ALLOC_CONF = expandable_segments:True
MC_IB_PCI_RELAXED_ORDERING = 0
MOONCAKE_GLOBAL_SEGMENT_SIZE = 0
MOONCAKE_MASTER = localhost:50051
NCCL_SOCKET_IFNAME = ib0
GLOO_SOCKET_IFNAME = ib0
HIP_VISIBLE_DEVICES = 2,3
FASTOKENS_BPE_THREADS=1
```

## 5. DFS（L1 + L3 + L4）

原始配置：`04-dfs-13-yb.conf`。

```ini
# Spark-X2.5-1.7B PD + Mooncake DFS, reproduced from historical small-model configuration
# source_envs is emitted in the service shell immediately before nohup.

[common]
node_ip = <NODE_IP>
dfs_root = <DFS_ROOT>
ib_devices = shca_0,shca_1,shca_2,shca_3
model_path = <MODEL_ROOT>/Spark-X2.5-1.7B
chat_template = <PACKAGE_ROOT>/Spark_config/chat_template.jinja
dcu_prepare = <PACKAGE_ROOT>/Spark_config/dcu_host_prepare.sh

[pd_disagg]
prefill_nodes = ${common:node_ip}
decode_nodes = ${common:node_ip}
mooncake_clients = ${common:node_ip}
router_ip = ${common:node_ip}

[mooncake_master_global]
NODE_IP = ${common:node_ip}
MASTER_BIND_HOST = ${common:node_ip}
METADATA_BIND_HOST = ${common:node_ip}
MOONCAKE_ENABLE_DFS = 1
MOONCAKE_DFS_FS_ADAPTER = posix
MOONCAKE_DFS_ROOT_DIR = ${common:dfs_root}
MOONCAKE_DFS_ALLOCATOR_TYPE = bucket
MOONCAKE_DFS_BUCKET_CAPACITY = 4294967296
MOONCAKE_DFS_MAX_BUCKET_COUNT = 5120
MOONCAKE_DFS_ALIGNMENT = 4096
MOONCAKE_DFS_SINGLE_TENANT = True
MOONCAKE_DFS_BUCKET_META_LOG_THRESHOLD = 4194304
MOONCAKE_DFS_EVICTION_ENABLED = True
MOONCAKE_DFS_EVICTION_HIGH_WATERMARK = 0.80
MOONCAKE_DFS_EVICTION_LOW_WATERMARK = 0.60
MOONCAKE_DFS_EVICTION_CHECK_INTERVAL = 2
MOONCAKE_DFS_DEFERRED_FREE_SECONDS = 10

[mooncake_master]
enable_offload = true
rpc_port = 50156
rpc_address = ${common:node_ip}
enable_http_metadata_server = true
http_metadata_server_host = ${common:node_ip}
http_metadata_server_port = 8095
rpc_enable_tcp_no_delay = true
enable_metric_reporting = true
metrics_port = 9014
default_kv_lease_ttl = 30000
allocation_strategy = free_ratio_first
eviction_ratio = 0.1
eviction_high_watermark_ratio = 0.9
minloglevel = 0
logtostderr = false
alsologtostderr = true

[mooncake_client_global]
MOONCAKE_LOCAL_HOSTNAME = ${common:node_ip}
MOONCAKE_TE_META_DATA_SERVER = http://${common:node_ip}:8080/metadata
MASTER_HOST = ${common:node_ip}
STORE_BIND_HOST = ${common:node_ip}
MC_ENABLE_DEST_DEVICE_AFFINITY = 1
MC_IB_GID_INDEX = 0
MC_IB_PCI_RELAXED_ORDERING = 0
MOONCAKE_OFFLOAD_FILE_STORAGE_PATH = ${common:dfs_root}
MC_STORE_CLIENT_METRIC = 1
MOONCAKE_ENABLE_DFS = 1
MOONCAKE_DFS_FS_ADAPTER = posix
MOONCAKE_DFS_ROOT_DIR = ${common:dfs_root}
MOONCAKE_DFS_ALLOCATOR_TYPE = bucket
MOONCAKE_DFS_BUCKET_CAPACITY = 4294967296
MOONCAKE_DFS_MAX_BUCKET_COUNT = 5120
MOONCAKE_DFS_ALIGNMENT = 4096
MOONCAKE_DFS_SINGLE_TENANT = True
MOONCAKE_OFFLOAD_ENABLED = True
MOONCAKE_OFFLOAD_STORAGE_BACKEND_DESCRIPTOR = distributed_storage_backend

[mooncake_client]
enable_offload = true
local_buffer_size = 4GB
master_server_address = ${common:node_ip}:50156
metadata_server = http://${common:node_ip}:8095/metadata
host = ${common:node_ip}
port = 50052
enable_http_server = true
http_port = 9300
protocol = rdma
device_names = shca_0
global_segment_size = 150GB
minloglevel = 0
logtostderr = false
alsologtostderr = true

[global_prefill_common]
SGLANG_CUDA_COREDUMP = 1
SGLANG_CUDA_COREDUMP_DIR = /tmp/cuda_coredump
CUDA_ENABLE_USER_TRIGGERED_COREDUMP = 1
SGLANG_HEALTH_CHECK_TIMEOUT = 120
SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT = 1200
SGLANG_DISAGGREGATION_WAITING_TIMEOUT = 1000
PYTHONPATH = <WRAPPER_ROOT>/
SGLANG_KV_LAYOUT_HCU_FA = 0
TORCH_DEVICE_BACKEND_AUTOLOAD = 0
MC_MS_AUTO_DISC = 1
MC_ENABLE_DEST_DEVICE_AFFINITY = 1
MC_TRANSFER_TIMEOUT = 120
MC_ALLOWED_IBV_DEVICES = ${common:ib_devices}
MC_TCP_ENABLE_CONNECTION_POOL = true
MC_SLICE_SIZE = 1048576
NCCL_IB_DISABLE = 0
NCCL_IB_HCA = ${common:ib_devices}
NCCL_PLUGIN_P2P = ib
NCCL_NET_PLUGIN = shca
NCCL_PXN_DISABLE = 0
RCCL_NET_PLANE = shca_0,shca_3|shca_1,shca_2
ROCSHMEM_ALLOWED_IBV_DEVICES = ${common:ib_devices}
ROCSHMEM_TOPO_FILE_FORCE = <PACKAGE_ROOT>/Spark_config/deep_topo.config
HSA_ENABLE_COREDUMP = 1
USE_DCU_CUSTOM_ALLREDUCE = 0
ALLREDUCE_STREAM_WITH_COMPUTE = 1
HIP_KERNEL_EVENT_SYSTENFENCE = 1
NCCL_MAX_NCHANNELS = 16
NCCL_MIN_NCHANNELS = 16
HIP_H2D_DISABLE_COPY_BUFFER = 0
HIP_D2H_DISABLE_COPY_BUFFER = 0
HIP_H2D_DIRECT_COPY_THRESHOLD = 32768
HIP_H2D_HSAAPI_COPY_THRESHOLD = 32768
HIP_D2H_DIRECT_COPY_THRESHOLD = 512
HIP_D2H_HSAAPI_COPY_THRESHOLD = 512
SGLANG_USE_AITER_AR = 0
PYTORCH_ALLOC_CONF = expandable_segments:True
MC_IB_PCI_RELAXED_ORDERING = 0
NCCL_SOCKET_IFNAME = ib0
GLOO_SOCKET_IFNAME = ib0
MOONCAKE_LOCAL_HOSTNAME = ${common:node_ip}
MOONCAKE_MASTER = ${common:node_ip}:50156
MOONCAKE_TE_META_DATA_SERVER = http://${common:node_ip}:8095/metadata
MOONCAKE_PROTOCOL = rdma
MOONCAKE_DEVICE = ${common:ib_devices}
MOONCAKE_GLOBAL_SEGMENT_SIZE = 0gb
MC_TE_FILTERS = ${common:ib_devices}
SGLANG_HOST_IP = ${common:node_ip}
MOONCAKE_ENABLE_DFS = 1
MOONCAKE_DFS_FS_ADAPTER = posix
MOONCAKE_DFS_ROOT_DIR = ${common:dfs_root}
MOONCAKE_DFS_ALLOCATOR_TYPE = bucket
MOONCAKE_DFS_BUCKET_CAPACITY = 4294967296
MOONCAKE_DFS_MAX_BUCKET_COUNT = 5120
MOONCAKE_DFS_ALIGNMENT = 4096
MOONCAKE_DFS_SINGLE_TENANT = True
MOONCAKE_DFS_BUCKET_META_LOG_THRESHOLD = 4194304
MOONCAKE_DFS_EVICTION_ENABLED = True
MOONCAKE_ENABLE_SSD_OFFLOAD = 1
MOONCAKE_OFFLOAD_STORAGE_BACKEND_DESCRIPTOR = distributed_storage_backend
MOONCAKE_OFFLOAD_FILE_STORAGE_PATH = ${common:dfs_root}
MC_STORE_DFS_READ_TRACE = 1
MOONCAKE_DFS_BATCH_READ_THREADS = 32
SGLANG_UVICORN_WORKER_HEALTHCHECK_TIMEOUT = 60
MOONCAKE_DFS_FORCE_ONE_REPLICA = True
MC_STORE_DFS_PINNED_POOL_BYTES = 8589934592
MOONCAKE_DFS_BATCH_READ_MERGE_ENABLED = 1
SGLANG_LIGHTOP_DEQUANTIZE_K_CACHE_PAGED = 1
MC_STORE_CLIENT_METRIC = 0
MC_STORE_MEMCPY = 1
MC_STORE_PINNED_RESTORE_ARENA_SIZE_BYTES = 8589934592
MC_STORE_DFS_PREFETCH_ARENA_SIZE_BYTES = 6442450944
SGLANG_MOONCAKE_FINAL_POLL_TIMEOUT_MS = 400
FASTOKENS_BPE_THREADS = 1
SGLANG_TIMEOUT_KEEP_ALIVE = 75
SGLANG_MOONCAKE_SESSION_REFRESH_AGE_S = 5
MOONCAKE_MASTER_METRICS_PORT = 9014
source_envs = ${common:dcu_prepare}

[global_prefill]

[global_prefill_2]
HIP_VISIBLE_DEVICES = 4,5

[sglang_prefill]
command = env -u MC_FORCE_TCP -u LD_PRELOAD python -m sglang.launch_server
log_file = <LOG_FILE>
swa-full-tokens-ratio = 0.15
hicache-storage-backend-extra-config = {"extra_backend_tag": "spark-x2.5-1.7b"}
extra-metric-labels = {"model_name":"spark-x2.5-1.7b"}
disaggregation-bootstrap-port = 31820
tp = 2
model-path = ${common:model_path}
mem-fraction-static = 0.85
enable-metrics = true
served-model-name = spark-x2.5-1.7b
max-running-requests = 12
tool-call-parser = spark
reasoning-parser = qwen3
allow-auto-truncate = true
enable-cache-report = true
chat-template = ${common:chat_template}
enable-strict-thinking = true
attention-backend = triton
prefill-attention-backend = triton
decode-attention-backend = triton
disaggregation-mode = prefill
disaggregation-transfer-backend = mooncake
disaggregation-ib-device = ${common:ib_devices}
chunked-prefill-size = 16384
log-level = info
page-size = 64
watchdog-timeout = 300
soft-watchdog-timeout = 120
host = ${common:node_ip}
enable-unified-cache-external-linker = true
enable-request-time-stats-logging = true
unified-cache-external-linker-backend = mooncake
mooncake-enable-page-wise-load = true
#disable-overlap-schedule = true
mooncake-enable-waiting-queue-dfs-prefetch = true
mooncake-waiting-queue-dfs-prefetch-workers = 1
mooncake-waiting-queue-dfs-prefetch-max-requests = 12
tokenizer-backend = fastokens

[sglang_prefill_2]
command = env -u MC_FORCE_TCP -u LD_PRELOAD python -m sglang.launch_server
log_file = <LOG_FILE>
swa-full-tokens-ratio = 0.15
hicache-storage-backend-extra-config = {"extra_backend_tag": "spark-x2.5-1.7b"}
extra-metric-labels = {"model_name":"spark-x2.5-1.7b"}
disaggregation-bootstrap-port = 31821
tp = 2
model-path = ${common:model_path}
mem-fraction-static = 0.85
enable-metrics = true
served-model-name = spark-x2.5-1.7b
max-running-requests = 12
tool-call-parser = spark
reasoning-parser = qwen3
allow-auto-truncate = true
enable-cache-report = true
chat-template = ${common:chat_template}
enable-strict-thinking = true
attention-backend = triton
prefill-attention-backend = triton
decode-attention-backend = triton
disaggregation-mode = prefill
disaggregation-transfer-backend = mooncake
disaggregation-ib-device = ${common:ib_devices}
chunked-prefill-size = 16384
log-level = info
page-size = 64
watchdog-timeout = 300
soft-watchdog-timeout = 120
host = ${common:node_ip}
enable-unified-cache-external-linker = true
enable-request-time-stats-logging = true
unified-cache-external-linker-backend = mooncake
mooncake-enable-page-wise-load = true
#disable-overlap-schedule = true
mooncake-enable-waiting-queue-dfs-prefetch = true
mooncake-waiting-queue-dfs-prefetch-workers = 1
mooncake-waiting-queue-dfs-prefetch-max-requests = 12
port = 20001
tokenizer-backend = fastokens

[global_decode]
SGLANG_HEALTH_CHECK_TIMEOUT = 120
SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT = 1200
SGLANG_DISAGGREGATION_WAITING_TIMEOUT = 1000
PYTHONPATH = <WRAPPER_ROOT>/
MOONCAKE_PROTOCOL = rdma
RDMA_DEVICES = ${common:ib_devices}
MOONCAKE_DEVICE = ${common:ib_devices}
LD_LIBRARY_PATH = /opt/dtk/hip/lib:/opt/dtk/lib:/opt/dtk/dcc/lib:/opt/hyhal/lib:/usr/local/lib/python3.10/dist-packages/amdsmi:$LD_LIBRARY_PATH
SGLANG_KV_LAYOUT_HCU_FA = 0
TORCH_DEVICE_BACKEND_AUTOLOAD = 0
MC_MS_AUTO_DISC = 1
MC_ENABLE_DEST_DEVICE_AFFINITY = 1
MC_TRANSFER_TIMEOUT = 120
MC_ALLOWED_IBV_DEVICES = ${common:ib_devices}
MC_TCP_ENABLE_CONNECTION_POOL = true
MC_SLICE_SIZE = 1048576
NCCL_IB_DISABLE = 0
NCCL_IB_HCA = ${common:ib_devices}
NCCL_PLUGIN_P2P = ib
NCCL_NET_PLUGIN = shca
NCCL_PXN_DISABLE = 0
RCCL_NET_PLANE = shca_0,shca_3|shca_1,shca_2
ROCSHMEM_ALLOWED_IBV_DEVICES = ${common:ib_devices}
HSA_ENABLE_COREDUMP = 1
USE_DCU_CUSTOM_ALLREDUCE = 0
ALLREDUCE_STREAM_WITH_COMPUTE = 1
HIP_KERNEL_EVENT_SYSTENFENCE = 1
NCCL_MAX_NCHANNELS = 16
NCCL_MIN_NCHANNELS = 16
HIP_H2D_DISABLE_COPY_BUFFER = 0
HIP_D2H_DISABLE_COPY_BUFFER = 0
HIP_H2D_DIRECT_COPY_THRESHOLD = 32768
HIP_H2D_HSAAPI_COPY_THRESHOLD = 32768
HIP_D2H_DIRECT_COPY_THRESHOLD = 512
HIP_D2H_HSAAPI_COPY_THRESHOLD = 512
SGLANG_USE_AITER_AR = 0
ROCSHMEM_TOPO_FILE_FORCE = <PACKAGE_ROOT>/Spark_config/deep_topo.config
ROCSHMEM_MAX_NUM_CONTEXTS = 60
PYTORCH_ALLOC_CONF = expandable_segments:True
MC_IB_PCI_RELAXED_ORDERING = 0
MOONCAKE_GLOBAL_SEGMENT_SIZE = 0
MOONCAKE_MASTER = localhost:50051
NCCL_SOCKET_IFNAME = ib0
GLOO_SOCKET_IFNAME = ib0
FASTOKENS_BPE_THREADS=1
HIP_VISIBLE_DEVICES = 2,3
source_envs = ${common:dcu_prepare}

[sglang_decode]
command = sglang serve
log_file = <LOG_FILE>
tp = 2
model-path = ${common:model_path}
mem-fraction-static = 0.85
enable-metrics = true
served-model-name = spark-x2.5-1.7b
max-running-requests = 12
context-length = 1048576
tool-call-parser = spark
reasoning-parser = qwen3
allow-auto-truncate = true
enable-cache-report = true
chat-template = ${common:chat_template}
enable-strict-thinking = true
attention-backend = triton
prefill-attention-backend = triton
decode-attention-backend = triton
disaggregation-mode = decode
disaggregation-transfer-backend = mooncake
chunked-prefill-size = 16384
extra-metric-labels = {"model_name":"spark-x2.5-1.7b"}
disaggregation-ib-device = ${common:ib_devices}
page-size = 64
hicache-storage-backend-extra-config = {"extra_backend_tag": "spark-x2.5-1.7b"}
disaggregation-decode-retraction-backup = host_pool
hicache-ratio = 0.4
triton-attention-num-kv-splits = 128
log-level = info
host = ${common:node_ip}
port = 30001
tokenizer-backend = fastokens

[router]
command = python -m sglang_router.launch_router
log_file = <LOG_FILE>
prefill_endpoints = http://${common:node_ip}:20001 31821,http://${common:node_ip}:30000 31820
decode_port = 30001
pd-disaggregation = true
mini-lb = true
request-timeout-secs = 21600
host = ${common:node_ip}
port = 30320
log-level = info
```

## 6. 两种配置的关键差异

- CPU 版使用 Mooncake client `global_segment_size = 300GB`，不包含 DFS bucket、SSD offload 或 DFS prefetch。
- DFS 版使用 `global_segment_size = 150GB`、`local_buffer_size = 4GB`，启用 DFS bucket、SSD offload、page-wise load 和等待队列 prefetch。
- 两份配置的 section、环境变量和服务参数均按原配置保留；文档只做敏感值脱敏。
