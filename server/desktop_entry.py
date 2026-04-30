import os

import uvicorn

from main import app


def main() -> None:
    host = os.environ.get("ASTUDIO_SERVER_HOST") or os.environ.get("ANTIT_SERVER_HOST") or "127.0.0.1"
    port = int(os.environ.get("ASTUDIO_SERVER_PORT") or os.environ.get("ANTIT_SERVER_PORT") or "8000")
    log_level = os.environ.get("ASTUDIO_LOG_LEVEL", "info")
    uvicorn.run(app, host=host, port=port, log_level=log_level)


if __name__ == "__main__":
    main()
