"""Run the eval-api service: ``py -3.10 -m runtime_eval.eval_api``."""

from __future__ import annotations

import argparse

from .app import run_server


def main() -> int:
    p = argparse.ArgumentParser(prog="runtime_eval.eval_api", description="eval-api 后端服务")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8800)
    p.add_argument("--env", default=None, help=".env 路径（默认 runtime_eval/.env）")
    args = p.parse_args()
    run_server(host=args.host, port=args.port, env_file=args.env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
