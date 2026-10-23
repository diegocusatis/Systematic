"""Download the semantic model and prove it loads offline. Runs in the app environment."""
import json
import os
from pathlib import Path
import sys

# Weights in safetensors plus configuration and tokenizer; skips ONNX/OpenVINO/TF copies.
FILES = ["*.json", "*.txt", "*.model", "*.safetensors"]


def main(cache, repo, revision):
    from huggingface_hub import snapshot_download
    try:
        path = snapshot_download(repo, revision=revision, cache_dir=cache, allow_patterns=FILES)
    except Exception:
        # Offline rerun: an already downloaded copy is still valid.
        path = snapshot_download(repo, revision=revision, cache_dir=cache, allow_patterns=FILES,
                                 local_files_only=True)
    os.environ["HF_HUB_OFFLINE"] = "1"
    from sentence_transformers import SentenceTransformer
    commit = Path(path).name
    model = SentenceTransformer(repo, revision=commit, cache_folder=cache, local_files_only=True,
                                trust_remote_code=False)
    if len(model.encode(["Systematic offline model check"])) != 1:
        raise RuntimeError("The model did not produce an embedding.")
    print("SYSTEMATIC_MODEL=" + json.dumps(dict(repo=repo, revision=commit, cache=str(cache))), flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
