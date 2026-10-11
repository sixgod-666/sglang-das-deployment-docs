#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/env.sh"

# TP8/CP8/EP8 Prefill with MegaMoE, DSPARK and Direct L3.
WORK_DIR=${WORK_DIR:-/home/xunfei/dpsk}
export ROCBLAS_TENSILE_LIBPATH=${ROCBLAS_TENSILE_LIBPATH:-/home/dspark/blas/library_gpu5}
SHCA_TOPO=${SHCA_TOPO-${WORK_DIR}/assets/topo.config}
MODEL_PATH=${MODEL_PATH:-/models/DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel}
HOSTNAME_SHORT=$(hostname -s 2>/dev/null || hostname)
LOG_FILE=${LOG_FILE:-${WORK_DIR}/logs/p_cp8ep8_pp1_dspark_${HOSTNAME_SHORT}_$(date +%Y%m%d_%H%M%S).log}
TOKENIZER_WORKER_NUM=${TOKENIZER_WORKER_NUM:-16}
# Apply on both P and D; enabled for the measured CP8 chat workload.
export SGLANG_DSV4_ASYNC_CHAT_ENCODING=${SGLANG_DSV4_ASYNC_CHAT_ENCODING:-0}
# Maximum concurrent chat encoding jobs per HTTP worker.
export SGLANG_DSV4_CHAT_ENCODING_CONCURRENCY=${SGLANG_DSV4_CHAT_ENCODING_CONCURRENCY:-0}
export SGLANG_DSV4_CHAT_ENCODING_TIMING=${SGLANG_DSV4_CHAT_ENCODING_TIMING:-0}
# Disable only external cache storage for a pure L1 test; PD still uses Mooncake.
ENABLE_DIRECT_L3=${ENABLE_DIRECT_L3:-1}
[[ "$ENABLE_DIRECT_L3" == 0 || "$ENABLE_DIRECT_L3" == 1 ]] || { echo "ENABLE_DIRECT_L3 must be 0 or 1" >&2; exit 2; }
DEEPEP_CONFIG=${DEEPEP_CONFIG-${WORK_DIR}/assets/deepep_config.json}
EXPERT_PLACEMENT=${EXPERT_PLACEMENT:-${WORK_DIR}/assets/placement_candidate.json}

[[ -d "$MODEL_PATH" ]] || { echo "Model not found: $MODEL_PATH" >&2; exit 1; }
[[ -r "$EXPERT_PLACEMENT" ]] || { echo "MegaMoE placement not found: $EXPERT_PLACEMENT" >&2; exit 1; }

