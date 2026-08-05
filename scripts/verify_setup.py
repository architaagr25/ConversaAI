"""
Checks this machine can run the project: interpreter version, every dependency
imports, FFmpeg reachable, and the local embedding model produces sensible
vectors. That last one matters most - it's the only compiled model running
locally and the most likely thing to break on an unfamiliar machine.

    .venv\\Scripts\\python scripts/verify_setup.py
"""

from __future__ import annotations

import importlib
import importlib.metadata
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_CACHE = PROJECT_ROOT / ".cache" / "models"

# Import name, then distribution name. They differ often enough that deriving
# one from the other doesn't work: bs4/beautifulsoup4, dotenv/python-dotenv.
PACKAGES = [
    ("dotenv", "python-dotenv"),
    ("pydantic", "pydantic"),
    ("pydantic_settings", "pydantic-settings"),
    ("tenacity", "tenacity"),
    ("rich", "rich"),
    ("google.genai", "google-genai"),
    ("groq", "groq"),
    ("deepgram", "deepgram-sdk"),
    ("edge_tts", "edge-tts"),
    ("fastembed", "fastembed"),
    ("numpy", "numpy"),
    ("rank_bm25", "rank-bm25"),
    ("datasketch", "datasketch"),
    ("httpx", "httpx"),
    ("trafilatura", "trafilatura"),
    ("selectolax", "selectolax"),
    ("bs4", "beautifulsoup4"),
    ("lxml", "lxml"),
    ("pypdf", "pypdf"),
    ("pdfplumber", "pdfplumber"),
    ("dateutil", "python-dateutil"),
    ("phonenumbers", "phonenumbers"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("websockets", "websockets"),
    ("jinja2", "jinja2"),
    ("soundfile", "soundfile"),
    ("webrtcvad", "webrtcvad-wheels"),
    ("pydub", "pydub"),
    ("pytest", "pytest"),
]

# Multilingual first: the knowledge base has to answer Tagalog and Indonesian
# questions, not only English.
PREFERRED_MODELS = [
    "intfloat/multilingual-e5-small",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "BAAI/bge-small-en-v1.5",
]

failures: list[str] = []


def heading(text: str) -> None:
    print(f"\n{text}")
    print("-" * len(text))


def check_interpreter() -> None:
    heading("Interpreter")
    major, minor = sys.version_info[:2]
    print(f"  python     {sys.version.split()[0]}")
    print(f"  executable {sys.executable}")
    if (major, minor) != (3, 12):
        failures.append(
            f"expected Python 3.12, found {major}.{minor}. "
            "Several audio and model packages have no wheels for newer versions."
        )
    if sys.prefix == sys.base_prefix:
        failures.append("not running inside the virtual environment")


def check_imports() -> None:
    heading("Dependencies")
    width = max(len(name) for name, _ in PACKAGES)
    for module_name, dist_name in PACKAGES:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            print(f"  {module_name:<{width}}  FAILED  {exc}")
            failures.append(f"{dist_name} does not import: {exc}")
            continue
        try:
            version = importlib.metadata.version(dist_name)
        except importlib.metadata.PackageNotFoundError:
            version = "?"
        print(f"  {module_name:<{width}}  {version}")


def check_ffmpeg() -> None:
    heading("FFmpeg")
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        if not path:
            print(f"  {tool:<8} NOT FOUND")
            failures.append(f"{tool} is not on the system path")
            continue
        try:
            result = subprocess.run(
                [tool, "-version"], capture_output=True, text=True, timeout=15
            )
            version = result.stdout.splitlines()[0].split()[2]
        except Exception:
            version = "unknown"
        print(f"  {tool:<8} {version}")


def check_embeddings() -> None:
    """Load the model and check it separates related from unrelated text.

    A model that loads but returns noise passes an import check and then quietly
    ruins retrieval, hence three sentences rather than one call.
    """
    heading("Embedding model")
    try:
        import numpy as np
        from fastembed import TextEmbedding
    except Exception as exc:
        failures.append(f"cannot import the embedding stack: {exc}")
        print(f"  import failed: {exc}")
        return

    available = {entry["model"] for entry in TextEmbedding.list_supported_models()}
    model_name = next((m for m in PREFERRED_MODELS if m in available), None)
    if model_name is None:
        failures.append("none of the preferred embedding models are available")
        print("  no usable model found")
        return

    print(f"  model      {model_name}")
    print(f"  cache      {MODEL_CACHE}")
    print("  loading (first run downloads the model, roughly 130 MB)...")

    MODEL_CACHE.mkdir(parents=True, exist_ok=True)
    try:
        embedder = TextEmbedding(model_name=model_name, cache_dir=str(MODEL_CACHE))
        sentences = [
            "What is the waiting period for pre-existing conditions?",
            "How long before pre-existing illnesses are covered?",
            "The office car park closes at eight in the evening.",
        ]
        vectors = np.array(list(embedder.embed(sentences)))
    except Exception as exc:
        failures.append(f"embedding failed: {exc}")
        print(f"  FAILED: {exc}")
        return

    def cosine(a, b):
        return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))

    related = cosine(vectors[0], vectors[1])
    unrelated = cosine(vectors[0], vectors[2])

    print(f"  dimensions {vectors.shape[1]}")
    print(f"  similar question pair    {related:.3f}")
    print(f"  unrelated sentence pair  {unrelated:.3f}")

    if related <= unrelated:
        failures.append(
            "the model rates an unrelated sentence as close as a paraphrase, "
            "so retrieval would not work"
        )
    else:
        print("  paraphrase scores higher than the unrelated sentence, as expected")


def main() -> int:
    print("=" * 62)
    print("ConversaAI setup check")
    print("=" * 62)

    check_interpreter()
    check_imports()
    check_ffmpeg()
    check_embeddings()

    heading("Result")
    if failures:
        print(f"  {len(failures)} problem(s) found:\n")
        for item in failures:
            print(f"    - {item}")
        return 1
    print("  everything checks out")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
