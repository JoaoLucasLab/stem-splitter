"""Example client: upload a song, wait, download the stems zip.

    python client.py "My Song.mp3"                # saves "My Song_stems.zip"
    python client.py song.mp3 --server http://localhost:8000
"""
import argparse
import sys
import time
from pathlib import Path

import requests


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("song", type=Path)
    p.add_argument("--server", default="http://localhost:8000")
    p.add_argument("--out", type=Path, default=Path("."))
    args = p.parse_args()

    with args.song.open("rb") as f:
        r = requests.post(f"{args.server}/jobs", files={"file": (args.song.name, f)})
    r.raise_for_status()
    job = r.json()
    print(f"Job {job['job_id']} created")

    last = None
    while job["status"] not in ("done", "failed"):
        time.sleep(3)
        job = requests.get(job["status_url"]).json()
        if job["status"] != last:
            extra = f" (position {job['queue_position']})" if job.get("queue_position") else ""
            print(f"  {job['status']}{extra}")
            last = job["status"]

    if job["status"] == "failed":
        sys.exit(f"Failed: {job['error']}")

    args.out.mkdir(parents=True, exist_ok=True)
    dest = args.out / f"{args.song.stem}_stems.zip"
    with requests.get(job["download_url"], stream=True) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    print(f"Done in {job['duration_seconds']}s -> {dest}  (stems: {', '.join(job['stems'])})")


if __name__ == "__main__":
    main()
