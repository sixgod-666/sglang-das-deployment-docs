import argparse
import asyncio
import json
import queue
import random
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import requests
from tqdm.asyncio import tqdm

from sglang.bench_serving import RequestFuncOutput
from sglang.benchmark.datasets.random import sample_random_requests
from sglang.benchmark.utils import get_tokenizer
from sglang.test.kits.cache_hit_kit import (
    async_request_openai_chat_completions,
    async_request_sglang_generate,
    gen_payload,
    gen_payload_openai,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Script to benchmark concurrent requests to a server."
    )
    parser.add_argument(
        "--num-clients",
        type=int,
        default=256,
        help="Number of concurrent clients",
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=128,
        help="Maximum number of parallel requests",
    )
    parser.add_argument(
        "--request-length",
        type=int,
        default=512,
        help="Length of each new request",
    )
    parser.add_argument(
        "--output-length",
        type=int,
        default=64,
        help="Length of each output",
    )
    parser.add_argument(
        "--num-rounds",
        type=int,
        default=5,
        help="Number of rounds per client",
    )
    parser.add_argument(
        "--distribution",
        type=str,
        default="poisson",
        choices=["poisson", "uniform"],
        help="Distribution type for request intervals (poisson or uniform)",
    )
    parser.add_argument(
        "--request-rate",
        type=float,
        default=1.0,
        help="Average number of requests per second",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="Server hostname or IP (default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=30000,
        help="Server port (default: 30000)",
    )
    parser.add_argument(
        "--model-path",
        type=str,
        default="meta-llama/Llama-3.1-8B-Instruct",
        help="model path compatible with Hugging Face Transformers",
    )
    parser.add_argument(
        "--dataset-path",
        type=str,
        default="",
        help="local dataset to sample tokens from",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default="performance_metrics.jsonl",
        help="File to log performance metrics",
    )
    parser.add_argument(
        "--disable-auto-run",
        action="store_true",
        help="If set, disable automatically testing with a range of request rates.",
    )
    parser.add_argument(
        "--disable-random-sample",
        action="store_true",
        help="If set, disable random sampling of requests from the ShareGPT dataset.",
    )
    parser.add_argument(
        "--enable-round-barrier",
        action="store_true",
        help="If set, only send i-th turn requests after all (i-1)-th turn requests finished.",
    )
    parser.add_argument(
        "--sub-question-input-length",
        type=int,
        default=0,
        help="Length of the sub question input for each request, if set 0 use request_length",
    )
    parser.add_argument(
        "--ready-queue-policy",
        type=str,
        default="random",
        help="Policy for popping requests from the ready queue (random or fifo)",
    )
    parser.add_argument(
        "--tag",
        type=str,
        default="",
        help="Tag of a certain run in the log file",
    )
    parser.add_argument(
        "--min-rounds",
        type=int,
        default=0,
        help="Min rounds per client (0 = use --num-rounds)",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=0,
        help="Max rounds per client (0 = use --num-rounds)",
    )
    parser.add_argument(
        "--range-ratio",
        type=float,
        default=1.0,
        help="Length variation ratio for prompts and outputs (1.0 = no variation, 0.5 = 50%% variation)",
    )
    parser.add_argument("--seed", type=int, default=1, help="The random seed.")
    parser.add_argument(
        "--lora-path",
        type=str,
        default="",
        help="String of LoRA path. Currently we only support benchmarking on a single LoRA adaptor.",
    )
    parser.add_argument(
        "--api-format",
        type=str,
        default="sglang",
        choices=["sglang", "openai"],
        help="API format to use: 'sglang' for native /generate endpoint, "
        "'openai' for OpenAI-compatible /v1/chat/completions endpoint.",
    )
    parser.add_argument(
        "--enable-dp-affinity",
        action="store_true",
        help="Pin each client to routed_dp_rank = client_id %% dp_size so the "
        "same conversation stays on one DP rank (needed for L1 prefix reuse).",
    )
    parser.add_argument(
        "--dp-size",
        type=int,
        default=8,
        help="DP world size used with --enable-dp-affinity (default: 8).",
    )
    parser.add_argument(
        "--disable-heartbeat",
        action="store_true",
        help="Skip the post-barrier heartbeat request (avoids extra KV on a random rank).",
    )
    parser.add_argument(
        "--round-gap-s",
        type=float,
        default=10.0,
        help="Sleep between barrier rounds, in seconds (0 disables the wait).",
    )
    parser.add_argument(
        "--round-cache-control-url",
        default="",
        help="Worker URL for optional between-round cache diagnostics.",
    )
    parser.add_argument(
        "--flush-cache-between-rounds",
        action="store_true",
        help="Flush the selected worker after each nonfinal barrier round.",
    )
    parser.add_argument(
        "--round-cache-metrics-dir",
        default="",
        help="Save worker metrics at each barrier, before any cache flush.",
    )
    parser.add_argument(
        "--repeat-prompt",
        action="store_true",
        help="Repeat the initial request unchanged; do not append output or questions.",
    )
    parser.add_argument(
        "--exact-chat-length",
        action="store_true",
        help="Prepare model-specific chat prompts of exactly request-length tokens, also for ID mode.",
    )
    parser.add_argument(
        "--prepared-cache-dir",
        default="",
        help="Reuse exact-length chat/ID fixtures from this directory.",
    )
    args = parser.parse_args()
    if args.prepared_cache_dir and not args.exact_chat_length:
        parser.error("--prepared-cache-dir requires --exact-chat-length")
    if args.exact_chat_length and (not args.repeat_prompt or args.range_ratio != 1):
        parser.error("--exact-chat-length requires --repeat-prompt and --range-ratio 1")
    if args.flush_cache_between_rounds or args.round_cache_metrics_dir:
        if not args.enable_round_barrier or not args.round_cache_control_url:
            parser.error(
                "Cache diagnostics require --enable-round-barrier and --round-cache-control-url"
            )
    if not np.isfinite(args.round_gap_s) or args.round_gap_s < 0:
        parser.error("--round-gap-s must be finite and >= 0")
    return args


