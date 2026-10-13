"""Run the API server: python -m opal_listener"""

from __future__ import annotations

import uvicorn

from opal_listener.app_settings import get_app_settings


def main() -> None:
    settings = get_app_settings()
    uvicorn.run("opal_listener.main:app_factory", factory=True, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
