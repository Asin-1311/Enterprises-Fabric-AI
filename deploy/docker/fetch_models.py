"""Download the CPU models into the image at build time (M17b; deploy/docker/app.Dockerfile).

Each model is fetched at the exact revision used on the development laptop, with only the
files the app loads (one safetensors copy of the weights). The cache then records that
revision as `main` (refs/main), because the app asks for the models by name and the
containers run offline (HF_HUB_OFFLINE=1): without the ref, an offline lookup of "main"
finds nothing (found when the first image would not start). Finally each model is loaded
once, offline, so a broken cache fails the BUILD, not the pod.
"""

import os
from pathlib import Path

from huggingface_hub import snapshot_download

MODELS = {  # the app/config.py defaults, at the revisions used on the laptop
    "BAAI/bge-small-en-v1.5": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
    "BAAI/bge-reranker-base": "2cfc18c9415c912f9d8155881c133215df768a70",
    "cross-encoder/nli-deberta-v3-xsmall": "a150876415327c80daeff35ca6f68f5ed8cf5c24",
}
KEEP = ["*.json", "*.txt", "*.model", "model.safetensors", "1_Pooling/*"]


def main() -> None:
    for name, revision in MODELS.items():
        snapshot = Path(snapshot_download(name, revision=revision, allow_patterns=KEEP))
        refs = snapshot.parent.parent / "refs"
        refs.mkdir(exist_ok=True)
        (refs / "main").write_text(revision, encoding="utf-8")
        print(f"{name} @ {revision[:10]}")

    os.environ["HF_HUB_OFFLINE"] = "1"
    from sentence_transformers import CrossEncoder, SentenceTransformer

    SentenceTransformer("BAAI/bge-small-en-v1.5", device="cpu")
    CrossEncoder("BAAI/bge-reranker-base", device="cpu")
    CrossEncoder("cross-encoder/nli-deberta-v3-xsmall", device="cpu")
    print("all models load offline")


if __name__ == "__main__":
    main()
