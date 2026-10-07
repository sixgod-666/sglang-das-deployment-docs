# Public, reviewed benchmark-tool copy for the SGLang DAS documentation site.
# Connects to an already deployed OpenAI-compatible service; it does not deploy one.
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# Adapted from
# https://github.com/vllm-project/vllm/blob/main/benchmarks/benchmark_long_document_qa_throughput.py

"""
Commandline arguments:
    --num-documents: The number of documents to sample prompts from.

    --document-length: The length of each document in tokens (random text:
                       +- --text-length-tolerance; a chat request adds 4
                       template tokens; 'hi' documents also carry their
                       '<i> ' prefix). Exception: with --hit-miss-ratio <h>
                       (total-length partial hit) it is the query length.
                       (Optional, default: 20000)

    --output-len: The number of tokens to generate for each prompt.
                  (Optional, default: 100)

    --repeat-count: The number of times to repeat each prompt.
                    (Optional, default: 2)

    --repeat-mode: The mode to repeat prompts. The supported modes are:
        - 'random': shuffle the prompts randomly. (Default)
        - 'tile': the entire prompt list is repeated in sequence.
        - 'interleave': each prompt is repeated consecutively before
                        moving to the next element.

    --shuffle-seed: Random seed when the repeat mode is "random".
                    (Optional, default: 0)

    --host: Host to query the vLLM server
    --port: Port to query the vLLM server
    --base-url: Base URL to query the LLM server (exclusive with --host/--port)

    --model: Model name

    --max-inflight-requests: Maximum number of in-flight requests. Default is 2

    --sleep-time-after-warmup: Sleep time after warm up iteration.
                              (Optional, default: 0.0 seconds)

    --output: Filename to write all responses to. If omitted, writes to stdout.

    --completions: Use completions API instead of chat completions API

    --visualize: Visualize the results

    --eos-token-id: EOS token id. we bias against this token id so we always
                   get the number of output tokens we specify

    --text-source: 'random' (default): documents are random token id runs
                  decoded with the model tokenizer (--tokenizer) into text,
                  the same kind of synthetic text as bench_multiturn.py
                  --api-format openai, calibrated to --document-length tokens.
                  'hi': the original '<i> hi hi ...' documents (one repeated
                  token; skews DeepSeek-V4 MoE routing, kept for comparison).

    --token-id-pattern: how the random ids are drawn before decoding:
                  'sequential' (default) cyclic runs (start + j) % vocab_size
                  from de-duplicated random starts (same kind of content as
                  bench_multiturn.py --disable-random-sample, not an
                  id-for-id replay); 'random' each id uniformly;
                  'repeat' unique first id + --repeat-token-id repeated.

    --hit-miss-ratio: controls cache hits in the query round.
        - 'hit:miss', e.g. '99:1' (request level, original behavior): in every
          period of (hit+miss) requests the first `hit` repeat their warmup
          document unchanged (designed full hit) and the last `miss` get a
          random prefix (full miss).
        Per-request partial hits (random text only; every query is the
        two-turn chat [user: its document, assistant: "OK", user: new text];
        the single-turn warmup request is constructed as an exact token
        prefix of it with DeepSeek-V4 chat rendering, thinking off, so the
        designed hit is the whole warmup request rounded down to a page):
        - '<h>', e.g. '0.99' (total length kept): query = --document-length
          (T) tokens exactly; the warmup request is exactly L =
          floor(T*h/page)*page tokens (document L-4 + template), page
          aligned (h is taken as the exact decimal typed); designed hit L/T
          (e.g. 65536 + 0.99: warmup 64768, 768 new per query, 0.9883).
          Warmup requests are shorter than T.
        - 'append:<h>', e.g. 'append:0.99' (original request kept, shaped
          like a multi-turn chat round): the warmup request is the document
          as usual (W = --document-length + 4) and the query appends so that
          query ~ W / h (e.g. 65536 + append:0.99: warmup 65540, query
          ~66202, designed hit ~0.9899).
        Printed / JSON: the nominal designed hit and the designed hit of the
        requests actually built (lengths have a tolerance):
        sum(floor(W_i / page) * page) / sum(query_i). Query plans above
        1048576 tokens are rejected. Whether the server actually restores
        the prefix (cache residency, offload finished, SWA state saved at the
        request end) must be checked from the actual hit.
        - not given: the query round repeats the warmup requests.

    Actual cache hit: read from usage.prompt_tokens_details.cached_tokens
    (server flag --enable-cache-report). sglang omits the details when a
    request cached 0 tokens, so if any response of the run (pre-warmup
    included) carries them, missing ones count as 0. This assumes a single
    server whose configuration does not change during the run. If no
    response carries them, the hit is reported unknown with a WARNING.
"""

# Standard
from dataclasses import dataclass
import argparse
import asyncio
from fractions import Fraction
import json
import math
import os
import random
import sys
import time

# Third Party
from openai import AsyncOpenAI
import pandas as pd

# Global output filename (set in __main__)
OUTPUT_FILE = None
completions_mode = False
visualize = False
eos_token_id = None
vocab_size = 128000
chat_thinking_off = False
APPEND_ASSISTANT_REPLY = "OK"
# DeepSeek-V4 chat rendering (sglang encoding_dsv4, thinking off):
#   single turn : BOS <User> doc <Assistant> </think>            -> 4 tokens
#   append query: ... </think> OK EOS <User> tail <Assistant> </think>
#                 -> EOS, <User>, <Assistant>, </think> = 4 beyond reply+tail
CHAT_TEMPLATE_TOKENS = 4
APPEND_OVERHEAD_TOKENS = 4
APPEND_REPLY_TOKENS = 1  # "OK" is one DeepSeek-V4 token (11932)
TEXT_DOC_MAX_ATTEMPTS = 50
# partial-hit queries above this are rejected (DeepSeek-V4 context)
MAX_QUERY_TOKENS = 1048576


@dataclass
class RequestStats:
    prompt_id: int
    request_start: float
    ttft: float
    request_end: float
    successful: bool
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # actual server-side prefix cache hit (-1 = not reported by the server)
    cached_tokens: int = -1
    error: str = ""


