"""Windows-friendly local launcher: python -m review_extractor."""

import argparse
import os
from pathlib import Path
import threading
import webbrowser

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Local full-text review and evidence extraction")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="Local storage folder (default: user application data)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["REVIEW_EXTRACTOR_DATA_DIR"] = str(args.data_dir.resolve())
    from .app import create_app
    app = create_app(args.data_dir)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Review Extractor: {url}\nLocal files: {app.state.service.data_dir}")
    if not args.no_browser:
        timer = threading.Timer(1.2, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
