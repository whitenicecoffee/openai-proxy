"""Run the local basispoints proxy."""

from __future__ import annotations

import argparse
import ipaddress
import logging
import os
from pathlib import Path

from bps_proxy.server import serve, MAX_CONCURRENT_REQUESTS, MAX_PENDING_REQUESTS, QUEUE_TIMEOUT
from bps_proxy.admission import Admission
from bps_proxy.rate_limit import RequestRateLimiter, UPSTREAM_REQUESTS_PER_SECOND
from bps_proxy.catalog import catalog_snapshot
from bps_proxy.upstream import MAX_EVENT_BYTES, validate_max_sse_event_bytes
from bps_proxy.wire import CallMemory, DEFAULT_MODEL
from bps_proxy.network import description as network_description


def main() -> None:
    parser = argparse.ArgumentParser(
        description="把本机 ChatGPT 的 Responses 请求转到 Excel 插件后端，并用 run_officejs 转接工具。"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument('--model-catalog', type=Path, help='使用指定的模型目录文件')
    parser.add_argument('--max-concurrent', type=int, default=MAX_CONCURRENT_REQUESTS)
    parser.add_argument('--max-pending', type=int, default=MAX_PENDING_REQUESTS)
    parser.add_argument('--upstream-rps', type=int, default=UPSTREAM_REQUESTS_PER_SECOND, help='每秒最多发起的上游请求数')
    parser.add_argument('--queue-timeout', type=float, default=QUEUE_TIMEOUT, help='排队最长等待秒数')
    parser.add_argument('--max-sse-event-mib', type=int, default=MAX_EVENT_BYTES // (1024 * 1024),
                        help='上游 SSE 单行及单事件大小上限，单位 MiB（默认 16）')
    parser.add_argument(
        "--state",
        type=Path,
        default=Path.home() / ".bps-proxy" / "calls.json",
        help="记住 run_officejs 调用，方便工具结果回放",
    )
    args = parser.parse_args()
    try:
        valid_host = args.host == 'localhost' or ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        valid_host = False
    if not valid_host:
        parser.error('--host must be a loopback address or localhost')
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    try:
        Admission(args.max_concurrent, args.max_pending, args.queue_timeout)
        RequestRateLimiter(args.upstream_rps)
        if args.max_sse_event_mib <= 0:
            raise ValueError('--max-sse-event-mib must be positive')
        max_sse_event_bytes = validate_max_sse_event_bytes(args.max_sse_event_mib * 1024 * 1024)
        if args.model_catalog is not None:
            os.environ['BPS_MODEL_CATALOG'] = str(args.model_catalog.expanduser().resolve())
        catalog_snapshot()
    except ValueError as exc:
        parser.error(str(exc))
    address = f'[{args.host}]' if ':' in args.host else args.host
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(
        "\n".join(
            [
                f"代理已准备监听 http://{address}:{args.port}/v1",
                f"默认模型 {DEFAULT_MODEL}。effort 的 max 会映射成 xhigh。",
                f"上游网络模式：{network_description()}。",
                "Codex 配置已由 start.bat 写入 openai-proxy；手动配置请使用项目 README 中的 model_providers.openai-proxy。",
                "",
            ]
        ),
        flush=True,
    )
    serve(args.host, args.port, CallMemory(args.state), max_concurrent=args.max_concurrent,
          max_pending=args.max_pending, queue_timeout=args.queue_timeout, upstream_rps=args.upstream_rps,
          max_sse_event_bytes=max_sse_event_bytes)


if __name__ == "__main__":
    main()