def log_to_jsonl_file(data, file_path="performance_metrics.jsonl", tag=""):
    """Append the data with a timestamp and tag to the specified JSONL file."""
    timestamped_data = {"timestamp": datetime.now().isoformat(), "tag": tag, **data}
    try:
        with open(file_path, "a") as file:
            file.write(
                json.dumps(timestamped_data) + "\n"
            )  # Write as a single line in JSONL format
    except IOError as e:
        print(f"Error writing to JSONL file: {e}")


class ReadyQueue:
    """
    Thread-safe queue that can pop requests in different orders based on given policy.
    """

    def __init__(self, init_requests=None, policy="random"):
        self.lock = threading.Lock()
        self.requests = init_requests or []
        self.policy = policy

    def append(self, item):
        with self.lock:
            self.requests.append(item)

    def pop(self):
        with self.lock:
            if not self.requests:
                return None
            if self.policy == "random":
                index = random.randrange(len(self.requests))
                return self.requests.pop(index)
            elif self.policy == "fifo":
                return self.requests.pop(0)
            else:
                # todo, varying thinking time of clients
                raise ValueError(f"{self.policy} not implemented")


class WorkloadGenerator:
    def __init__(self, args):
        self.api_format = args.api_format
        self.repeat_prompt = args.repeat_prompt
        self.exact_chat_length = args.exact_chat_length
        self.expected_prompt_length = args.request_length
        self.model_path = args.model_path

        # Construct the base URL and select request/payload functions
        if self.api_format == "openai":
            self.url = f"http://{args.host}:{args.port}/v1/chat/completions"
            self.request_func = async_request_openai_chat_completions
        else:
            self.url = f"http://{args.host}:{args.port}/generate"
            self.request_func = async_request_sglang_generate

        self.tokenizer = get_tokenizer(args.model_path)
        self.distribution = args.distribution
        self.request_rate = args.request_rate
        self.start_time = None
        self.finished_time = None
        self.lora_path = args.lora_path
        self.enable_dp_affinity = args.enable_dp_affinity
        self.dp_size = args.dp_size
        self.disable_heartbeat = args.disable_heartbeat
        self.round_gap_s = args.round_gap_s
        self.round_cache_control_url = args.round_cache_control_url.rstrip("/")
        self.flush_cache_between_rounds = args.flush_cache_between_rounds
        self.round_cache_metrics_dir = args.round_cache_metrics_dir
        self.barrier_error = None
        self.cache_maintenance = []
        if self.enable_dp_affinity:
            if self.dp_size < 1:
                raise ValueError(f"--dp-size must be >= 1, got {self.dp_size}")
            print(
                f"DP affinity on: client i -> routed_dp_rank i%{self.dp_size} "
                f"({args.num_clients} clients, {self.dp_size} ranks)"
            )

        self.sent_requests = 0
        self.completed_requests = 0

        # Resolve per-client round counts
        min_rounds = args.min_rounds
        max_rounds = args.max_rounds
        if min_rounds == 0 and max_rounds == 0:
            # Backward compat: all clients use --num-rounds
            min_rounds = args.num_rounds
            max_rounds = args.num_rounds
        elif min_rounds == 0:
            min_rounds = max_rounds
        elif max_rounds == 0:
            max_rounds = min_rounds
        if min_rounds < 1:
            raise ValueError(f"--min-rounds must be >= 1, got {min_rounds}")
        if min_rounds > max_rounds:
            raise ValueError(
                f"--min-rounds ({min_rounds}) must be <= --max-rounds ({max_rounds})"
            )

        self.min_rounds = min_rounds
        self.max_rounds = max_rounds

        if min_rounds == max_rounds:
            # All clients have the same round count; skip randint to preserve random state
            self.client_total_rounds = [min_rounds] * args.num_clients
        else:
            self.client_total_rounds = [
                random.randint(min_rounds, max_rounds) for _ in range(args.num_clients)
            ]

        # clients_per_round[r] = number of clients participating in round r
        self.clients_per_round = [
            sum(1 for t in self.client_total_rounds if t > r) for r in range(max_rounds)
        ]
        self.total_requests = sum(self.client_total_rounds)

        range_ratio = args.range_ratio

        def sample_inputs():
            # Use return_text=False to get token ids instead of text
            first_round_samples = sample_random_requests(
                input_len=args.request_length,
                output_len=args.output_length,
                num_prompts=args.num_clients,
                range_ratio=range_ratio,
                tokenizer=self.tokenizer,
                dataset_path=args.dataset_path,
                random_sample=not args.disable_random_sample,
                return_text=False,
            )
            # Store per-sample output_len for first round
            first_round_output_lens = [row.output_len for row in first_round_samples]
            # r.prompt is now List[int] when return_text=False
            self.candidate_inputs = [list(i.prompt) for i in first_round_samples]

            return first_round_output_lens

        prepared_messages = None
        if self.exact_chat_length:
            from repeat_prompt import (
                cached_prepare,
                prepare_exact_chat,
                prepared_cache_key,
                resolve_exact_chat_spec,
            )

            chat_spec = resolve_exact_chat_spec(self.model_path, self.tokenizer)

            def prepare():
                sample_inputs()
                prepared = []
                prepare_started = time.perf_counter()
                for i, ids in enumerate(self.candidate_inputs):
                    prepared.append(
                        prepare_exact_chat(
                            self.tokenizer, ids, args.request_length, spec=chat_spec
                        )
                    )
                    if (i + 1) % 10 == 0 or i + 1 == len(self.candidate_inputs):
                        print(
                            f"[PREPARING] {i + 1}/{len(self.candidate_inputs)} prompts, "
                            f"elapsed={time.perf_counter() - prepare_started:.1f}s",
                            flush=True,
                        )
                return prepared

            # Keep scheduling randomness independent of cache hit/miss.
            random_state, numpy_state = random.getstate(), np.random.get_state()
            try:
                prepared = cached_prepare(
                    args.prepared_cache_dir,
                    (
                        prepared_cache_key(args, self.tokenizer, chat_spec)
                        if args.prepared_cache_dir
                        else ""
                    ),
                    args.request_length,
                    args.num_clients,
                    prepare,
                )
            finally:
                random.setstate(random_state)
                np.random.set_state(numpy_state)
            first_round_output_lens = [args.output_length] * args.num_clients
            prepared_messages = {i: item[0] for i, item in enumerate(prepared)}
            self.candidate_inputs = [item[1] for item in prepared]
            print(
                f"[PREPARED] {len(prepared)} chat prompts, encoder={chat_spec or 'hf_template'}, "
                f"exactly {args.request_length} tokens including template"
            )

        else:
            first_round_output_lens = sample_inputs()

        if args.sub_question_input_length != 0:
            sub_question_input_length = args.sub_question_input_length
        else:
            sub_question_input_length = args.request_length

        num_sub_questions = sum(max(t - 1, 0) for t in self.client_total_rounds)

        self.sub_question_inputs = (
            []
            if self.repeat_prompt
            else sample_random_requests(
                input_len=sub_question_input_length,
                output_len=args.output_length,
                num_prompts=max(num_sub_questions, 1),
                range_ratio=range_ratio,
                tokenizer=self.tokenizer,
                dataset_path=args.dataset_path,
                random_sample=not args.disable_random_sample,
                return_text=False,
            )
        )

        if self.api_format == "openai":
            # OpenAI mode: history is a messages list for /v1/chat/completions
            initial_messages = prepared_messages or {
                i: [
                    {
                        "role": "user",
                        "content": self.tokenizer.decode(self.candidate_inputs[i]),
                    }
                ]
                for i in range(args.num_clients)
            }
            init_requests = [
                (
                    i,
                    gen_payload_openai(
                        initial_messages[i],
                        first_round_output_lens[i],
                        self.model_path,
                    ),
                )
                for i in range(args.num_clients)
            ]
            self.client_records = {
                i: {
                    "round": 0,
                    "history": initial_messages[i],
                    "total_rounds": self.client_total_rounds[i],
                }
                for i in range(args.num_clients)
            }
        else:
            # SGLang mode: history is List[int] (token ids)
            init_requests = [
                (
                    i,
                    self._make_payload(
                        self.candidate_inputs[i],
                        first_round_output_lens[i],
                        client_id=i,
                    ),
                )
                for i in range(args.num_clients)
            ]
            self.client_records = {
                i: {
                    "round": 0,
                    "history": list(self.candidate_inputs[i]),
                    "total_rounds": self.client_total_rounds[i],
                }
                for i in range(args.num_clients)
            }
        if (
            self.exact_chat_length
            and self.api_format == "openai"
            and chat_spec in ("dsv4", "dsv32")
        ):
            for _, payload in init_requests:
                payload["chat_template_kwargs"] = {"thinking": False}
        self.initial_payloads = dict(init_requests)
        self.ready_queue = ReadyQueue(
            init_requests=init_requests, policy=args.ready_queue_policy
        )
        self.candidate_inputs = self.candidate_inputs[args.num_clients :]

        self.response_queue = queue.Queue()
        self.pbar = tqdm(total=self.total_requests)
        self.performance_metrics = {
            "ttft": [],
            "itl": [],
            "latency": [],
            "prompt_len": [],
            "cached_tokens": [],
            "generated_len": [],
        }
        self.enable_round_barrier = args.enable_round_barrier
        if self.enable_round_barrier:
            # Add round-specific metrics while preserving the original structure
            for i in range(self.max_rounds):
                self.performance_metrics[f"round_{i}"] = {
                    "request_start": [],
                    "request_end": [],
                    "ttft": [],
                    "latency": [],
                    "prompt_len": [],
                    "cached_tokens": [],
                    "generated_len": [],
                }
        self.num_clients = args.num_clients

        self.num_rounds = self.max_rounds
        self.max_parallel = args.max_parallel
        self.output_length = args.output_length

    def _dp_rank_for_client(self, client_id):
        if not self.enable_dp_affinity:
            return None
        return client_id % self.dp_size

    def _make_payload(self, input_ids, output_len, client_id=None):
        payload = gen_payload(input_ids, output_len, self.lora_path)
        rank = self._dp_rank_for_client(client_id) if client_id is not None else None
        if rank is not None:
            payload["routed_dp_rank"] = rank
        return payload

    async def handle_request(self, item):
        client_id, payload = item
        try:
            # Apply affinity at the shared send boundary for both API formats.
            rank = self._dp_rank_for_client(client_id)
            if rank is not None:
                payload["routed_dp_rank"] = rank
            request_start = time.perf_counter()
            response = await self.request_func(payload, self.url, self.pbar)
            response.benchmark_request_start = request_start
            response.benchmark_request_end = time.perf_counter()
            if self.pbar.n == self.pbar.total:
                self.finished_time = time.perf_counter()
            self.response_queue.put((client_id, response))
        except Exception as e:
            print(f"Request failed for client {client_id}: {e}")
            failed_response = RequestFuncOutput()
            failed_response.success = False
            failed_response.error = str(e)
            self.response_queue.put((client_id, failed_response))

    def request_sender(self):
        async def request_loop():
            while True:
                if self.barrier_error is not None:
                    break
                if self.sent_requests - self.completed_requests < self.max_parallel:
                    new_request = self.ready_queue.pop()
                    if new_request:
                        asyncio.create_task(self.handle_request(new_request))
                        self.sent_requests += 1
                else:
                    await asyncio.sleep(0.05)
                    continue

                if self.pbar.n == self.pbar.total:
                    break

                # Calculate Poisson-distributed wait time
                if self.distribution == "poisson":
                    sleep_time = random.expovariate(self.request_rate)
                elif self.distribution == "uniform":
                    avg_interval = (
                        1.0 / self.request_rate if self.request_rate > 0 else 1.0
                    )
                    sleep_time = random.uniform(0, 2 * avg_interval)
                else:
                    raise ValueError("Invalid distribution type")
                await asyncio.sleep(sleep_time)  # Wait before sending the next request

        # Create and run the event loop for asynchronous requests
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(request_loop())
        loop.close()

    def response_handler(self):
        next_round_reqs = []
        current_barrier_round = 0
        barrier_round_completed = 0
        while True:
            try:
                client_id, response = self.response_queue.get(
                    timeout=10
                )  # Block until response is available
                if not response.success:
                    print(f"Request failed for client {client_id}: {response.error}")
                    self.completed_requests += 1
                    if self.repeat_prompt:
                        self.barrier_error = RuntimeError(response.error)
                        return
                    continue
                if (
                    self.exact_chat_length
                    and response.prompt_len != self.expected_prompt_length
                ):
                    self.barrier_error = RuntimeError(
                        f"Client {client_id}: server prompt_tokens={response.prompt_len}, "
                        f"expected {self.expected_prompt_length}; check tokenizer/template versions."
                    )
                    return
                # Repeated requests must not incorporate generated output.
                if self.repeat_prompt:
                    pass
                elif self.api_format == "openai":
                    if response.generated_text:
                        self.client_records[client_id]["history"].append(
                            {"role": "assistant", "content": response.generated_text}
                        )
                else:
                    self.client_records[client_id]["history"].extend(
                        response.output_ids
                    )
                current_round = self.client_records[client_id]["round"]
                self.client_records[client_id]["round"] += 1
                self.performance_metrics["ttft"].append(response.ttft)
                self.performance_metrics["itl"].extend(response.itl)
                self.performance_metrics["latency"].append(response.latency)
                self.performance_metrics["prompt_len"].append(response.prompt_len)
                self.performance_metrics["cached_tokens"].append(response.cached_tokens)
                self.performance_metrics["generated_len"].append(response.generated_len)
                if self.enable_round_barrier:
                    self.performance_metrics[f"round_{current_round}"][
                        "request_start"
                    ].append(response.benchmark_request_start)
                    self.performance_metrics[f"round_{current_round}"][
                        "request_end"
                    ].append(response.benchmark_request_end)
                    self.performance_metrics[f"round_{current_round}"]["ttft"].append(
                        response.ttft
                    )
                    self.performance_metrics[f"round_{current_round}"][
                        "latency"
                    ].append(response.latency)
                    self.performance_metrics[f"round_{current_round}"][
                        "prompt_len"
                    ].append(response.prompt_len)
                    self.performance_metrics[f"round_{current_round}"][
                        "cached_tokens"
                    ].append(response.cached_tokens)
                    self.performance_metrics[f"round_{current_round}"][
                        "generated_len"
                    ].append(response.generated_len)
                self.completed_requests += 1

                client_total = self.client_records[client_id]["total_rounds"]
                if self.client_records[client_id]["round"] < client_total:
                    if self.repeat_prompt:
                        new_req = (client_id, self.initial_payloads[client_id])
                    else:
                        sub_q = self.sub_question_inputs.pop()
                        if self.api_format == "openai":
                            # Append sub-question as a new user message
                            sub_q_text = self.tokenizer.decode(list(sub_q.prompt))
                            self.client_records[client_id]["history"].append(
                                {"role": "user", "content": sub_q_text}
                            )
                            new_req = (
                                client_id,
                                gen_payload_openai(
                                    self.client_records[client_id]["history"],
                                    sub_q.output_len,
                                    self.model_path,
                                ),
                            )
                        else:
                            # Append sub-question token ids to client's history
                            sub_q_ids = list(sub_q.prompt)
                            self.client_records[client_id]["history"].extend(sub_q_ids)
                            new_req = (
                                client_id,
                                self._make_payload(
                                    self.client_records[client_id]["history"],
                                    sub_q.output_len,
                                    client_id=client_id,
                                ),
                            )
                    if self.enable_round_barrier:
                        next_round_reqs.append(new_req)
                    else:
                        self.ready_queue.append(new_req)

                # Barrier logic: release next round when all clients for
                # current barrier round have completed
                if (
                    self.enable_round_barrier
                    and current_barrier_round < self.max_rounds
                ):
                    barrier_round_completed += 1
                    expected = self.clients_per_round[current_barrier_round]
                    if barrier_round_completed == expected:
                        print(
                            f"\n  Barrier: round {current_barrier_round} complete "
                            f"({expected} clients), releasing {len(next_round_reqs)} "
                            f"requests for round {current_barrier_round + 1}"
                        )
                        try:
                            self._round_cache_control(
                                current_barrier_round, bool(next_round_reqs)
                            )
                        except Exception as error:
                            self.barrier_error = error
                            return
                        if not self.disable_heartbeat:
                            self._send_heartbeat(input_len=100, output_len=100)
                        time.sleep(self.round_gap_s)
                        for req in next_round_reqs:
                            self.ready_queue.append(req)
                        next_round_reqs = []
                        current_barrier_round += 1
                        barrier_round_completed = 0
            except queue.Empty:
                if self.pbar.n == self.pbar.total:
                    break
            except ValueError as e:
                print(f"Error processing response for client {client_id}: {e}")
                continue

    def _round_cache_control(self, round_id, has_next_round):
        if not (self.round_cache_metrics_dir or self.flush_cache_between_rounds):
            return
        started = time.perf_counter()
        if self.round_cache_metrics_dir:
            response = requests.get(
                self.round_cache_control_url + "/metrics", timeout=30
            )
            response.raise_for_status()
            directory = Path(self.round_cache_metrics_dir)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"p_round_{round_id}.prom").write_text(response.text)
        flushed = self.flush_cache_between_rounds and has_next_round
        if flushed:
            response = requests.post(
                self.round_cache_control_url + "/flush_cache",
                params={"timeout": 120},
                timeout=150,
            )
            response.raise_for_status()
        elapsed = time.perf_counter() - started
        self.cache_maintenance.append(
            {"round": round_id, "flushed": flushed, "seconds": elapsed}
        )
        print(
            f"Cache control: round={round_id} flushed={flushed} elapsed={elapsed:.6f}s",
            flush=True,
        )

    def _send_heartbeat(self, input_len=100, output_len=20):
        """Send a small heartbeat request to the server."""
        heartbeat_input = [1] * input_len
        payload = self._make_payload(heartbeat_input, output_len, client_id=0)
        try:
            requests.post(self.url, json=payload, timeout=30)
        except Exception as e:
            print(f"Heartbeat request failed: {e}")

    def run(self):
        request_thread = threading.Thread(target=self.request_sender, daemon=True)
        response_thread = threading.Thread(target=self.response_handler, daemon=True)

        self.start_time = time.perf_counter()
        request_thread.start()
        response_thread.start()

        request_thread.join()
        response_thread.join()
        self.pbar.close()
        if self.barrier_error is not None:
            raise RuntimeError(
                "Benchmark request validation or between-round cache control failed"
            ) from self.barrier_error

        duration = self.finished_time - self.start_time
        sorted_ttft = sorted(self.performance_metrics["ttft"])
        sorted_latency = sorted(self.performance_metrics["latency"])
        sorted_itl = sorted(self.performance_metrics["itl"])
        sorted_prompt_len = sorted(self.performance_metrics["prompt_len"])
        sorted_output_len = sorted(self.performance_metrics["generated_len"])

        def percentile(sorted_vals, q):
            if not sorted_vals:
                return 0.0
            idx = int(q * len(sorted_vals))
            if idx >= len(sorted_vals):
                idx = len(sorted_vals) - 1
            return sorted_vals[idx]

        def max_or_zero(sorted_vals):
            return sorted_vals[-1] if sorted_vals else 0.0

        performance_data = {
            "summary": {
                "total_requests": len(self.performance_metrics["ttft"]),
                "request_rate": self.request_rate,
                "average_prompt_len": (
                    sum(self.performance_metrics["prompt_len"])
                    / len(self.performance_metrics["prompt_len"])
                    if self.performance_metrics["prompt_len"]
                    else 0.0
                ),
                "average_output_len": (
                    sum(self.performance_metrics["generated_len"])
                    / len(self.performance_metrics["generated_len"])
                    if self.performance_metrics["generated_len"]
                    else 0.0
                ),
                "p90_prompt_len": percentile(sorted_prompt_len, 0.9),
                "p99_prompt_len": percentile(sorted_prompt_len, 0.99),
                "p90_output_len": percentile(sorted_output_len, 0.9),
                "p99_output_len": percentile(sorted_output_len, 0.99),
                "average_ttft": sum(self.performance_metrics["ttft"])
                / len(self.performance_metrics["ttft"]),
                "p90_ttft": percentile(sorted_ttft, 0.9),
                "p99_ttft": percentile(sorted_ttft, 0.99),
                "median_ttft": percentile(sorted_ttft, 0.5),
                "max_ttft": max_or_zero(sorted_ttft),
                "average_itl": (
                    sum(self.performance_metrics["itl"])
                    / len(self.performance_metrics["itl"])
                    if self.performance_metrics["itl"]
                    else 0.0
                ),
                "p90_itl": percentile(sorted_itl, 0.9),
                "p99_itl": percentile(sorted_itl, 0.99),
                "median_itl": percentile(sorted_itl, 0.5),
                "max_itl": max_or_zero(sorted_itl),
                "average_latency": sum(self.performance_metrics["latency"])
                / len(self.performance_metrics["latency"]),
                "p90_latency": percentile(sorted_latency, 0.9),
                "p99_latency": percentile(sorted_latency, 0.99),
                "median_latency": percentile(sorted_latency, 0.5),
                "max_latency": max_or_zero(sorted_latency),
                "input_token_throughput": sum(self.performance_metrics["prompt_len"])
                / duration,
                "output_token_throughput": sum(
                    self.performance_metrics["generated_len"]
                )
                / duration,
                "throughput": self.pbar.total / duration,
                "cache_hit_rate": (
                    0
                    if sum(self.performance_metrics["prompt_len"]) == 0
                    else sum(self.performance_metrics["cached_tokens"])
                    / sum(self.performance_metrics["prompt_len"])
                ),
                "dp_affinity": self.enable_dp_affinity,
                "dp_size": self.dp_size,
                "heartbeat_disabled": self.disable_heartbeat,
                "round_gap_s": self.round_gap_s,
                "flush_cache_between_rounds": self.flush_cache_between_rounds,
                "round_cache_control_url": self.round_cache_control_url,
                "cache_maintenance": self.cache_maintenance,
            },
        }
        if self.enable_round_barrier:
            performance_data["round"] = {}
            for round_num in range(self.num_rounds):
                round_key = f"round_{round_num}"
                round_metrics = self.performance_metrics[round_key]
                # Measure the whole round, including overlap between requests.
                # Exclude heartbeat and sleep between rounds. Input tokens include
                # cached prefixes, matching the overall input throughput metric.
                round_duration = (
                    max(round_metrics["request_end"])
                    - min(round_metrics["request_start"])
                    if round_metrics["request_start"]
                    else 0.0
                )
                performance_data["round"][round_key] = {
                    "duration": round_duration,
                    "input_token_throughput": (
                        sum(round_metrics["prompt_len"]) / round_duration
                        if round_duration > 0
                        else 0.0
                    ),
                    "average_ttft": (
                        sum(round_metrics["ttft"]) / len(round_metrics["ttft"])
                        if round_metrics["ttft"]
                        else 0
                    ),
                    "cache_hit_rate": (
                        0
                        if sum(round_metrics["prompt_len"]) == 0
                        else sum(round_metrics["cached_tokens"])
                        / sum(round_metrics["prompt_len"])
                    ),
                    "request_count": len(round_metrics["ttft"]),
                    "prompt_tokens_min": min(round_metrics["prompt_len"], default=0),
                    "prompt_tokens_max": max(round_metrics["prompt_len"], default=0),
                    "cached_tokens_min": min(round_metrics["cached_tokens"], default=0),
                    "cached_tokens_max": max(round_metrics["cached_tokens"], default=0),
                }
        print("All requests completed")
        print("Performance metrics summary:")
        print(
            f"  Total requests: {performance_data['summary']['total_requests']} at {performance_data['summary']['request_rate']} requests per second"
        )
        print(
            f"  Average Prompt Length: {performance_data['summary']['average_prompt_len']:.2f} tokens"
        )
        print(
            f"  Average Output Length: {performance_data['summary']['average_output_len']:.2f} tokens"
        )
        print(
            f"  P90 Prompt Length: {performance_data['summary']['p90_prompt_len']:.0f} tokens"
        )
        print(
            f"  P99 Prompt Length: {performance_data['summary']['p99_prompt_len']:.0f} tokens"
        )
        print(
            f"  P90 Output Length: {performance_data['summary']['p90_output_len']:.0f} tokens"
        )
        print(
            f"  P99 Output Length: {performance_data['summary']['p99_output_len']:.0f} tokens"
        )
        print(f"  Average TTFT: {performance_data['summary']['average_ttft']:.2f}")
        print(f"  P90 TTFT: {performance_data['summary']['p90_ttft']:.2f}")
        print(f"  P99 TTFT: {performance_data['summary']['p99_ttft']:.2f}")
        print(f"  Median TTFT: {performance_data['summary']['median_ttft']:.2f}")
        print(f"  Max TTFT: {performance_data['summary']['max_ttft']:.2f}")
        print(f"  Average ITL: {performance_data['summary']['average_itl']:.4f}")
        print(f"  P90 ITL: {performance_data['summary']['p90_itl']:.4f}")
        print(f"  P99 ITL: {performance_data['summary']['p99_itl']:.4f}")
        print(f"  Median ITL: {performance_data['summary']['median_itl']:.4f}")
        print(f"  Max ITL: {performance_data['summary']['max_itl']:.4f}")
        print(
            f"  Average latency: {performance_data['summary']['average_latency']:.2f}"
        )
        print(f"  P90 latency: {performance_data['summary']['p90_latency']:.2f}")
        print(f"  P99 latency: {performance_data['summary']['p99_latency']:.2f}")
        print(f"  Median latency: {performance_data['summary']['median_latency']:.2f}")
        print(f"  Max latency: {performance_data['summary']['max_latency']:.2f}")
        print(
            f"  Input token throughput: {performance_data['summary']['input_token_throughput']:.2f} tokens per second"
        )
        print(
            f"  Output token throughput: {performance_data['summary']['output_token_throughput']:.2f} tokens per second"
        )
        print(
            f"  Request Throughput: {performance_data['summary']['throughput']:.2f} requests per second"
        )
        print(f"  Cache Hit Rate: {performance_data['summary']['cache_hit_rate']:.6f}")

        if self.enable_round_barrier:
            # Print round-basedsummary
            print("Per-round metrics:")
            if "round" in performance_data:
                for round_num in range(self.num_rounds):
                    round_key = f"round_{round_num}"
                    if round_key in performance_data["round"]:
                        round_data = performance_data["round"][round_key]
                        avg_ttft = round_data["average_ttft"]
                        cache_hit_rate = round_data["cache_hit_rate"]
                        request_count = round_data["request_count"]
                        clients_in_round = self.clients_per_round[round_num]
                        print(
                            f"  Round {round_num}: Average TTFT = {avg_ttft:.2f}s, "
                            f"Cache Hit Rate = {cache_hit_rate:.6f}, "
                            f"Input Throughput = {round_data['input_token_throughput']:.2f} tok/s, "
                            f"Duration = {round_data['duration']:.3f}s "
                            f"({request_count} requests, "
                            f"{clients_in_round} clients)"
                        )
                        if self.repeat_prompt:
                            print(
                                f"    Prompt tokens: {round_data['prompt_tokens_min']}..{round_data['prompt_tokens_max']}; "
                                f"cached tokens: {round_data['cached_tokens_min']}..{round_data['cached_tokens_max']}"
                            )
                    else:
                        print(f"  Round {round_num}: No requests completed")

        return performance_data


if __name__ == "__main__":
    args = parse_args()
    flush_cache_url = f"http://{args.host}:{args.port}/flush_cache"

    random.seed(args.seed)
    np.random.seed(args.seed)

    if args.disable_auto_run:
        print("Running with specified request rate...")
        request_rates = [args.request_rate]
    else:
        print("Auto-running with different request rates...")
        request_rates = [16, 14, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]

    for rate in request_rates:
        args.request_rate = rate
        requests.post(flush_cache_url)
        time.sleep(1)
        performance_data = WorkloadGenerator(args).run()
        log_to_jsonl_file(performance_data, args.log_file, tag=args.tag)
