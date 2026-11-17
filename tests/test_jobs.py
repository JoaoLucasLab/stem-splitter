"""Tests the job queue, stem naming and zip packaging with a fake separator (no models needed).

    python -m unittest tests/test_jobs.py
"""
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

from app import config


class FakeSeparator:
    """Mimics audio-separator: writes '<base>_(Stem)_<model>.wav' into output_dir, returns bare names."""

    def __init__(self, output_dir: Path, stems: list[str], model: str):
        self.output_dir, self.stems, self.model = output_dir, stems, model

    def separate(self, path: str):
        base = Path(path).stem
        names = []
        for s in self.stems:
            n = f"{base}_({s})_{self.model}.wav"
            (self.output_dir / n).write_bytes(b"RIFF" + s.encode())
            names.append(n)
        return names


def make_pipeline(tmp: Path):
    from app.pipeline import StemPipeline

    p = StemPipeline()
    p._work_dir = tmp / "_work"
    p._work_dir.mkdir(parents=True)
    p._vocal_sep = FakeSeparator(p._work_dir, ["Vocals", "Instrumental"], "bs_roformer")
    p._inst_sep = FakeSeparator(p._work_dir, ["Vocals", "Drums", "Bass", "Guitar", "Piano", "Other"], "htdemucs_6s")
    p.ready.set()
    return p


class PipelineTest(unittest.TestCase):
    def test_two_stage_output(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            p = make_pipeline(tmp)
            song = tmp / "song.mp3"
            song.write_bytes(b"x")
            stages = []
            stems = p.separate(song, tmp / "out", stages.append)

            self.assertEqual(list(stems), config.STEMS)
            self.assertEqual(stages, ["separating_vocals", "separating_instruments"])
            # vocals must come from RoFormer, not Demucs's leftover vocals
            self.assertEqual(stems["vocals"].read_bytes(), b"RIFFVocals")
            self.assertTrue(all(f.exists() for f in stems.values()))
            self.assertEqual(sorted(f.name for f in (tmp / "out").iterdir()),
                             sorted(f"{s}.wav" for s in config.STEMS))
            self.assertEqual(list(p._work_dir.iterdir()), [])  # nothing left behind

    def test_load_error_surfaces(self):
        from app.pipeline import StemPipeline
        p = StemPipeline()
        p.load_error = "boom"
        p.ready.set()
        with self.assertRaisesRegex(RuntimeError, "boom"):
            p.separate(Path("x.mp3"), Path(tempfile.mkdtemp()))


class JobManagerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._orig = config.DATA_DIR
        config.DATA_DIR = self.tmp / "data"

    def tearDown(self):
        config.DATA_DIR = self._orig
        self._tmp.cleanup()

    def _wait(self, jm, job_id, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            if jm.get(job_id).status in ("done", "failed"):
                return jm.get(job_id)
            time.sleep(0.02)
        self.fail("job did not finish")

    def _submit(self, jm, name="My Song.mp3"):
        job_id, d = jm.new_job_dir()
        inp = d / f"input_{job_id}.mp3"
        inp.write_bytes(b"x")
        return jm.submit(job_id, d, inp, name)

    def test_job_done_and_zip(self):
        from app.jobs import JobManager
        jm = JobManager(make_pipeline(self.tmp))
        jm.start()
        job = self._wait(jm, self._submit(jm).id)
        self.assertEqual(job.status, "done", job.error)
        with zipfile.ZipFile(job.zip_path) as zf:
            self.assertEqual(sorted(zf.namelist()), sorted(f"My Song - {s}.wav" for s in config.STEMS))
        self.assertFalse(job.input_path.exists())
        self.assertIsNotNone(job.to_dict()["duration_seconds"])
        jm.stop()

    def test_failure_is_reported(self):
        from app.jobs import JobManager

        class Broken:
            def separate(self, *a):
                raise ValueError("bad audio")

        jm = JobManager(Broken())
        jm.start()
        job = self._wait(jm, self._submit(jm).id)
        self.assertEqual(job.status, "failed")
        self.assertIn("bad audio", job.error)
        jm.stop()

    def test_queue_position_and_delete_and_ttl(self):
        from app.jobs import JobManager

        gate = threading.Event()

        class Slow:
            def separate(self, audio, out, progress):
                progress("separating_vocals")
                gate.wait(5)
                out.mkdir(parents=True, exist_ok=True)
                f = out / "vocals.wav"
                f.write_bytes(b"x")
                return {"vocals": f}

        jm = JobManager(Slow())
        jm.start()
        a = self._submit(jm, "a.mp3")
        time.sleep(0.01)
        b = self._submit(jm, "b.mp3")
        time.sleep(0.01)
        c = self._submit(jm, "c.mp3")
        time.sleep(0.1)
        self.assertEqual(a.status, "separating_vocals")
        self.assertEqual(jm.queue_position(b), 1)
        self.assertEqual(jm.queue_position(c), 2)
        with self.assertRaises(RuntimeError):
            jm.delete(a.id)               # running
        self.assertTrue(jm.delete(c.id))  # queued -> removed, worker must skip it
        self.assertFalse(c.dir.exists())
        gate.set()
        self._wait(jm, b.id)
        self.assertIsNone(jm.get(c.id))
        self.assertEqual(jm.cleanup_expired(now=time.time() + config.JOB_TTL_HOURS * 3600 + 1), 2)
        self.assertEqual(jm.list(), [])
        jm.stop()


if __name__ == "__main__":
    unittest.main()
