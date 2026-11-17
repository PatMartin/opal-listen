"""Run the API server: python -m opal_listener"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from opal_listener.app_settings import dotenv_baseline, get_app_settings
from opal_listener.dotenv import DotenvError, prepare


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="opal-listen-svc")
    parser.add_argument("--env-file")
    parser.add_argument("--no-env-file", action="store_true")
    args = parser.parse_args(argv)
    try:
        prepare(env_file=args.env_file, no_env_file=args.no_env_file, baseline=dotenv_baseline())
    except DotenvError as exc:
        parser.error(str(exc))
    # A managed-server restart (`POST /api/v1/service/start` on the tracked pid) asks the
    # replacement child to hold off binding until the outgoing process has exited.
    wait_for = os.environ.get("OPAL_LISTENER_WAIT_FOR_PID")
    if wait_for:
        from opal_listener.service.lifecycle import wait_for_pids

        wait_for_pids(wait_for.split(","))
    # `opal listen service start` passes this so the pidfile records the *server's* pid, not the
    # python-launcher shim's pid (the shim is not the parent of the uvicorn worker on Windows).
    pid_file = os.environ.get("OPAL_LISTENER_PID_FILE")
    if pid_file:
        Path(pid_file).write_text(str(os.getpid()))
    settings = get_app_settings()
    uvicorn.run("opal_listener.main:app_factory", factory=True, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
