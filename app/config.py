"""Settings, read from environment variables (all optional)."""
import os
from pathlib import Path

# Let PyTorch fall back to CPU for any op Apple's MPS backend doesn't support yet.
# Must be set before torch is imported.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

BASE_DIR = Path(__file__).resolve().parent.parent

# Where uploads, stems and zips are stored (one sub-folder per job).
DATA_DIR = Path(os.getenv("STEMS_DATA_DIR", BASE_DIR / "data")).resolve()

# Where model weights are cached (downloaded automatically on first run, ~1 GB total).
MODEL_DIR = Path(os.getenv("STEMS_MODEL_DIR", BASE_DIR / "models")).resolve()

# Stage 1: vocals vs. instrumental.
VOCAL_MODEL = os.getenv("STEMS_VOCAL_MODEL", "model_bs_roformer_ep_317_sdr_12.9755.ckpt")

# How many overlapping passes the vocal model makes over each part of the song.
# Unset = the model's own setting (4 for the default model). 2 is about twice as fast, slightly less clean.
VOCAL_OVERLAP = int(os.getenv("STEMS_VOCAL_OVERLAP")) if os.getenv("STEMS_VOCAL_OVERLAP") else None

# Stage 2: split the instrumental into drums / bass / guitar / piano / other.
INSTRUMENT_MODEL = os.getenv("STEMS_INSTRUMENT_MODEL", "htdemucs_6s.yaml")

# Max upload size in MB.
MAX_UPLOAD_MB = int(os.getenv("STEMS_MAX_UPLOAD_MB", "200"))

# Finished/failed jobs (and their files) are deleted after this many hours. 0 = never.
JOB_TTL_HOURS = float(os.getenv("STEMS_JOB_TTL_HOURS", "24"))

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".aiff", ".aif", ".opus"}

# Final stems returned to the client, in order.
STEMS = ["vocals", "instrumental", "drums", "bass", "guitar", "piano", "other"]