TOPO_FILE=${MC_CUSTOM_TOPO_JSON:-${WORK_DIR}/assets/topology-best.json}
[[ -r "$TOPO_FILE" ]] || { echo "Mooncake topology not found: $TOPO_FILE" >&2; exit 1; }
# SGLang is an RDMA request client only. The external mooncake_client process
# owns all L3 capacity, so no SGLang rank contributes a global segment.
export MOONCAKE_MASTER=${MOONCAKE_MASTER:-127.0.0.1:50051}
export MOONCAKE_TE_META_DATA_SERVER=${MOONCAKE_TE_META_DATA_SERVER:-http://127.0.0.1:8080/metadata}
export MOONCAKE_LOCAL_HOSTNAME=${MOONCAKE_LOCAL_HOSTNAME:-${P_HOST}}
export MOONCAKE_PROTOCOL=${MOONCAKE_PROTOCOL:-rdma}
export MOONCAKE_DEVICE=
export MOONCAKE_GLOBAL_SEGMENT_SIZE=0
export MOONCAKE_TENANT_ID=${MOONCAKE_TENANT_ID:-default}
export MOONCAKE_CHECK_SERVER=1
export MC_CUSTOM_TOPO_JSON="${TOPO_FILE}"
unset MC_TOPO_FILE_FORCE
export MC_ALLOWED_IBV_DEVICES=${MC_ALLOWED_IBV_DEVICES:-shca_0,shca_1,shca_2,shca_3}
export MC_MS_FILTERS=${MC_MS_FILTERS:-${MC_ALLOWED_IBV_DEVICES}}
export MC_ENABLE_DEST_DEVICE_AFFINITY=${MC_ENABLE_DEST_DEVICE_AFFINITY:-1}
export MC_IB_GID_INDEX=${MC_IB_GID_INDEX:-0}
export MC_MS_AUTO_DISC=1

echo "[SGLang Mooncake] global_segment_size=${MOONCAKE_GLOBAL_SEGMENT_SIZE} topology=${MC_CUSTOM_TOPO_JSON}"

# Direct L3 transfers the model's native page pools directly between L1 and
# Mooncake. Keep the same HCU KV layout as the L1 baseline for a fair comparison.
export SGLANG_KV_LAYOUT_HCU_FA=${SGLANG_KV_LAYOUT_HCU_FA:-1}
# ReadPlan and read-path controls (0=off, 1=on). Requires the rebuilt package.
export SGLANG_MOONCAKE_READ_PLAN=${SGLANG_MOONCAKE_READ_PLAN:-1}
# Experimental compact reads; set READ_PLAN=0 when enabling READ_LAYOUT.
export SGLANG_MOONCAKE_READ_LAYOUT=${SGLANG_MOONCAKE_READ_LAYOUT:-0}
# Read-path controls for this CP8 configuration.
# Override individual variables before launch for comparisons.
export MOONCAKE_READ_PLAN_PIPELINE=${MOONCAKE_READ_PLAN_PIPELINE:-0}
export SGLANG_MOONCAKE_BATCH_EXISTS=${SGLANG_MOONCAKE_BATCH_EXISTS:-1}
export SGLANG_MOONCAKE_LINEAR_SCAN=${SGLANG_MOONCAKE_LINEAR_SCAN:-0}
export SGLANG_MOONCAKE_LAZY_PUT_METADATA=${SGLANG_MOONCAKE_LAZY_PUT_METADATA:-0}
export SGLANG_MOONCAKE_ASYNC_LOOKUP=${SGLANG_MOONCAKE_ASYNC_LOOKUP:-1}
# Per-rank request lookup concurrency; only used when async lookup is enabled.
export SGLANG_MOONCAKE_ASYNC_LOOKUP_WORKERS=${SGLANG_MOONCAKE_ASYNC_LOOKUP_WORKERS:-8}
# Experimental: submit waiting-request lookups before the current P forward.
# Requires ASYNC_LOOKUP=1; does not prefetch sessions or KV data.
export SGLANG_MOONCAKE_LOOKUP_PREFETCH=${SGLANG_MOONCAKE_LOOKUP_PREFETCH:-0}
export SGLANG_MOONCAKE_LOOKUP_PREFETCH_TIMING=${SGLANG_MOONCAKE_LOOKUP_PREFETCH_TIMING:-0}
export SGLANG_KV_FREE_PAGE_TABLE=${SGLANG_KV_FREE_PAGE_TABLE:-1}
export MOONCAKE_RDMA_MR_INDEX=${MOONCAKE_RDMA_MR_INDEX:-0}
export MOONCAKE_RDMA_EARLY_SUBMIT=${MOONCAKE_RDMA_EARLY_SUBMIT:-0}
export MOONCAKE_SEGMENT_LOCATION_CACHE=${MOONCAKE_SEGMENT_LOCATION_CACHE:-0}
# Per-thread cache capacity: 0 disables caching; valid range 0-256.
export MOONCAKE_SEGMENT_LOCATION_CACHE_CAPACITY=${MOONCAKE_SEGMENT_LOCATION_CACHE_CAPACITY:-0}
# TCP_NODELAY is a library fix, with no additional runtime toggle.


export PYTORCH_ALLOC_CONF=expandable_segments:True
unset SGLANG_PD_HIDDEN_POOL_TOKENS SGLANG_PD_HIDDEN_RECV_POOL_TOKENS
unset SGLANG_DEEPEP_BF16_DISPATCH
export SGLANG_SET_CPU_AFFINITY=1
export SGLANG_DISAGGREGATION_BOOTSTRAP_TIMEOUT=1200
export SGLANG_UVICORN_WORKER_HEALTHCHECK_TIMEOUT=${SGLANG_UVICORN_WORKER_HEALTHCHECK_TIMEOUT:-120}
export SGLANG_DISAGGREGATION_ALL_CP_RANKS_TRANSFER=1
export SGLANG_USE_LIGHTOP=1
export SGLANG_ROCM_USE_AITER_MOE=1
export SGLANG_ROCM_USE_AITER_TILELANG_MHC=1
export SGLANG_GROUPGEMM=true
export SGLANG_USE_FP8_W8A8_MOE=0
export SGLANG_USE_LIGHTOP_EP_MOE_ALIGN=1
export SGLANG_USE_LIGHTOP_EP_SCATTER=1
export SGLANG_USE_LIGHTOP_EP_GATHER=1
export SGLANG_USE_LIGHTOP_TOPK_IDS_POSTPROCESS=1
export SGLANG_USE_LIGHTOP_GROUP_FP8_QUANT=0
export SGLANG_OPT_USE_FUSED_HASH_TOPK=true
export SGLANG_OPT_USE_JIT_KERNEL_FUSED_TOPK=true
export SGLANG_TOPK_TRANSFORM_512_TORCH=false
export SGLANG_OPT_SWIGLU_CLAMP_FUSION=false
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
export SGLANG_W4A8_EP_USE_GROUPGEMM=true
# Target model uses MegaMoE; DSPARK draft keeps DeepEP.
export SGLANG_HCU_MEGA_MOE_RUNTIME=megamoe
export SGLANG_OPT_DEEPGEMM_MEGA_MOE_NUM_MAX_TOKENS_PER_RANK=${SGLANG_OPT_DEEPGEMM_MEGA_MOE_NUM_MAX_TOKENS_PER_RANK:-4096}
export SGLANG_MOE_COPY_WEIGHT_VIEWS_BEFORE_H2D=1
export SGLANG_DSV4_HCU_INT8_INDEX_K_CACHE=1
export SGLANG_OPT_FLASHMLA_SPARSE_PREFILL=1
export SGLANG_DSV4_SPLIT_PREFILL_DECODE_MLA=1
export SGLANG_DSV4_HCU_USE_BF16_FLASH_MLA=0
export SGLANG_DSV4_HCU_USE_LIGHTOP_BF16_GATHER=0
export SGLANG_USE_W4A8_CONTIGUOUS_HIPC=1
export SGLANG_USE_LIGHTOP_W4A8_MARLIN_MOE=false
export SGLANG_LIGHTOP_TOPK=1
export SGL_USE_LIGHTOP_TOPK_BACKAND=2
export SGLANG_LIGHTOP_KVALLOC_KERNEL=1
export W8A8_SUPPORT_METHODS=3
export SGLANG_LIGHTOP_DEQUANTIZE_K_CACHE_PAGED=1

export NCCL_IB_HCA=${NCCL_IB_HCA:-shca_0,shca_1,shca_2,shca_3}
export NCCL_NET_PLUGIN=shca
export NCCL_PLUGIN_P2P=ib
export NCCL_SOCKET_IFNAME=${NCCL_SOCKET_IFNAME:-ib0}
export GLOO_SOCKET_IFNAME=${GLOO_SOCKET_IFNAME:-ib0}
export ROCSHMEM_MAX_NUM_CONTEXTS=48
export ROCSHMEM_ALLOWED_IBV_DEVICES=${ROCSHMEM_ALLOWED_IBV_DEVICES:-${NCCL_IB_HCA}}
if [[ -n "${SHCA_TOPO:-}" ]]; then
  [[ -r "$SHCA_TOPO" ]] || { echo "SHCA topology not found: $SHCA_TOPO" >&2; exit 1; }
  export ROCSHMEM_TOPO_FILE_FORCE="$SHCA_TOPO"
fi
# An explicitly empty SHCA_TOPO leaves ROCSHMEM discovery unchanged.
if [[ -n "${ROCBLAS_TENSILE_LIBPATH:-}" && ! -d "$ROCBLAS_TENSILE_LIBPATH" ]]; then
  echo "BLAS library directory not found: $ROCBLAS_TENSILE_LIBPATH" >&2
  exit 1
fi
python - <<'CHECK'
import os
from mooncake.store import MooncakeDistributedStore
if os.environ['SGLANG_MOONCAKE_READ_LAYOUT'] == '1':
    if os.environ['SGLANG_MOONCAKE_READ_PLAN'] == '1':
        raise SystemExit('Enable only one of READ_LAYOUT and READ_PLAN.')
    if not callable(getattr(MooncakeDistributedStore, 'batch_get_into_layouts', None)):
        raise SystemExit('Installed Mooncake package is missing batch_get_into_layouts.')
if os.environ['SGLANG_MOONCAKE_READ_PLAN'] == '1' and not callable(
    getattr(MooncakeDistributedStore, 'create_read_plan', None)
):
    raise SystemExit('Installed Mooncake package is missing create_read_plan.')
CHECK

extra_args=()
if [[ "$ENABLE_DIRECT_L3" == 1 ]]; then
  extra_args+=(--enable-unified-cache-external-linker)
fi
if [[ -n "${DEEPEP_CONFIG:-}" ]]; then
  [[ -r "$DEEPEP_CONFIG" ]] || { echo "DeepEP config not found: $DEEPEP_CONFIG" >&2; exit 1; }
  extra_args+=(--deepep-config "$DEEPEP_CONFIG")
fi
mkdir -p "$(dirname "$LOG_FILE")"
cd "$WORK_DIR"
sglang serve \
  --disable-overlap-schedule \
  --reasoning-parser deepseek-v4 \
  --tool-call-parser deepseekv4 \
  --model-path "$MODEL_PATH" \
  --trust-remote-code \
  --quantization slimquant_marlin \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-${P_PORT}}" \
  --tp 8 \
  --dp 1 \
  --enable-prefill-cp \
  --cp-strategy interleave \
  --enable-cp-cache-layer-split \
  --ep 8 \
  --moe-dense-tp-size 1 \
  --moe-a2a-backend megamoe \
  --moe-runner-backend auto \
  --init-expert-location "$EXPERT_PLACEMENT" \
  --deepep-mode normal \
  --dist-init-addr "${DIST_INIT_ADDR:-127.0.0.1:5125}" \
  --dist-timeout 10000 \
  --watchdog-timeout 3600 \
  --max-total-tokens "${MAX_TOTAL_TOKENS:-1788928}" \
  --disaggregation-mode prefill \
  --disaggregation-transfer-backend mooncake \
  --disaggregation-bootstrap-port "${BOOTSTRAP_PORT:-8998}" \
  --disaggregation-ib-device "$NCCL_IB_HCA" \
  --disable-cuda-graph \
  --speculative-algorithm DSPARK \
  --speculative-num-steps 1 \
  --speculative-eagle-topk 1 \
  --speculative-moe-a2a-backend deepep \
  --speculative-moe-runner-backend deep_gemm \
  --chunked-prefill-size 32768 \
  --max-prefill-tokens 131072 \
  --max-running-requests "${MAX_RUNNING_REQUESTS:-32}" \
  --random-seed "${SEED:-42}" \
  --mem-fraction-static "${MEM_FRACTION_STATIC:-0.85}" \
  --swa-full-tokens-ratio "${SWA_FULL_TOKENS_RATIO:-0.15}" \
  --kv-cache-dtype auto \
  --disable-flashinfer-autotune \
  --model-loader-extra-config '{"enable_multithread_load": "true", "num_threads": 64}' \
  --tokenizer-worker-num "$TOKENIZER_WORKER_NUM" \
  --enable-cache-report \
  --enable-metrics \
  "${extra_args[@]}" \
  "$@" 2>&1 | tee -a "$LOG_FILE"
