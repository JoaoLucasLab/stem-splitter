#!/bin/bash
# One-time setup for Apple Silicon Macs. Run:  bash setup_mac.sh
# Finds (or installs) an Apple Silicon Python, installs the packages into it,
# checks the GPU, and creates start.command (double-click to start the app).
set -u
cd "$(dirname "$0")"

say()  { printf "\n\033[1m==> %s\033[0m\n" "$1"; }
fail() { printf "\n\033[31mERROR: %s\033[0m\n" "$1"; exit 1; }

# 1. Confirm this is an Apple Silicon Mac (works even if Terminal runs under Rosetta)
say "Checking your Mac"
[ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ] || fail "This script is for Apple Silicon Macs (M1/M2/M3...)."
echo "Apple Silicon: $(sysctl -n machdep.cpu.brand_string)"

# 2. Find a native arm64 Python 3.10-3.12
say "Looking for an Apple Silicon Python (3.10 - 3.12)"
is_good_python() {
  arch -arm64 "$1" -c 'import platform,sys; ok = platform.machine()=="arm64" and (3,10) <= sys.version_info[:2] <= (3,12); sys.exit(0 if ok else 1)' 2>/dev/null
}
find_python() {
  for p in /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.10 \
           /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.10/bin/python3; do
    [ -x "$p" ] && is_good_python "$p" && { echo "$p"; return 0; }
  done
  return 1
}
PY="$(find_python)"

if [ -z "$PY" ]; then
  if [ -x /opt/homebrew/bin/brew ]; then
    say "Installing Python 3.11 with Homebrew (Apple Silicon)"
    arch -arm64 /opt/homebrew/bin/brew install python@3.11 || fail "brew install python@3.11 failed."
    PY="$(find_python)"
  fi
fi
if [ -z "$PY" ]; then
  echo
  echo "No Apple Silicon Python found, and Apple Silicon Homebrew isn't installed."
  echo "Install Python 3.11 from https://www.python.org/downloads/macos/"
  echo "(the 'macOS 64-bit universal2 installer'), then run this script again."
  exit 1
fi
echo "Using: $PY ($(arch -arm64 "$PY" --version))"

# 3. ffmpeg (needed to read mp3/m4a)
say "Checking ffmpeg"
if command -v ffmpeg >/dev/null || [ -x /opt/homebrew/bin/ffmpeg ]; then
  echo "ffmpeg found"
elif [ -x /opt/homebrew/bin/brew ]; then
  arch -arm64 /opt/homebrew/bin/brew install ffmpeg || fail "brew install ffmpeg failed."
else
  echo "WARNING: ffmpeg not found. Install it (https://ffmpeg.org or Homebrew) to split mp3/m4a files."
fi

# 4. Install the packages into that Python (no virtual environment)
pip_install() {
  arch -arm64 "$PY" -m pip install --upgrade "$@" \
    || arch -arm64 "$PY" -m pip install --upgrade --break-system-packages "$@"
}
libs_load() {
  arch -arm64 "$PY" -c "import fastapi, uvicorn, multipart; from audio_separator.separator import Separator" 2>/dev/null
}
# Skip pip if everything already loads: audio-separator asks for NumPy 2, and letting
# pip install it can bring back the "numpy.core.multiarray failed to import" error.
if libs_load; then
  say "Packages already installed and loading, skipping pip"
else
  say "Installing packages (this can take a few minutes)"
  pip_install pip >/dev/null 2>&1
  pip_install -r requirements.txt || fail "Package install failed (see messages above)."
fi

# 5. Make sure everything imports (fixes the NumPy 1-vs-2 mismatch if it shows up)
say "Checking the libraries load"
IMPORT_LOG="$(mktemp)"
if ! arch -arm64 "$PY" -c "from audio_separator.separator import Separator" 2>"$IMPORT_LOG"; then
  if grep -qi numpy "$IMPORT_LOG"; then
    echo "NumPy mismatch detected, installing numpy<2"
    pip_install "numpy<2" || fail "Couldn't fix NumPy."
    arch -arm64 "$PY" -c "from audio_separator.separator import Separator" || fail "audio-separator still won't load."
  else
    cat "$IMPORT_LOG"; fail "audio-separator won't load (error above)."
  fi
fi
echo "Libraries OK"

# 6. GPU check
say "Checking the GPU"
arch -arm64 "$PY" -c "
import torch, platform
print('arch:', platform.machine(), '| torch:', torch.__version__, '| MPS available:', torch.backends.mps.is_available())
raise SystemExit(0 if torch.backends.mps.is_available() else 1)
" || fail "PyTorch can't see the GPU. Make sure macOS is 12.3 or newer."

# 7. Double-clickable launcher
cat > start.command <<LAUNCH
#!/bin/bash
cd "\$(dirname "\$0")"
export STEMS_VOCAL_OVERLAP="\${STEMS_VOCAL_OVERLAP:-2}"   # 2 = ~2x faster vocals; delete this line for full quality (4)
exec arch -arm64 "$PY" run.py
LAUNCH
chmod +x start.command setup_mac.sh 2>/dev/null

say "All set!"
echo "Start the app by double-clicking start.command in this folder"
echo "(or run:  ./start.command). It opens http://localhost:8000 and runs on the GPU."
