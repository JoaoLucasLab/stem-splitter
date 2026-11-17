"""Two-stage separation: RoFormer pulls out vocals, then Demucs 6s splits the instrumental."""
from __future__ import annotations

import logging
import re
import shutil
import threading
from pathlib import Path
from typing import Callable

from . import config

log = logging.getLogger(__name__)

# audio-separator names outputs like "song_(Vocals)_model_bs_roformer....wav"
_STEM_RE = re.compile(r"_\(([^)]+)\)_")

ProgressFn = Callable[[str], None]


def detect_device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:  # torch missing or broken
        pass
    return "cpu"


def _stem_name(filename: str) -> str | None:
    m = _STEM_RE.search(Path(filename).name)
    return m.group(1).strip().lower() if m else None


class StemPipeline:
    """Holds both models in memory. Not thread-safe: call `separate` from one worker only."""

    def __init__(self) -> None:
        self._vocal_sep = None
        self._inst_sep = None
        self._work_dir = config.DATA_DIR / "_work"
        self.ready = threading.Event()
        self.load_error: str | None = None

    def load(self) -> None:
        """Load both models (downloads weights on first run)."""
        try:
            from audio_separator.separator import Separator

            config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
            self._work_dir.mkdir(parents=True, exist_ok=True)

            common = dict(
                log_level=logging.INFO,
                model_file_dir=str(config.MODEL_DIR),
                output_dir=str(self._work_dir),
                output_format="WAV",
            )
            log.info("Loading vocal model %s", config.VOCAL_MODEL)
            mdxc = {"segment_size": 256, "override_model_segment_size": False, "batch_size": None,
                    "overlap": config.VOCAL_OVERLAP, "pitch_shift": 0}
            self._vocal_sep = Separator(**common, mdxc_params=mdxc)
            self._vocal_sep.load_model(model_filename=config.VOCAL_MODEL)

            log.info("Loading instrument model %s", config.INSTRUMENT_MODEL)
            self._inst_sep = Separator(**common)
            self._inst_sep.load_model(model_filename=config.INSTRUMENT_MODEL)

            log.info("Models ready on %s", detect_device())
        except Exception as e:
            self.load_error = f"{type(e).__name__}: {e}"
            log.exception("Model loading failed")
            raise
        finally:
            self.ready.set()

    def _run(self, separator, audio: Path, out_dir: Path) -> dict[str, Path]:
        """Run one separator; move its outputs into out_dir; return {stem: path}."""
        files = separator.separate(str(audio))
        result: dict[str, Path] = {}
        for f in files:
            src = Path(f)
            if not src.is_absolute():
                src = self._work_dir / src
            stem = _stem_name(src.name)
            if stem is None:
                log.warning("Couldn't identify stem for %s, skipping", src.name)
                src.unlink(missing_ok=True)
                continue
            dst = out_dir / f"{stem}.wav"
            shutil.move(str(src), dst)
            result[stem] = dst
        return result

    def separate(self, audio: Path, out_dir: Path, progress: ProgressFn = lambda s: None) -> dict[str, Path]:
        """Split `audio` into the stems in config.STEMS, written as WAVs in out_dir."""
        self.ready.wait()
        if self.load_error:
            raise RuntimeError(f"Models failed to load: {self.load_error}")

        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = out_dir / "_stage1"
        tmp.mkdir(exist_ok=True)

        # Stage 1: vocals / instrumental
        progress("separating_vocals")
        stage1 = self._run(self._vocal_sep, audio, tmp)
        if "vocals" not in stage1 or "instrumental" not in stage1:
            raise RuntimeError(f"Vocal model returned unexpected stems: {sorted(stage1)}")

        # Stage 2: instrumental -> drums / bass / guitar / piano / other
        progress("separating_instruments")
        stage2_dir = tmp / "stage2"
        stage2_dir.mkdir(exist_ok=True)
        stage2 = self._run(self._inst_sep, stage1["instrumental"], stage2_dir)
        # Demucs also emits a "vocals" stem here — it's only leftover bleed, so drop it.
        stage2.pop("vocals", None)

        final: dict[str, Path] = {}
        for stem, path in {**stage1, **stage2}.items():
            if stem not in config.STEMS:
                continue
            dst = out_dir / f"{stem}.wav"
            shutil.move(str(path), dst)
            final[stem] = dst
        shutil.rmtree(tmp, ignore_errors=True)

        missing = [s for s in config.STEMS if s not in final]
        if missing:
            log.warning("Missing stems: %s", missing)
        return {s: final[s] for s in config.STEMS if s in final}
