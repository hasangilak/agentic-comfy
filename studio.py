# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "fastapi", "uvicorn[standard]", "pillow", "numpy", "scipy", "httpx",
#   "imageio-ffmpeg", "websocket-client",
# ]
# ///
"""The Paper Reel studio: a node canvas for building reels.

    uv run studio.py            # http://127.0.0.1:8787

Serves the built frontend from studio/dist when it exists. For frontend development run
`npm run dev` in studio/ alongside this and use the Vite URL instead -- it proxies here.

Binds to loopback only. This process holds your Modal proxy tokens and can spend money on
a GPU; it is not something to expose on a network.
"""

from __future__ import annotations

import argparse

import uvicorn

from paperreel import config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--reload", action="store_true", help="for backend development")
    args = parser.parse_args()

    dist = config.ROOT / "studio" / "dist"
    print(f"[studio] http://{args.host}:{args.port}")
    print(f"[studio] frontend: {'studio/dist' if dist.is_dir() else 'NOT BUILT -- run npm run dev in studio/'}")
    print(f"[studio] backend:  {config.BACKEND_URL}")
    uvicorn.run("paperreel.api:app", host=args.host, port=args.port, reload=args.reload,
                log_level="warning")


if __name__ == "__main__":
    main()