def get_url_from_args(args):
    """
    Get the base URL from command line arguments.
    Args:
        args: Command line arguments.
    Returns:
        str: The base URL.
    """
    if args.base_url is not None:
        return args.base_url
    else:
        host = args.host if args.host is not None else "localhost"
        port = args.port if args.port is not None else 8000
        return f"http://{host}:{port}/v1"


def extract_reasoning_content(chunk):
    """
    Extract reasoning content from the response chunk.
    Args:
        chunk: The response chunk from OpenAI Chat Completions API.
    Returns:
        str | None: The reasoning content extracted from the chunk.
            None means no reasoning content in this chunk.
    """
    delta = chunk.choices[0].delta
    potential_reasoning_keys = [
        "reasoning_content",
        "reasoning",
        "tool_calls",
        "tool_call",
        "tool_responses",
    ]
    for key in potential_reasoning_keys:
        if hasattr(delta, key) and getattr(delta, key):
            return getattr(delta, key)
    return None


def extract_normal_content(chunk):
    """
    Extract normal content from the response chunk.
    Args:
        chunk: The response chunk from OpenAI Chat Completions API.
    Returns:
        str | None: The normal content extracted from the chunk.
            None means no normal content in this chunk.
    """
    delta = chunk.choices[0].delta
    if hasattr(delta, "content") and delta.content:
        return chunk.choices[0].delta.content
    return None


def has_content_completions(chunk):
    """
    Completions streaming emits text at choices[0].text.
    """
    return bool(chunk.choices) and (chunk.choices[0].text is not None)


def has_content(chunk, completions_mode=False):
    """
    Check if the chunk has content in the choices.
    Args:
        chunk: The response chunk from OpenAI Chat Completions API.

    Returns:
        bool: True if content exists, False otherwise.
    """
    if completions_mode:
        return has_content_completions(chunk)

    return (
        chunk.choices
        and chunk.choices[0].delta
        and (
            extract_normal_content(chunk) is not None
            or extract_reasoning_content(chunk) is not None
        )
    )


def extract_content_completions(chunk):
    """
    Extract content from a Completions stream chunk.
    """
    return chunk.choices[0].text or ""


def extract_content(chunk, completions_mode=False):
    """
    Extract content from the response chunk.
    Args:
        chunk: The response chunk from OpenAI Chat Completions API.
    Returns:
        str: The content extracted from the chunk.
    """
    if completions_mode:
        return extract_content_completions(chunk)

    if normal_content := extract_normal_content(chunk):
        return normal_content
    elif reasoning_content := extract_reasoning_content(chunk):
        return reasoning_content
    else:
        return ""


def write_resp(text: str):
    """
    Write text to the specified output file (if any), otherwise to stdout.
    """
    if OUTPUT_FILE:
        with open(OUTPUT_FILE, "a") as resp_file:
            resp_file.write(text)
    else:
        sys.stdout.write(text)


async def process_single_prompt(
    client, model, prompt, prompt_index, total_prompts, output_len, semaphore
) -> RequestStats:
    """
    Process a single prompt with the given client and model.

    Args:
        client: The OpenAI client for making API calls.
        model: The model name to use for generation.
        prompt: A document string, or a chat message list (partial-hit query).
        prompt_index: Index of the current prompt (0-based).
        total_prompts: Total number of prompts being processed.
        output_len: The maximum number of tokens to generate.
        semaphore: Asyncio semaphore to limit concurrent requests.

    Returns:
        RequestStats: RequestStats object containing the request stats
    """
    async with semaphore:  # Acquire semaphore to limit concurrent requests
        write_resp(f"\n--- Sending prompt {prompt_index + 1}/{total_prompts} ---\n")
        # a request starts once it acquires the semaphore
        start_time = time.time()
        first_token_time = None

        # add stop None so we always get the number of output tokens we specify
        if completions_mode:
            response = await client.completions.create(
                model=model,
                prompt=prompt,
                stream=True,
                max_tokens=output_len,
                temperature=0.0,
                stream_options={"include_usage": True},
                logit_bias={str(eos_token_id): -100}
                if eos_token_id is not None
                else None,
            )
        else:
            messages = (
                prompt
                if isinstance(prompt, list)
                else [{"role": "user", "content": prompt}]
            )
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                max_tokens=output_len,
                temperature=0.0,
                stream_options={"include_usage": True},
                logit_bias={str(eos_token_id): -100}
                if eos_token_id is not None
                else None,
                # DeepSeek-V4 thinking mode renders the first turn differently
                # once a later user turn exists, breaking the prefix property
                extra_body={"chat_template_kwargs": {"thinking": False}}
                if chat_thinking_off
                else None,
            )

        responses = []
        prompt_tokens = 0
        completion_tokens = 0
        cached_tokens = -1
        got_usage = False
        finish_reason = None
        # Collect the response chunks
        async for chunk in response:
            usage = getattr(chunk, "usage", None)
            if usage:
                got_usage = True
                prompt_tokens = usage.prompt_tokens or 0
                completion_tokens = usage.completion_tokens or 0
                # sglang omits details when nothing was cached (or when
                # --enable-cache-report is off); -1 = not reported
                details = getattr(usage, "prompt_tokens_details", None)
                if details is not None and getattr(details, "cached_tokens", None) is not None:
                    cached_tokens = details.cached_tokens
            if not chunk.choices:
                continue
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason

            # Handle content for chat completions
            if has_content(chunk, completions_mode):
                content = extract_content(chunk, completions_mode)
                if first_token_time is None and content != "":
                    first_token_time = time.time()
                responses.append(content)

        end_time = time.time()
        final_response = "".join(responses)

        # 成功判据: 有 usage、正常结束(stop/length)、输入非空、至少一个输出
        error = ""
        if not got_usage:
            error = "missing usage"
        elif finish_reason not in ("stop", "length"):
            error = f"finish_reason {finish_reason}"
        elif prompt_tokens <= 0:
            error = "prompt_tokens not reported"
        elif completion_tokens < 1:
            error = f"completion_tokens {completion_tokens}"
        if error:
            write_resp(f"\nRequest {prompt_index} failed: {error}\n")
        write_resp(f"\nResponse of request {prompt_index}: {final_response}\n")

        # TTFT < 0 means not successful
        ttft = (first_token_time - start_time) if first_token_time is not None else -1
        if error:
            ttft = -1
        return RequestStats(
            prompt_id=prompt_index,
            request_start=start_time,
            ttft=ttft,
            request_end=end_time,
            successful=ttft > 0,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            error=error,
        )


