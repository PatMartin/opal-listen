"""Run the API server: python -m opal_listener"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from opal_listener.app_settings import get_app_settings


def main() -> None:
    # `opal listen service start` passes this so the pidfile records the *server's* pid, not the
    # python-launcher shim's pid (the shim is not the parent of the uvicorn worker on Windows).
    pid_file = os.environ.get("OPAL_LISTENER_PID_FILE")
    if pid_file:
        Path(pid_file).write_text(str(os.getpid()))
    settings = get_app_settings()
    uvicorn.run("opal_listener.main:app_factory", factory=True, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
