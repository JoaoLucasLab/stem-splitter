"""Start the Stem Splitter.  Just run:  python3 run.py"""
import platform
import sys
import threading
import webbrowser

HOST = "127.0.0.1"
PORT = 8000


def check_gpu() -> None:
    """Print whether the models will run on the Mac's GPU (MPS), and why not if they won't."""
    print("-" * 60)
    print(f"Python : {sys.version.split()[0]}  ({sys.executable})")
    print(f"Arch   : {platform.machine()}")
    try:
        import torch
    except ImportError:
        print("Torch  : NOT INSTALLED -> python3 -m pip install -r requirements.txt")
        print("-" * 60)
        return
    print(f"Torch  : {torch.__version__}")
    print(f"MPS    : built={torch.backends.mps.is_built()}  available={torch.backends.mps.is_available()}")

    if sys.platform == "darwin" and platform.machine() == "x86_64":
        print("\n!! This Python is an INTEL build running under Rosetta, so it can't use the GPU.")
        print("   Install an Apple Silicon Python (see README > 'Running on the GPU').")
    elif sys.platform == "darwin" and not torch.backends.mps.is_available():
        print("\n!! PyTorch can't use the GPU. Upgrade it:")
        print("   python3 -m pip install --upgrade torch torchaudio")
    elif torch.backends.mps.is_available():
        print("\nOK: models will run on the GPU (MPS).")
    print("-" * 60)


if __name__ == "__main__":
    check_gpu()
    import uvicorn

    url = f"http://localhost:{PORT}"
    print(f"Stem Splitter running at {url}  (press Ctrl+C to stop)")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.main:app", host=HOST, port=PORT)
