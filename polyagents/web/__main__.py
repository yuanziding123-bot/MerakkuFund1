"""``python -m polyagents.web`` — serve the chat UI."""
import os

import uvicorn


def main() -> None:
    host = os.getenv("POLYAGENTS_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("POLYAGENTS_WEB_PORT", "8000"))
    print(f"polyagents chat -> http://{host}:{port}  (needs ANTHROPIC_API_KEY)")
    uvicorn.run("polyagents.web.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