async def test_long_document_qa(
    client, model, prompts=None, output_len=100, max_inflight_requests=10
) -> list[RequestStats]:
    """
    Test long document QA with the given prompts and sampling parameters.
    Process prompts concurrently with a limit on inflight requests.

    Args:
        client: The OpenAI client for making API calls.
        model: The model name to use for generation.
        prompts: A list of prompts to be processed by the LLM.
        output_len: The maximum number of tokens to generate.
        max_inflight_requests: Maximum number of concurrent requests.

    Returns:
        list: request_stats - a list of RequestStats objects
    """
    # Create semaphore to limit concurrent requests
    semaphore = asyncio.Semaphore(max_inflight_requests)

    # Create tasks for all prompts
    tasks = [
        process_single_prompt(
            client=client,
            model=model,
            prompt=prompt,
            prompt_index=i,
            total_prompts=len(prompts),
            output_len=output_len,
            semaphore=semaphore,
        )
        for i, prompt in enumerate(prompts)
    ]
    # Execute all tasks concurrently and collect results
    # The semaphore will control max concurrent requests
    request_stats = await asyncio.gather(*tasks)

    return request_stats


def repeat_prompts(prompts, repeat_count, mode: str):
    """
    Repeat each prompt in the list for a specified number of times.
    The order of prompts in the output list depends on the mode.

    Args:
        prompts: A list of prompts to be repeated.
        repeat_count: The number of times each prompt is repeated.
        mode: The mode of repetition. Supported modes are:
            - 'random': Shuffle the prompts randomly after repetition.
            - 'tile': Repeat the entire prompt list in sequence.
              Example: [1, 2, 3] -> [1, 2, 3, 1, 2, 3].
            - 'interleave': Repeat each prompt consecutively before moving to
              the next. Example: [1, 2, 3] -> [1, 1, 2, 2, 3, 3].

    Returns:
        A list of repeated prompts in the specified order.

    Raises:
        ValueError: If an invalid mode is provided.
    """
    write_resp(f"Repeat mode:  {mode}\n")
    if mode == "random":
        repeated_prompts = prompts * repeat_count
        random.shuffle(repeated_prompts)
        return repeated_prompts
    elif mode == "tile":
        return prompts * repeat_count
    elif mode == "interleave":
        repeated_prompts = []
        for prompt in prompts:
            repeated_prompts.extend([prompt] * repeat_count)
        return repeated_prompts
    else:
        raise ValueError(
            f"Invalid mode: {mode}, only support 'random', 'tile', 'interleave'"
        )


def add_cache_misses(prompts, hit_miss_ratio):
    """
    Request-level misses, 'hit:miss' style (periodic): in every period of
    hit+miss requests the last `miss` get a random prefix -> full miss.
    Keeps the original vLLM behavior. Per-request partial hits ('<h>') are
    built by
    add_appended_misses instead: a token inserted mid-document can only be
    restored back to an earlier cached boundary on DeepSeek-V4 (SWA state).
    """
    if hit_miss_ratio is None:
        return prompts, [False] * len(prompts)

    hit, miss = map(int, hit_miss_ratio.split(":", 1))
    period = hit + miss
    miss_mask = [False] * len(prompts)

    for i in range(len(prompts)):
        if period and (i % period) >= hit:
            miss_mask[i] = True
            prompts[i] = f"{random.randint(-10_000_000, 10_000_000)} {prompts[i]}"

    return prompts, miss_mask


def parse_partial_ratio(hit_miss_ratio):
    """
    Per-request partial hit:
      '0.8'        -> ("total", Fraction(4, 5)): query length = --document-length
      'append:0.8' -> ("append", Fraction(4, 5)): warmup = the document
    None for 'hit:miss' or no ratio. The ratio is kept as the exact decimal
    the user typed (Fraction), so page boundaries are not lost to binary
    floating point (e.g. 12800 * 0.58 = 7424, not 7423.999...).
    """
    if hit_miss_ratio is None:
        return None
    mode, value = "total", hit_miss_ratio
    if hit_miss_ratio.startswith("append:"):
        mode, value = "append", hit_miss_ratio[len("append:"):]
    elif ":" in hit_miss_ratio:
        return None
    try:
        ratio = Fraction(value.strip())
    except (ValueError, ZeroDivisionError):
        raise ValueError(
            f"--hit-miss-ratio must be 'hit:miss', a ratio in (0, 1) or "
            f"'append:<ratio>', got {hit_miss_ratio!r}"
        ) from None
    if not 0 < ratio < 1:
        raise ValueError(f"partial hit ratio must be in (0, 1), got {value}")
    return mode, ratio


def partial_total_plan(total_length, hit_ratio, page_size, tolerance=0):
    """
    '<h>' sizing, total length kept: every query is exactly --document-length
    (T) tokens and the warmup request is exactly L = floor(T*h/page)*page
    (document + chat template), so the hit boundary is page aligned. Both
    the document and the appended text are generated with exact lengths.
    Returns (warmup request tokens, new text tokens per query, query tokens,
    designed hit).
    """
    if total_length > MAX_QUERY_TOKENS:
        raise ValueError(
            f"--document-length {total_length} is above the "
            f"{MAX_QUERY_TOKENS}-token query limit"
        )
    warmup = int(total_length * hit_ratio) // page_size * page_size
    tail = total_length - warmup - APPEND_OVERHEAD_TOKENS - APPEND_REPLY_TOKENS
    if warmup - CHAT_TEMPLATE_TOKENS < 1 or tail < 1:
        raise ValueError(
            f"--hit-miss-ratio {float(hit_ratio)} with --document-length "
            f"{total_length} leaves no room for the document or the appended "
            f"text (page-aligned warmup {warmup})"
        )
    return warmup, tail, total_length, warmup / total_length


