"""Guitar and Bass AI Smart Remover: split the guitar or bass out of a song and serve it to the practice player.

writtenBy('Closegamer', 2026)
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from queue import Queue
from statistics import median
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("GH_DATA", ROOT))
TRACKS = DATA / "tracks"
MODEL = "htdemucs_6s"
PORT = 8765
AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".aac"}
AUDIO_MIME = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
    ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".aac": "audio/aac",
}
MAX_UPLOAD = 300 * 1024 * 1024
MAX_FRET = 19
INSTRUMENTS = {
    "guitar": {"stem": "guitar.mp3", "tab": "tab.json", "open": (40, 45, 50, 55, 59, 64)},
    "bass": {"stem": "bass.mp3", "tab": "bass_tab.json", "open": (28, 33, 38, 43)},
}

jobs = {}
prepare_pending = Queue()
preparing = set()


def read_meta(folder):
    try:
        return json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def last_log_line(path):
    try:
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="replace").replace("\r", "\n").splitlines()]
        return next((ln for ln in reversed(lines) if ln), "")
    except OSError:
        return ""


def analyse_pitch(bends):
    """Best-effort vibrato and bend flags from Basic Pitch's per-frame contour."""
    if not bends or len(bends) < 6:
        return {}
    values = [float(bend) / 3.0 for bend in bends]  # contour bins are thirds of a semitone
    count = len(values)
    edge = max(1, min(count // 4, 20))
    net = median(values[-edge:]) - median(values[:edge])
    flags = {}
    mid = values[count // 4:3 * count // 4] or values
    if len(mid) >= 10:
        ordered = sorted(mid)
        low = ordered[int(len(ordered) * 0.1)]
        high = ordered[int(len(ordered) * 0.9)]
        centre = median(mid)
        signs = [1 if v > centre + 0.1 else -1 if v < centre - 0.1 else 0 for v in mid]
        changes = sum(1 for a, b in zip(signs, signs[1:]) if a and b and a != b)
        if high - low >= 0.6 and changes >= 4:
            flags["vib"] = True
    if not flags.get("vib") and abs(net) >= 1.0:
        flags["bend"] = "up" if net > 0 else "down"
    return flags


def build_tab(note_events, open_strings, string_penalty=2.0):
    """Map transcribed pitches to string/fret positions, preferring little hand movement."""
    notes = []
    for start, end, pitch, _, bends in note_events:
        pitch = int(pitch)
        start = float(start)
        if min(open_strings) <= pitch <= max(open_strings) + MAX_FRET:
            notes.append((start, max(0.08, float(end) - start), pitch, bends))
    notes.sort()

    result = []
    prev_fret, prev_string = 3, 1
    index = 0
    while index < len(notes):
        group = [notes[index]]
        index += 1
        while index < len(notes) and notes[index][0] - group[0][0] < 0.06:
            group.append(notes[index])
            index += 1
        seen = set()
        used_strings = set()
        for start, duration, pitch, bends in group:
            if pitch in seen:
                continue
            seen.add(pitch)
            options = []
            for string, open_pitch in enumerate(open_strings):
                fret = pitch - open_pitch
                if 0 <= fret <= MAX_FRET:
                    cost = abs(fret - prev_fret) + string_penalty * abs(string - prev_string) + fret * 0.02
                    if string in used_strings:
                        cost += 30
                    options.append((cost, string, fret))
            if not options:
                continue
            _, string, fret = min(options)
            used_strings.add(string)
            prev_fret, prev_string = fret, string
            note = {
                "t": round(start, 3), "d": round(duration, 3), "p": pitch,
                "s": string + 1, "f": fret,
            }
            note.update(analyse_pitch(bends))
            result.append(note)

    run_start = 0
    while run_start < len(result):
        run_end = run_start + 1
        while (run_end < len(result)
               and result[run_end]["p"] == result[run_end - 1]["p"]
               and result[run_end]["s"] == result[run_end - 1]["s"]
               and result[run_end]["t"] - result[run_end - 1]["t"] <= 0.18):
            run_end += 1
        if run_end - run_start >= 4:
            for note in result[run_start:run_end]:
                note["trem"] = True
        run_start = run_end

    by_string = {}
    for note in result:
        by_string.setdefault(note["s"], []).append(note)
    for string_notes in by_string.values():
        for first, second in zip(string_notes, string_notes[1:]):
            gap = second["t"] - (first["t"] + first["d"])
            delta = second["p"] - first["p"]
            if (0 <= gap <= 0.12 and 0 < abs(delta) <= 7
                    and not first.get("trem") and not second.get("trem")):
                second["slide"] = "up" if delta > 0 else "down"

    return result



def transcribe_bass(path):
    """Monophonic bass line from the isolated stem: pyin pitch plus stem onsets, one note at a time."""
    import numpy as np
    import librosa
    from scipy.ndimage import median_filter

    sample_rate = 22050
    hop = 512
    frame = 4096
    audio, _ = librosa.load(str(path), sr=sample_rate, mono=True)
    f0, voiced, probability = librosa.pyin(
        audio, fmin=30, fmax=300, sr=sample_rate,
        frame_length=frame, hop_length=hop, fill_na=np.nan,
    )
    rms = librosa.feature.rms(y=audio, frame_length=frame, hop_length=hop)[0][:len(f0)]
    finite = np.isfinite(f0)
    midi = np.full(len(f0), np.nan)
    midi[finite] = librosa.hz_to_midi(f0[finite])
    gate = finite & voiced & (probability >= 0.6) & (rms > max(0.004, float(np.median(rms)) * 0.35))
    if int(gate.sum()) < 10:
        return []

    filled = np.interp(np.arange(len(midi)), np.flatnonzero(finite), midi[finite])
    smooth = median_filter(filled, size=5)
    pitch = np.where(gate, np.round(smooth).astype(int), -1)

    segments = []
    start = None
    current = -1
    for index, value in enumerate(pitch):
        if value < 0:
            if start is not None:
                segments.append([start, index, current])
                start = None
                current = -1
        elif start is None:
            start, current = index, value
        elif value != current:
            segments.append([start, index, current])
            start, current = index, value
    if start is not None:
        segments.append([start, len(pitch), current])

    merged = []
    for segment in segments:
        if merged and segment[2] == merged[-1][2] and segment[0] - merged[-1][1] <= 4:
            merged[-1][1] = segment[1]
        else:
            merged.append(segment)

    onset_env = librosa.onset.onset_strength(y=audio, sr=sample_rate, hop_length=hop)
    onsets = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sample_rate, hop_length=hop,
        units="frames", backtrack=True, delta=0.03, wait=4,
    )
    strong = sorted(int(index) for index in onsets if index < len(pitch) and onset_env[index] >= 0.15)

    min_gap = int(0.15 * sample_rate / hop)
    pieces = []
    for start_frame, end_frame, midi_note in merged:
        cuts = [start_frame]
        for frame_index in strong:
            if start_frame + 3 < frame_index < end_frame - 2 and frame_index - cuts[-1] >= min_gap:
                cuts.append(frame_index)
        cuts.append(end_frame)
        for begin, finish in zip(cuts, cuts[1:]):
            pieces.append([begin, finish, midi_note])

    min_frames = int(round(0.08 * sample_rate / hop))
    events = []
    for start_frame, end_frame, midi_note in pieces:
        if end_frame - start_frame < min_frames or not 28 <= midi_note <= 64:
            continue
        confidence = probability[start_frame:end_frame]
        events.append((
            start_frame * hop / sample_rate,
            end_frame * hop / sample_rate,
            int(midi_note),
            float(np.nanmedian(confidence)) if len(confidence) else 0.0,
            None,
        ))

    events.sort()
    clean = []
    for event in events:
        if clean and event[0] - clean[-1][0] < 0.09:
            if event[1] - event[0] > clean[-1][1] - clean[-1][0]:
                clean[-1] = event
            continue
        clean.append(event)
    return clean


def write_tab(folder, instrument):
    import logging
    logging.disable(logging.WARNING)  # basic-pitch warns about its unused runtimes
    config = INSTRUMENTS[instrument]
    if instrument == "bass":
        events = transcribe_bass(folder / config["stem"])
    else:
        from basic_pitch.inference import predict
        _, _, events = predict(str(folder / config["stem"]))
    notes = build_tab(events, config["open"], 0.35 if instrument == "bass" else 2.0)
    (folder / config["tab"]).write_text(json.dumps(notes), encoding="utf-8")
    return len(notes)


def clean_tracks():
    """Delete every saved song folder. Returns (removed, errors)."""
    removed = 0
    errors = []
    if TRACKS.is_dir():
        for folder in TRACKS.iterdir():
            if not folder.is_dir():
                continue
            try:
                shutil.rmtree(folder)
                removed += 1
            except OSError as exc:
                errors.append(f"{folder.name}: {exc}")
    return removed, errors


def track_status(track_id):
    folder = TRACKS / track_id
    if not folder.is_dir():
        return None
    job = jobs.get(track_id)
    if job and job.get("status") in {"queued", "running", "error"}:
        return job
    if any((folder / config["stem"]).exists() for config in INSTRUMENTS.values()):
        return {"status": "done"}
    return job or {"status": "error", "error": "processing was interrupted; load the song again"}


def prepare_worker():
    """Cut only the requested instrument, build its play-along mix and write its tab."""
    while True:
        track_id, instrument = prepare_pending.get()
        folder = TRACKS / track_id
        config = INSTRUMENTS[instrument]
        job = jobs.setdefault(track_id, {})
        job.update(status="running", stage=f"stem-{instrument}", started=time.time())
        work_dir = folder / f"demucs-{instrument}"
        try:
            backing = folder / f"no_{instrument}.mp3"
            if not (folder / config["stem"]).exists() or not backing.exists():
                original = next(folder.glob("original.*"), None)
                if original is None:
                    raise RuntimeError("original upload is missing")
                log_path = folder / f"{instrument}_extract.log"
                log = log_path.open("w", encoding="utf-8", errors="replace")
                try:
                    result = subprocess.run(
                        [sys.executable, "-m", "demucs.separate", "-n", MODEL,
                         "--two-stems", instrument, "--mp3",
                         "-o", str(work_dir), str(original)],
                        stdout=log, stderr=subprocess.STDOUT,
                    )
                finally:
                    log.close()
                stem = next((work_dir / MODEL).glob(f"*/{config['stem']}"), None)
                no_stem = next((work_dir / MODEL).glob(f"*/no_{instrument}.mp3"), None)
                if result.returncode != 0 or stem is None or no_stem is None:
                    detail = last_log_line(log_path)
                    raise RuntimeError(detail or f"demucs exited with code {result.returncode}")
                if not (folder / config["stem"]).exists():
                    shutil.move(str(stem), folder / config["stem"])
                shutil.move(str(no_stem), backing)

            if not (folder / config["tab"]).exists():
                job.update(stage=f"tab-{instrument}")
                write_tab(folder, instrument)
            job.update(status="done", stage="done", finished=time.time())
        except Exception as exc:
            (folder / f"{instrument}_prepare_error.txt").write_text(
                f"{type(exc).__name__}: {exc}", encoding="utf-8")
            job.update(status="error", stage="error", error=str(exc)[:600])
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
            preparing.discard((track_id, instrument))


class Handler(BaseHTTPRequestHandler):
    server_version = "GuitarAndBassAISmartRemover/1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self.send_file(ROOT / "index.html", "text/html; charset=utf-8")
        match = re.fullmatch(r"/vendor/([A-Za-z0-9._-]+)", path)
        if match:
            file = ROOT / "vendor" / match.group(1)
            if file.is_file():
                return self.send_file(file, "text/javascript; charset=utf-8")
        if path == "/api/tracks":
            tracks = []
            if TRACKS.is_dir():
                folders = sorted(TRACKS.glob("*/"), key=lambda p: p.stat().st_mtime, reverse=True)
                for folder in folders:
                    meta = read_meta(folder)
                    if meta:
                        tracks.append({
                            "id": folder.name,
                            "name": meta.get("name", folder.name),
                            "ready": any((folder / c["stem"]).exists() for c in INSTRUMENTS.values()),
                            "has_tab": any((folder / c["tab"]).exists() for c in INSTRUMENTS.values()),
                        })
            return self.send_json({"tracks": tracks})
        match = re.fullmatch(r"/api/tracks/([0-9a-f]{8})", path)
        if match:
            status = track_status(match.group(1))
            return self.send_json(status, 200 if status else 404)
        match = re.fullmatch(r"/media/([0-9a-f]{8})/(guitar|bass|no_guitar|no_bass)\.mp3", path)
        if match:
            file = TRACKS / match.group(1) / f"{match.group(2)}.mp3"
            if file.is_file():
                return self.send_file(file, "audio/mpeg")
        match = re.fullmatch(r"/media/([0-9a-f]{8})/(tab|bass_tab)\.json", path)
        if match:
            file = TRACKS / match.group(1) / f"{match.group(2)}.json"
            if file.is_file():
                return self.send_file(file, "application/json")
        match = re.fullmatch(r"/media/([0-9a-f]{8})/original", path)
        if match:
            folder = TRACKS / match.group(1)
            original = next(folder.glob("original.*"), None)
            if original and original.is_file():
                return self.send_file(original, AUDIO_MIME.get(original.suffix.lower(), "application/octet-stream"))
        self.send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path != "/api/tracks":
            return self.send_json({"error": "not found"}, 404)
        active = [track_id for track_id, job in jobs.items() if job.get("status") in {"queued", "running"}]
        if active:
            return self.send_json({"error": "a song is still being processed; wait for it to finish"}, 409)
        removed, errors = clean_tracks()
        if errors:
            return self.send_json({"error": "; ".join(errors)[:600], "removed": removed}, 500)
        jobs.clear()
        preparing.clear()
        self.send_json({"removed": removed})

    def do_POST(self):
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/tracks/([0-9a-f]{8})/prepare/(guitar|bass)", path)
        if match:
            track_id, instrument = match.groups()
            folder = TRACKS / track_id
            if not folder.is_dir():
                return self.send_json({"error": "track not found"}, 404)
            config = INSTRUMENTS[instrument]
            if ((folder / config["stem"]).exists()
                    and (folder / config["tab"]).exists()
                    and (folder / f"no_{instrument}.mp3").exists()):
                return self.send_json({"status": "done"})
            key = (track_id, instrument)
            if key in preparing:
                return self.send_json({"status": "queued"})
            preparing.add(key)
            jobs.setdefault(track_id, {}).update(status="queued", stage=f"stem-{instrument}")
            prepare_pending.put(key)
            return self.send_json({"status": "queued"})
        if path != "/api/tracks":
            return self.send_json({"error": "not found"}, 404)
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_UPLOAD:
            return self.send_json({"error": "missing file or file too large"}, 400)
        name = Path(unquote(self.headers.get("X-Filename", ""))).name or "track"
        ext = Path(name).suffix.lower()
        if ext not in AUDIO_EXT:
            return self.send_json({"error": f"unsupported audio type '{ext}'"}, 400)

        track_id = uuid.uuid4().hex[:8]
        folder = TRACKS / track_id
        folder.mkdir(parents=True)
        dest = folder / ("original" + ext)
        remaining = length
        try:
            with dest.open("wb") as file:
                while remaining:
                    chunk = self.rfile.read(min(1 << 20, remaining))
                    if not chunk:
                        raise ConnectionError("upload interrupted")
                    file.write(chunk)
                    remaining -= len(chunk)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        (folder / "meta.json").write_text(json.dumps({"name": name, "created": time.time()}), encoding="utf-8")
        instrument = self.headers.get("X-Instrument", "guitar")
        if instrument not in INSTRUMENTS:
            instrument = "guitar"
        key = (track_id, instrument)
        preparing.add(key)
        jobs[track_id] = {"status": "queued", "stage": f"stem-{instrument}"}
        prepare_pending.put(key)
        self.send_json({"id": track_id})

    def send_json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type):
        try:
            size = path.stat().st_size
        except OSError:
            return self.send_json({"error": "not found"}, 404)
        start, end = 0, size - 1
        header = self.headers.get("Range")
        if header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
            if match and (match.group(1) or match.group(2)):
                if match.group(1):
                    start = int(match.group(1))
                    end = int(match.group(2) or size - 1)
                else:
                    start = max(0, size - int(match.group(2)))
                if start >= size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                end = min(end, size - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
        else:
            self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with path.open("rb") as file:
                file.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = file.read(min(1 << 16, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    TRACKS.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=prepare_worker, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Guitar and Bass AI Smart Remover -> http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
