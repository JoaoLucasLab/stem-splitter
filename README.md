# Stem Splitter

A Python API that splits a song into **vocals, instrumental, drums, bass, guitar, piano and other** in two stages:

1. **BS-RoFormer** separates the vocals from the instrumental. It's the best current model for vocals.
2. **Demucs `htdemucs_6s`** then splits that instrumental into drums, bass, guitar, piano and other.

The API returns a zip of WAV files. Jobs run in the background, so long songs never time out.

## Setup (Mac with Apple Silicon)

**Easiest:** run `bash setup_mac.sh` once, then double-click `start.command` to start the app. The script finds or installs an Apple Silicon Python, installs everything into it and checks that the GPU works.

Manual setup:

```bash
brew install ffmpeg python@3.11
cd stem-splitter
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python3 run.py
```

This starts the server on port 8000 and opens the page in your browser. Change `PORT` at the top of `run.py` to use another port.

- The first start downloads the models (about 1 GB) into `models/`. Check `GET /health` until `models_loaded` is `true`.
- Interactive API docs are at **http://localhost:8000/docs**.
- Run it with **one worker only**, because jobs are kept in memory. Don't use `--workers N`.

## Use it

Open **http://localhost:8000** in your browser. Choose or drag in a song, click **Split song**, then play or download each stem (or all of them as a zip).

From the terminal:

```bash
python client.py "My Song.mp3"          # uploads, waits, saves "My Song_stems.zip"
```

Or with curl:

```bash
curl -F "file=@My Song.mp3" http://localhost:8000/jobs        # -> {"job_id": "...", ...}
curl http://localhost:8000/jobs/<job_id>                       # poll until "status": "done"
curl -o stems.zip http://localhost:8000/jobs/<job_id>/download
```

## Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Device (mps, cuda or cpu), whether the models are loaded, and any load error |
| POST | `/jobs` | Upload a song as the multipart field `file`. Returns `202` with a `job_id` |
| GET | `/jobs/{id}` | Status, which is one of `queued`, `separating_vocals`, `separating_instruments`, `packaging`, `done` or `failed` |
| GET | `/jobs/{id}/download` | Zip with all the stems |
| GET | `/jobs/{id}/stems/{stem}` | A single stem as WAV, such as `/stems/vocals` |
| GET | `/jobs` | List all jobs |
| DELETE | `/jobs/{id}` | Delete a job and its files |

Accepted formats are mp3, wav, flac, m4a, aac, ogg, aiff and opus, up to 200 MB. Songs are processed one at a time, in the order they arrive.

## Running on the GPU

`run.py` prints a check at startup. On an Apple Silicon Mac you want to see `Arch : arm64` and `OK: models will run on the GPU (MPS)`.

- **`Arch : x86_64`**: your Python is an Intel build running under Rosetta. Install an Apple Silicon one, for example with Homebrew installed in `/opt/homebrew` (`brew install python@3.11`) or the macOS installer from python.org. Then reinstall the packages with *that* Python (`python3.11 -m pip install -r requirements.txt`) and start with `python3.11 run.py`.
- **`MPS available=False` on arm64**: run `python3 -m pip install --upgrade torch torchaudio`. MPS also needs macOS 12.3 or newer.

## Settings (environment variables)

| Variable | Default |
|---|---|
| `STEMS_VOCAL_MODEL` | `model_bs_roformer_ep_317_sdr_12.9755.ckpt` |
| `STEMS_VOCAL_OVERLAP` | the model's own setting (4). `2` makes the vocal step about twice as fast, slightly less clean. `start.command` sets it to `2` |
| `STEMS_INSTRUMENT_MODEL` | `htdemucs_6s.yaml` (use `htdemucs_ft.yaml` for 4 cleaner stems with no guitar or piano) |
| `STEMS_DATA_DIR` | `./data` |
| `STEMS_MODEL_DIR` | `./models` |
| `STEMS_MAX_UPLOAD_MB` | `200` |
| `STEMS_JOB_TTL_HOURS` | `24` (finished jobs are deleted after this many hours; `0` keeps them) |

## Tests

```bash
python -m unittest tests.test_jobs -v
```

The tests use a fake separator, so they check the queue, stem naming and zip packaging without downloading any models.

## Notes

- Expect a 4-minute song to take roughly 1–3 minutes on an M-series Mac. The vocal stage is the slow part.
- The guitar and piano stems from `htdemucs_6s` are less clean than drums and bass. That's a limit of the model.
- Jobs live in memory, so restarting the server loses the job list. Files already in `data/` stay on disk.