def partial_append_plan(document_length, hit_ratio, page_size, tolerance=0):
    """
    'append:<h>' sizing, multi-turn style: the warmup request W = document +
    chat template stays as is and the query appends so that W is a fraction
    h of it. Returns (warmup request tokens, new text tokens per query, query
    tokens, designed hit ratio with the restorable prefix rounded down to a
    page).
    """
    warmup = document_length + CHAT_TEMPLATE_TOKENS
    planned = Fraction(warmup) / hit_ratio
    # checked before any text is built: a tiny h would make huge queries;
    # the document and the appended text may each be `tolerance` longer
    if planned + 2 * tolerance > MAX_QUERY_TOKENS:
        shown = f"{int(planned)}" if planned < 10**15 else "more than 1e15"
        raise ValueError(
            f"--hit-miss-ratio append:{float(hit_ratio):g} with --document-length "
            f"{document_length} plans a ~{shown}-token query; with "
            f"the +-{tolerance} length tolerance of the document and the "
            f"appended text it may exceed the {MAX_QUERY_TOKENS}-token limit"
        )
    query = round(planned)
    tail = query - warmup - APPEND_OVERHEAD_TOKENS - APPEND_REPLY_TOKENS
    if tail < 1:
        raise ValueError(
            f"--hit-miss-ratio append:{float(hit_ratio)} with --document-length "
            f"{document_length} leaves no room for appended text"
        )
    designed = (warmup // page_size * page_size) / query
    return warmup, tail, query, designed


def constructed_hit_ratio(prompts, tails, doc_length_of, page_size):
    """
    Designed hit of the requests actually built: documents and tails have a
    length tolerance, which can move a warmup request across a page
    boundary, so use sum(page-floored W_i) / sum(query_i) over the queries.
    Returns (ratio, number of warmup documents with no full page).
    """
    hit = total = 0
    empty = set()
    for doc, tail_len in zip(prompts, tails):
        w = doc_length_of[doc] + CHAT_TEMPLATE_TOKENS
        hit += w // page_size * page_size
        total += w + APPEND_OVERHEAD_TOKENS + APPEND_REPLY_TOKENS + tail_len
        if w < page_size:
            empty.add(doc)
    return hit / total, len(empty)


def make_id_run(length, pattern, rng, used_starts, repeat_token_id):
    """
    One token id run of `length` ids in [0, vocab_size) whose first id was not
    used by any earlier run ('sequential': (start + j) % vocab_size;
    'random': uniform ids; 'repeat': start + repeat_token_id repeated).
    Raises when no unused first id is left (redraws also consume starts).
    """
    forbidden = 1 if pattern == "repeat" and repeat_token_id not in used_starts else 0
    if len(used_starts) + forbidden >= vocab_size:
        raise ValueError(
            f"all {vocab_size} distinct first ids are used up "
            "(too many documents / redraws for --vocab-size)"
        )
    if pattern == "sequential":
        start = rng.randrange(vocab_size)
        while start in used_starts:
            start = rng.randrange(vocab_size)
        ids = [(start + j) % vocab_size for j in range(length)]
    elif pattern == "random":
        ids = [rng.randrange(vocab_size) for _ in range(length)]
        while ids[0] in used_starts:
            ids[0] = rng.randrange(vocab_size)
    elif pattern == "repeat":
        start = rng.randrange(vocab_size)
        while start in used_starts or start == repeat_token_id:
            start = rng.randrange(vocab_size)
        ids = [start] + [repeat_token_id] * (length - 1)
    else:
        raise ValueError(f"Invalid token id pattern: {pattern}")
    used_starts.add(ids[0])
    return ids


def load_tokenizer(path):
    """HF `tokenizers` Tokenizer from tokenizer.json (file or model directory)."""
    # Third Party
    from tokenizers import Tokenizer

    if os.path.isdir(path):
        path = os.path.join(path, "tokenizer.json")
    return Tokenizer.from_file(path)


def count_tokens(tokenizer, text):
    return len(tokenizer.encode(text, add_special_tokens=False).ids)


def ids_to_text(tokenizer, ids, n_tokens, tolerance):
    """
    Decode `ids` to text whose re-encoded length is n_tokens (+-tolerance).
    decode->encode is not an identity (random ids usually re-encode ~2%
    longer, but some runs merge and come out much shorter), so trim on the
    re-encoded ids and re-check. Returns (text, encoded ids) or None when this
    id run cannot reach the target; the caller then draws a new run.
    """
    text = tokenizer.decode(ids)
    for _ in range(4):
        encoded = tokenizer.encode(text, add_special_tokens=False).ids
        if abs(len(encoded) - n_tokens) <= tolerance:
            return text, encoded
        if len(encoded) < n_tokens:
            return None
        text = tokenizer.decode(encoded[:n_tokens])
    return None


def make_text_documents(
    num, n_tokens, tokenizer, pattern, rng, used_starts, tolerance,
    used_prefixes, prefix_len, repeat_token_id,
):
    """
    Random-text documents of n_tokens (+-tolerance) tokens: token id runs
    decoded with the model tokenizer.

    Raw-id uniqueness does not survive decode (special ids decode to "",
    invalid UTF-8 pieces collapse), so every accepted text must re-encode to a
    first `prefix_len` tokens (one cache page) not used by any earlier
    document or tail (`used_prefixes`). Rejected runs are redrawn, at most
    TEXT_DOC_MAX_ATTEMPTS times per document.
    """
    docs, lengths = [], []
    for _ in range(num):
        for _attempt in range(TEXT_DOC_MAX_ATTEMPTS):
            ids = make_id_run(
                int(n_tokens * 1.1) + 16, pattern, rng, used_starts, repeat_token_id
            )
            result = ids_to_text(tokenizer, ids, n_tokens, tolerance)
            if result is None:
                continue
            text, encoded = result
            key = tuple(encoded[:prefix_len])
            if key in used_prefixes:
                continue
            used_prefixes.add(key)
            docs.append(text)
            lengths.append(len(encoded))
            break
        else:
            raise ValueError(
                f"could not build a unique {n_tokens}-token random text "
                f"(tolerance {tolerance}) in {TEXT_DOC_MAX_ATTEMPTS} attempts"
            )
    return docs, lengths


def add_appended_misses(prompts, tails):
    """
    Partial hit ('<h>'): query = [user: document, assistant: "OK",
    user: new text]. With DeepSeek-V4 chat rendering (thinking off) the
    single-turn warmup request is an exact token prefix of this conversation,
    so the designed hit boundary is the warmup request end (the actual
    restore is up to the server; check the reported cache hit).
    """
    queries = [
        [
            {"role": "user", "content": doc},
            {"role": "assistant", "content": APPEND_ASSISTANT_REPLY},
            {"role": "user", "content": tail},
        ]
        for doc, tail in zip(prompts, tails)
    ]
    return queries, [True] * len(prompts)


def relative_time(df, start_time):
    """
    Relative time to the start of the benchmark.
    """
    df["request_start"] = df["request_start"] - start_time
    df["request_end"] = df["request_end"] - start_time
    df["ttft_time"] = df["request_start"] + df["ttft"]


def visualize_results(warmup_df, benchmark_df):
    def plot_bars(df, title, filename):
        plt.figure(figsize=(12, 6))

        if "is_miss" in df.columns:
            is_miss = df["is_miss"]
        else:
            is_miss = pd.Series(False, index=df.index)

        hits = df[~is_miss]
        misses = df[is_miss]

        # Prefill: dark blue (hit), dark orange (miss)
        if not hits.empty:
            plt.barh(
                hits["prompt_id"],
                hits["ttft_time"] - hits["request_start"],
                left=hits["request_start"],
                color="darkblue",
                label="Loading",  # prefill hits
            )
        if not misses.empty:
            plt.barh(
                misses["prompt_id"],
                misses["ttft_time"] - misses["request_start"],
                left=misses["request_start"],
                color="darkorange",
                label="Compute",  # prefill misses
            )

        # Decode: light blue (hit), light orange (miss)
        if not hits.empty:
            plt.barh(
                hits["prompt_id"],
                hits["request_end"] - hits["ttft_time"],
                left=hits["ttft_time"],
                color="skyblue",
                label="Decoding after loading",
            )
        if not misses.empty:
            plt.barh(
                misses["prompt_id"],
                misses["request_end"] - misses["ttft_time"],
                left=misses["ttft_time"],
                color="pink",
                label="Decoding after compute",
            )

        plt.xlabel("Time (s)")
        plt.ylabel("Prompt ID")
        plt.legend()
        plt.tight_layout()
        plt.savefig(filename)
        plt.close()

    plot_bars(warmup_df, "Warmup Round", "warmup_round.png")
    plot_bars(benchmark_df, "Query Round", "query_round.png")


async def main(args):
    random.seed(args.shuffle_seed)

    # Create the OpenAI client
    # No timeout: some benchmarks can take 4-5 minutes per request
    base_url = get_url_from_args(args)
    print("Using base URL:", base_url)

    # Most locally hosted OpenAI-compatible servers accept a placeholder key.
    # If authentication is enabled, set OPENAI_API_KEY in the local environment.
    api_key = os.getenv("OPENAI_API_KEY", "placeholder")

    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=None,
    )
    model = args.model
    if model == "auto":
        print("Auto-selecting model...", end=" ")
        models = (await client.models.list(),)
        model = models[0].data[0].id
        print(f"selected model: {model}")

    partial_ratio = parse_partial_ratio(args.hit_miss_ratio)
    designed_hit = None
    nominal_hit = None

    if args.text_source == "random":
        tokenizer = load_tokenizer(args.tokenizer)
        # 独立 RNG: 不改变全局 random 的序列(未命中注入 / shuffle 仍由 shuffle_seed 决定)
        token_rng = random.Random(args.token_seed)
        used_starts = set()
        used_prefixes = set()

        def make_docs(num, n_tokens, tolerance):
            return make_text_documents(
                num, n_tokens, tokenizer, args.token_id_pattern, token_rng,
                used_starts, tolerance, used_prefixes, args.page_size,
                args.repeat_token_id,
            )

        pre_warmup_prompts, _ = make_docs(5, 1000, args.text_length_tolerance)
        # document content tokens; the chat template adds CHAT_TEMPLATE_TOKENS
        doc_tokens = args.document_length
        doc_tolerance = args.text_length_tolerance
        if partial_ratio is not None:
            partial_mode, ratio = partial_ratio
            # query = warmup request + OK + EOS + <User> + tail + <Assistant></think>
            plan = partial_total_plan if partial_mode == "total" else partial_append_plan
            warmup_len, tail_tokens, query_len, designed_hit = plan(
                args.document_length, ratio, args.page_size,
                args.text_length_tolerance,
            )
            if partial_mode == "total":
                # total length kept: the warmup request must end exactly on the
                # planned page boundary, so the document length is exact
                doc_tokens = warmup_len - CHAT_TEMPLATE_TOKENS
                doc_tolerance = 0
        warmup_prompts, doc_lengths = make_docs(
            args.num_documents, doc_tokens, doc_tolerance
        )
        print(
            f"Documents: random text ({args.token_id_pattern}, seed="
            f"{args.token_seed}), tokens min/max {min(doc_lengths)}/"
            f"{max(doc_lengths)} (target {doc_tokens})"
        )
        if partial_ratio is not None:
            append_tails, tail_lengths = make_docs(
                len(warmup_prompts) * args.repeat_count, tail_tokens,
                0 if partial_mode == "total" else args.text_length_tolerance,
            )
            doc_length_of = dict(zip(warmup_prompts, doc_lengths))
            nominal_hit = designed_hit
            print(
                f"Partial hit ({partial_mode}): warmup request ~{warmup_len} "
                f"tokens, query ~{query_len} tokens (+{query_len - warmup_len} "
                f"appended), nominal designed hit ratio {nominal_hit:.4f}"
            )
    else:
        pre_warmup_prompts = [
            str(i) + "xx" + " ".join(["hi"] * 1000) for i in range(5)
        ]
        # Prepare the prompts:
        # we append the document id at the beginning to avoid any of the document
        # being the prefix of other documents
        warmup_prompts = [
            str(i) + " " + " ".join(["hi"] * args.document_length)
            for i in range(args.num_documents)
        ]

    pre_warmup_stats = await test_long_document_qa(
        client=client,
        model=model,
        prompts=pre_warmup_prompts,
        output_len=args.output_len,
        max_inflight_requests=args.max_inflight_requests,
    )

    prompts = repeat_prompts(warmup_prompts, args.repeat_count, mode=args.repeat_mode)
    if partial_ratio is not None:
        designed_hit, no_page_docs = constructed_hit_ratio(
            prompts, tail_lengths, doc_length_of, args.page_size
        )
        print(f"Partial hit: designed hit ratio of the built requests {designed_hit:.4f}")
        if no_page_docs:
            print(f"WARNING: {no_page_docs} warmup requests are shorter than one "
                  f"page ({args.page_size} tokens): nothing of them can be restored")
        prompts, miss_mask = add_appended_misses(prompts, append_tails)
    else:
        prompts, miss_mask = add_cache_misses(prompts, args.hit_miss_ratio)

    write_resp("------warm up round------\n")
    warmup_start_time = time.time()
    warmup_request_stats = await test_long_document_qa(
        client=client,
        model=model,
        prompts=warmup_prompts,
        output_len=args.output_len,
        max_inflight_requests=args.max_inflight_requests,
    )
    warmup_end_time = time.time()
    write_resp("------query round------\n")

    sleep_time_after_warmup = args.sleep_time_after_warmup
    if sleep_time_after_warmup > 0:
        write_resp(f"Sleeping for {sleep_time_after_warmup} seconds after warmup...\n")
        time.sleep(sleep_time_after_warmup)

    benchmark_start_time = time.time()
    benchmark_request_stats = await test_long_document_qa(
        client=client,
        model=model,
        prompts=prompts,
        output_len=args.output_len,
        max_inflight_requests=args.max_inflight_requests,
    )
    benchmark_end_time = time.time()

    warmup_df = pd.DataFrame([stats.__dict__ for stats in warmup_request_stats])
    relative_time(warmup_df, warmup_start_time)
    warmup_df["is_miss"] = True
    benchmark_df = pd.DataFrame([stats.__dict__ for stats in benchmark_request_stats])
    benchmark_df["is_miss"] = miss_mask
    relative_time(benchmark_df, benchmark_start_time)

    # keep upstream column order; append new columns at the end
    tail_cols = ["prompt_tokens", "completion_tokens", "cached_tokens", "error"]

    def new_cols_last(df):
        cols = [c for c in df.columns if c not in tail_cols]
        return df[cols + tail_cols]

    warmup_df = new_cols_last(warmup_df)
    benchmark_df = new_cols_last(benchmark_df)
    warmup_df.to_csv("warmup_round.csv", index=False)
    benchmark_df.to_csv("query_round.csv", index=False)

    # Print results
    warmup_mean_ttft = warmup_df.query("successful == True")["ttft"].mean()
    query_mean_ttft = benchmark_df.query("successful == True")["ttft"].mean()
    warmup_success_count = warmup_df.query("successful == True").shape[0]
    query_success_count = benchmark_df.query("successful == True").shape[0]
    def round_tput(df, duration):
        in_tok = int(df["prompt_tokens"].sum())
        out_tok = int(df["completion_tokens"].sum())
        d = duration if duration > 0 else 1e-9
        return in_tok, out_tok, in_tok / d, out_tok / d, (in_tok + out_tok) / d

    w_in, w_out, w_itps, w_otps, w_ttps = round_tput(
        warmup_df, warmup_end_time - warmup_start_time
    )
    q_in, q_out, q_itps, q_otps, q_ttps = round_tput(
        benchmark_df, benchmark_end_time - benchmark_start_time
    )

    def input_rate(df):
        # per-request prefill-equivalent rate: prompt_tokens / TTFT
        ok = df[(df["successful"]) & (df["ttft"] > 0) & (df["prompt_tokens"] > 0)]
        if ok.empty:
            return 0.0, 0.0
        rates = ok["prompt_tokens"] / ok["ttft"]
        return float(rates.mean()), float(rates.max())

    w_irate_mean, w_irate_peak = input_rate(warmup_df)
    q_irate_mean, q_irate_peak = input_rate(benchmark_df)

    # sglang returns prompt_tokens_details=None both when --enable-cache-report
    # is off and when a request cached 0 tokens, so one response cannot tell.
    # Any reported value in the run (pre-warmup included) proves the report
    # is on; then a missing value is taken as 0. This assumes one server with
    # one configuration for the whole run (uniform "omit when 0" protocol).
    pre_warmup_failed = sum(1 for s in pre_warmup_stats if not s.successful)
    if (
        any(s.cached_tokens >= 0 for s in pre_warmup_stats)
        or (warmup_df["cached_tokens"] >= 0).any()
        or (benchmark_df["cached_tokens"] >= 0).any()
    ):
        cache_report = "detected"
    else:
        cache_report = "not_detected"

    def actual_cache(df):
        """
        Token-weighted server-reported hit over the successful requests of a
        round. With the report detected a missing value counts as 0;
        otherwise the round is unknown (None), never a ratio over a reported
        subset.
        """
        ok = df[df["successful"]]
        if ok.empty or int(ok["prompt_tokens"].sum()) == 0:
            return None, 0
        cached = ok["cached_tokens"]
        reported = int((cached >= 0).sum())
        if cache_report == "detected":
            cached = cached.clip(lower=0)
        elif reported < len(ok):
            return None, reported
        return float(cached.sum() / ok["prompt_tokens"].sum()), reported

    w_hit, w_hit_n = actual_cache(warmup_df)
    q_hit, q_hit_n = actual_cache(benchmark_df)
    failed = int((~warmup_df["successful"]).sum() + (~benchmark_df["successful"]).sum())
    if pre_warmup_failed:
        print(f"WARNING: {pre_warmup_failed} of {len(pre_warmup_stats)} pre-warmup "
              "requests failed; the service may not have been warmed up")
    if failed:
        print(f"WARNING: {failed} failed requests; throughput includes them")
    if w_hit is not None and w_hit > 0:
        print(f"WARNING: warmup round is not cold (server cache hit {w_hit:.4f})")

    CSI = "\x1b["
    RESET = CSI + "0m"
    print(f"Warmup round mean TTFT: {warmup_mean_ttft:.3f}s")
    print(f"Warmup round time: {warmup_end_time - warmup_start_time:.3f}s")
    print(f"Warmup round prompt count: {len(warmup_df)}")
    print(f"Warmup round successful prompt count: {warmup_success_count}")
    print(f"Warmup round tokens (in/out): {w_in} / {w_out}")
    print(
        f"Warmup round throughput (in/out/total): "
        f"{w_itps:.1f} / {w_otps:.1f} / {w_ttps:.1f} toks/s"
    )
    print(
        f"Warmup round input rate (tokens/TTFT, mean/peak): "
        f"{w_irate_mean:.1f} / {w_irate_peak:.1f} toks/s"
    )
    print(f"{CSI}36;1m\n=== BENCHMARK RESULTS ==={RESET}")
    print(f"{CSI}32mQuery round mean TTFT: {query_mean_ttft:.3f}s{RESET}")
    print(
        f"{CSI}33mQuery round time: "
        f"{benchmark_end_time - benchmark_start_time:.3f}s{RESET}"
    )
    print(f"{CSI}35mQuery round prompt count: {len(benchmark_df)}{RESET}")
    print(f"{CSI}34mQuery round successful prompt count: {query_success_count}{RESET}")
    print(f"{CSI}32mQuery round tokens (in/out): {q_in} / {q_out}{RESET}")
    print(
        f"{CSI}32mQuery round throughput (in/out/total): "
        f"{q_itps:.1f} / {q_otps:.1f} / {q_ttps:.1f} toks/s{RESET}"
    )
    print(
        f"{CSI}32mQuery round input rate (tokens/TTFT, mean/peak): "
        f"{q_irate_mean:.1f} / {q_irate_peak:.1f} toks/s{RESET}"
    )
    print(
        "Actual cache hit (server reported, token-weighted) warmup/query: "
        f"{'unknown' if w_hit is None else round(w_hit, 4)} / "
        f"{'unknown' if q_hit is None else round(q_hit, 4)}"
        f"  [cache report: {cache_report}]"
    )
    if cache_report == "not_detected":
        print(
            "WARNING: actual cache hit unknown: no response of this run carried "
            "cached-token details (server without --enable-cache-report, no "
            "successful hit request, or no prefix hit at all)"
        )

    if visualize:
        visualize_results(warmup_df, benchmark_df)

    if args.json_output:
        query_duration = benchmark_end_time - benchmark_start_time
        query_round_time_per_prompt = query_duration / len(benchmark_df)
        warmup_duration = warmup_end_time - warmup_start_time
        warmup_round_time_per_prompt = warmup_duration / len(warmup_df)

        summary = {
            "query_ttft_per_prompt": query_mean_ttft,
            "query_round_time_per_prompt": query_round_time_per_prompt,
            "warmup_round_time_per_prompt": warmup_round_time_per_prompt,
            "warmup_input_tokens": w_in,
            "warmup_output_tokens": w_out,
            "warmup_input_throughput": w_itps,
            "warmup_output_throughput": w_otps,
            "warmup_total_throughput": w_ttps,
            "query_input_tokens": q_in,
            "query_output_tokens": q_out,
            "query_input_throughput": q_itps,
            "query_output_throughput": q_otps,
            "query_total_throughput": q_ttps,
            "warmup_input_rate_mean": w_irate_mean,
            "warmup_input_rate_peak": w_irate_peak,
            "query_input_rate_mean": q_irate_mean,
            "query_input_rate_peak": q_irate_peak,
            # 口径元数据: total('<h>') 时 query 恒为 T、warmup 更短; append 时 warmup 为文档、query 追加; 是否冷看实际命中
            "text_source": args.text_source,
            "token_id_pattern": args.token_id_pattern,
            "token_seed": args.token_seed,
            "hit_miss_ratio": args.hit_miss_ratio,
            "partial_mode": partial_ratio[0] if partial_ratio else None,
            "designed_hit_ratio": designed_hit,
            "nominal_designed_hit_ratio": nominal_hit,
            "page_size": args.page_size,
            "chat_thinking_off": chat_thinking_off,
            "warmup_requests": len(warmup_df),
            "query_requests": len(benchmark_df),
            "warmup_prompt_tokens_mean": float(warmup_df["prompt_tokens"].mean()),
            "query_prompt_tokens_mean": float(benchmark_df["prompt_tokens"].mean()),
            "failed_requests": failed,
            "pre_warmup_failed_requests": pre_warmup_failed,
            "cache_report": cache_report,
            "warmup_actual_cache_hit": w_hit,
            "query_actual_cache_hit": q_hit,
            "warmup_cache_reported_requests": w_hit_n,
            "query_cache_reported_requests": q_hit_n,
        }
        print(json.dumps(summary))

    # 任一阶段有失败请求: 结果已输出, 以非零退出让 harness 拒收这次运行
    if failed or pre_warmup_failed:
        print(
            f"ERROR: run invalid ({pre_warmup_failed} pre-warmup / {failed} "
            "warmup+query requests failed); exiting with status 2"
        )
        sys.exit(2)


