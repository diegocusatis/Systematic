"""Desktop-only server wrapper with a maintenance lock and graceful Stop button."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.request

from installer import installation_lock, recover


def enable_ocr(state):
    """Turn on OCR with the engine setup verified; SYSTEMATIC_OCR=0 still opts out."""
    tesseract = state.get("tesseract")
    if tesseract and Path(tesseract).is_file():
        os.environ.setdefault("SYSTEMATIC_OCR", "1")
        os.environ["SYSTEMATIC_TESSERACT"] = tesseract


def enable_semantic(state):
    """Point the app at the model setup downloaded and verified; never fetch at runtime."""
    model = state.get("semantic_model")
    if not model or not Path(model["cache"]).is_dir():
        return
    os.environ.update(HF_HUB_OFFLINE="1", HF_HUB_CACHE=model["cache"],
                      SENTENCE_TRANSFORMERS_HOME=model["cache"])
    os.environ.setdefault("SYSTEMATIC_EMBEDDING_MODEL", model["repo"])
    os.environ.setdefault("SYSTEMATIC_EMBEDDING_REVISION", model["revision"])


def prepare_search(service):
    """Initialize native libraries before the launcher starts waiting on stdin.

    On Windows, SciPy/OpenBLAS initialization can wait indefinitely when another
    thread is blocked reading the launcher's pipe. Setup's standalone model
    check does not have that reader, so it cannot detect this startup deadlock.
    Load the same offline models once here; scan workers reuse them afterward.
    """
    print("Preparing local search…", flush=True)
    if service.semantic._load() is None:
        print(service.semantic.error, flush=True)
    if service.reranker.enabled and service.reranker._load() is None:
        print(service.reranker.error, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    with installation_lock(args.home):
        recover(args.home)
        state = json.loads((args.home / "current.json").read_text(encoding="utf-8"))
        enable_ocr(state)
        enable_semantic(state)
        # Bind before constructing the app, so a port conflict cannot touch review data.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        from review_extractor.app import create_app
        import uvicorn
        app = create_app(Path(state["data"]))
        prepare_search(app.state.service)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

        def stop_on_input():
            # EOF also stops the server if the launcher crashes.
            sys.stdin.readline()
            server.should_exit = True

        def ready():
            while not server.started and not server.should_exit:
                time.sleep(.1)
            if server.started:
                print("SYSTEMATIC_READY=http://127.0.0.1:" + str(port), flush=True)

        threading.Thread(target=stop_on_input, daemon=True).start()
        threading.Thread(target=ready, daemon=True).start()
        try:
            server.run(sockets=[sock])
            if not server.started:
                raise RuntimeError("Application startup failed. See the application log for details.")
        finally:
            sock.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("SYSTEMATIC_ERROR: " + str(error), flush=True)
        sys.exit(1)