def create_argument_parser():
    parser = argparse.ArgumentParser(
        description="Benchmark the performance with or "
        "without automatic prefix caching."
    )

    parser.add_argument(
        "--document-length",
        type=int,
        # Roughly the number of tokens for a system paper,
        # excluding images
        default=20000,
        help="Length of each document in tokens (chat adds 4 template tokens); "
        "the query length with --hit-miss-ratio <h> (total-length mode).",
    )

    parser.add_argument(
        "--num-documents",
        type=int,
        default=8,
        help="Number of documents to generate for testing.",
    )

    parser.add_argument(
        "--output-len",
        type=int,
        default=100,
        help="Maximum number of tokens to generate for each prompt.",
    )

    parser.add_argument(
        "--repeat-count",
        type=int,
        default=2,
        help="Number of times to repeat each prompt",
    )

    parser.add_argument(
        "--repeat-mode",
        type=str,
        default="random",
        help="The mode to repeat prompts. The supported "
        'modes are "random", "tile", and "interleave". '
        "See repeat_prompts() in the source code for details.",
    )

    parser.add_argument(
        "--shuffle-seed",
        type=int,
        default=0,
        help='Random seed when the repeat mode is "random"',
    )

    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Host to query the vLLM server",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to query the vLLM server",
    )

    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="Base URL to query the LLM server",
    )

    parser.add_argument(
        "--model",
        type=str,
        default="auto",
        help="Model name, can be set to 'auto' if the "
        "endpoint support openai api /models",
    )

    parser.add_argument(
        "--max-inflight-requests",
        type=int,
        default=2,
        help="Maximum number of concurrent inflight requests",
    )

    parser.add_argument(
        "--sleep-time-after-warmup",
        type=float,
        default=0.0,
        help="Sleep time after warm up iteration",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Filename to write all responses to; if omitted, writes to stdout.",
    )

    parser.add_argument(
        "--completions",
        action="store_true",
        help="Use completions API instead of chat completions API",
    )

    parser.add_argument(
        "--visualize",
        action="store_true",
        help="Visualize the results",
    )

    parser.add_argument(
        "--hit-miss-ratio",
        type=str,
        default=None,
        help=(
            "Query-round cache hits. 'hit:miss' (e.g. 99:1): in every period "
            "of hit+miss requests the first `hit` repeat their document and "
            "the last `miss` get a random prefix (full miss). "
            "Per-request partial hit (random text only): '<h>' (e.g. 0.99) "
            "makes every query exactly --document-length tokens with a "
            "page-aligned, shorter warmup; 'append:<h>' keeps the warmup "
            "document at --document-length tokens (+4 chat template) and "
            "appends so that warmup ~ h * query. Omitted: queries repeat the "
            "warmup requests."
        ),
    )

    parser.add_argument(
        "--eos-token-id",
        type=int,
        default=None,
        help=(
            "EOS token id. we bias against this token id so we always "
            "get the number of output tokens we specify"
        ),
    )

    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Print benchmark summary as a single JSON line to stdout.",
    )

    parser.add_argument(
        "--text-source",
        type=str,
        default="random",
        choices=["random", "hi"],
        help="'random' (default): random token id runs decoded into text with "
        "--tokenizer; 'hi': the original '<i> hi hi ...' documents.",
    )

    parser.add_argument(
        "--tokenizer",
        type=str,
        default="/ai_data/models/DeepSeek-V4-Flash-0731-W4A8-INT4-Channel-Attn-W8A8-INT8-Channel",
        help="(random text) tokenizer.json or the model directory containing it.",
    )

    parser.add_argument(
        "--token-id-pattern",
        type=str,
        default="sequential",
        choices=["sequential", "random", "repeat"],
        help="(random text) 'sequential': cyclic id runs from de-duplicated "
        "random starts, the same kind of content as bench_multiturn.py "
        "--disable-random-sample (not an id-for-id replay); 'random' draws "
        "each id uniformly; 'repeat' = unique first id + --repeat-token-id.",
    )

    parser.add_argument(
        "--token-seed",
        type=int,
        default=1,
        help="(random text) seed of the document generator.",
    )

    parser.add_argument(
        "--vocab-size",
        type=int,
        default=128000,
        help="(random text) ids are drawn from [0, vocab-size); 128000 = "
        "tokenizer.vocab_size of DeepSeek-V4-Flash (added tokens above).",
    )

    parser.add_argument(
        "--repeat-token-id",
        type=int,
        default=19346,
        help="(random text, repeat pattern) default 19346 = 'hi'.",
    )

    parser.add_argument(
        "--text-length-tolerance",
        type=int,
        default=8,
        help="(random text) allowed |re-encoded length - target| per document "
        "and appended text (total-length partial-hit texts are exact).",
    )

    parser.add_argument(
        "--page-size",
        type=int,
        default=256,
        help="Server page size: the designed partial hit rounds the "
        "restorable warmup request down to it, and every document's first "
        "page must be unique.",
    )

    return parser


def validate_args(args):
    # Verify port and base_url are exclusive
    has_host_port = args.host is not None and args.port is not None
    has_base_url = args.base_url is not None
    if has_host_port and has_base_url:
        raise ValueError("Cannot use --host/--port and --base-url together.")

    # 边界与唯一首 token 容量, 避免死循环 / 越界 id / 阻塞
    for name in ("document_length", "num_documents", "repeat_count", "page_size",
                 "vocab_size", "output_len", "max_inflight_requests"):
        if getattr(args, name) <= 0:
            raise ValueError(f"--{name.replace('_', '-')} must be positive")
    if args.text_length_tolerance < 0:
        raise ValueError("--text-length-tolerance must be >= 0")
    partial = parse_partial_ratio(args.hit_miss_ratio)
    ratio = partial[1] if partial else None
    if args.hit_miss_ratio is not None and partial is None:
        hit, miss = map(int, args.hit_miss_ratio.split(":", 1))
        if hit < 0 or miss < 0 or hit + miss == 0:
            raise ValueError("--hit-miss-ratio hit:miss needs non-negative, non-zero sum")
    if ratio is not None:
        if args.text_source != "random":
            raise ValueError("--hit-miss-ratio <h> needs --text-source random")
        if args.completions:
            raise ValueError("--hit-miss-ratio <h> needs the chat API, not --completions")
        # raises when the document or the appended text would be empty, or
        # the planned query is too long
        plan = partial_total_plan if partial[0] == "total" else partial_append_plan
        plan(args.document_length, ratio, args.page_size, args.text_length_tolerance)
    if args.text_source == "random":
        if not 0 <= args.repeat_token_id < args.vocab_size:
            raise ValueError("--repeat-token-id must be in [0, --vocab-size)")
        # distinct raw first ids: 5 pre-warmup + documents (+ one tail per
        # query); redraws consume more, bounded by TEXT_DOC_MAX_ATTEMPTS
        needed = 5 + args.num_documents
        if ratio is not None:
            needed += args.num_documents * args.repeat_count
        available = args.vocab_size - (1 if args.token_id_pattern == "repeat" else 0)
        if needed > available:
            raise ValueError(
                f"need {needed} distinct first tokens, only {available} available"
            )


if __name__ == "__main__":
    parser = create_argument_parser()
    args = parser.parse_args()
    validate_args(args)
    completions_mode = args.completions
    visualize = args.visualize
    if visualize:
        # Third Party
        import matplotlib.pyplot as plt
    if args.eos_token_id is not None:
        eos_token_id = args.eos_token_id
    vocab_size = args.vocab_size
    chat_thinking_off = parse_partial_ratio(args.hit_miss_ratio) is not None
    OUTPUT_FILE = args.output
    asyncio.run(main(args))
