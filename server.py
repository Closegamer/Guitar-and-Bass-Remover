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
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from queue import Queue
from statistics import median
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("GH_DATA", ROOT))
TRACKS = DATA / "tracks"
SETTINGS = DATA / "settings.json"
MODEL = "htdemucs_6s"
PORT = 8765
AI_DEFAULTS = {
    "provider": "included",
    "claude_key": "",
    "claude_model": "claude-sonnet-4-5",
    "local_url": "http://127.0.0.1:11434",
    "local_model": "",
    "local_key": "",
}
QUALITY = {"fast": ("0", "0.25"), "balanced": ("1", "0.25"), "best": ("5", "0.5")}
RU_MESSAGES = {
    "not found": "Не найдено",
    "track not found": "Песня не найдена",
    "a song is still being processed; wait for it to finish": "Песня ещё обрабатывается; дождитесь завершения",
    "missing file or file too large": "Файл не выбран или слишком большой",
    "original upload is missing": "Исходный файл песни не найден",
    "could not detect the tempo of this song": "Не удалось определить темп этой песни",
    "could not detect the beats of this song": "Не удалось определить доли этой песни",
    "the model did not return JSON": "Модель не вернула JSON",
    "choose Claude or a local LLM in Models first": "Сначала выберите Claude или локальную LLM в разделе «Модели»",
    "there is no included tab to restore": "Нет встроенных табов для восстановления",
    "invalid model name": "Недопустимое имя модели",
    "this instrument is already being cut": "Эта партия уже вырезается",
    "Claude API key is not set": "API-ключ Claude не задан",
    "no model selected": "Модель не выбрана",
    "the included models do not use a chat API": "Встроенные модели не используют чат-API",
    "processing was interrupted; load the song again": "Обработка прервана; загрузите песню снова",
    "The included models run locally and need no connection.": "Встроенные модели работают локально и не требуют подключения.",
}
RU_PREFIXES = {
    "Connected. Model replied:": "Подключено. Ответ модели:",
    "unsupported audio type": "неподдерживаемый тип аудио",
    "demucs exited with code": "Demucs завершился с кодом",
}


def localize_ru(text):
    if text in RU_MESSAGES:
        return RU_MESSAGES[text]
    for prefix, translated in RU_PREFIXES.items():
        if text.startswith(prefix):
            return translated + text[len(prefix):]
    match = re.match(r"the (.+) model has no guitar stem; use (.+)", text)
    if match:
        return f"у модели {match.group(1)} нет отдельной партии гитары; используйте {match.group(2)}"
    return text
DEMUCS_REPOS = {
    "models--adefossez--HTDemucs-6s": "htdemucs_6s",
    "models--adefossez--HTDemucs": "htdemucs",
    "models--adefossez--HTDemucs-FT": "htdemucs_ft",
    "models--adefossez--HDemucs_mmi": "hdemucs_mmi",
}
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


def read_settings():
    settings = AI_DEFAULTS.copy()
    try:
        saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            settings.update(saved)
    except (OSError, ValueError):
        pass
    return settings


def write_settings(settings):
    SETTINGS.write_text(json.dumps(settings, indent=1), encoding="utf-8")


def http_json(request, timeout):
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def openai_root(url):
    base = (url or AI_DEFAULTS["local_url"]).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[:-len("/chat/completions")].rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base


def openai_chat_url(url):
    base = (url or AI_DEFAULTS["local_url"]).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if not urlparse(base).path.strip("/"):
        return base + "/v1/chat/completions"
    return base + "/chat/completions"


def openai_models_url(url):
    base = (url or AI_DEFAULTS["local_url"]).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[:-len("/chat/completions")].rstrip("/")
    if not urlparse(base).path.strip("/"):
        return base + "/v1/models"
    return base + "/models"


def local_models(url, key=""):
    """Names of models served by Ollama or any OpenAI-compatible server, local or cloud."""
    headers = {"authorization": f"Bearer {key}"} if key else {}
    try:
        data = http_json(urllib.request.Request(openai_root(url) + "/api/tags", headers=headers), 1.5)
        names = [item.get("name") for item in data.get("models", []) if item.get("name")]
        if names:
            return names
    except (OSError, ValueError, RuntimeError):
        pass
    try:
        data = http_json(urllib.request.Request(openai_models_url(url), headers=headers), 1.5)
        return [item.get("id") for item in data.get("data", []) if item.get("id")]
    except (OSError, ValueError, RuntimeError):
        return []


def ai_chat(settings, prompt, max_tokens=1024):
    """One prompt to Claude or to an OpenAI-compatible model, local or cloud. Returns plain text."""
    provider = settings.get("provider", "included")
    if provider == "claude":
        key = (settings.get("claude_key") or "").strip()
        if not key:
            raise RuntimeError("Claude API key is not set")
        body = json.dumps({
            "model": settings.get("claude_model") or AI_DEFAULTS["claude_model"],
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={
                "content-type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
        )
        data = http_json(request, 120)
        return "".join(part.get("text", "") for part in data.get("content", [])
                       if part.get("type") == "text")
    if provider == "local":
        model = (settings.get("local_model") or "").strip()
        if not model:
            raise RuntimeError("no model selected")
        headers = {"content-type": "application/json"}
        key = (settings.get("local_key") or "").strip()
        if key:
            headers["authorization"] = f"Bearer {key}"
            headers["api-key"] = key
        body = json.dumps({
            "model": model, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        request = urllib.request.Request(
            openai_chat_url(settings.get("local_url")), data=body, headers=headers,
        )
        data = http_json(request, 240)
        choices = data.get("choices") or []
        return (choices[0].get("message", {}).get("content", "") if choices else "")
    raise RuntimeError("the included models do not use a chat API")


def tab_backup(folder, instrument):
    return folder / f"{Path(INSTRUMENTS[instrument]['tab']).stem}.included.json"


def strip_json(text):
    text = (text or "").strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if match:
        text = match.group(1).strip()
    end = text.rfind("]")
    if end < 0:
        raise RuntimeError("the model did not return JSON")
    for start in reversed([item.start() for item in re.finditer(r"\[", text[:end])]):
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            continue
        if isinstance(data, list):
            return data
    raise RuntimeError("the model did not return JSON")


def refine_notes(notes, instrument, first, last):
    """Ask the chosen AI to re-finger the notes in one tab window, keeping pitch and timing."""
    settings = read_settings()
    config = INSTRUMENTS[instrument]
    opens = config["open"]
    picked = [note for note in notes if first <= note["t"] <= last][:120]
    if not picked:
        return 0
    listing = [{"i": index, "p": note["p"], "s": note.get("s"), "f": note.get("f")}
               for index, note in enumerate(picked)]
    prompt = (
        f"You are an expert {instrument} tab arranger. Open-string MIDI pitches, "
        f"thinnest string first: {list(opens)}. Strings are numbered 1 to {len(opens)}. "
        f"Frets run from 0 to {MAX_FRET}. The notes below were detected from audio; "
        "their timing and pitch are correct and must not change. Choose a string s and "
        "fret f for each note so that pitch = open[s-1] + f, the part is comfortable, "
        "and the hand moves little. Return ONLY a JSON array such as "
        '[{"i":0,"s":1,"f":3}] with one entry per note. Notes: '
        + json.dumps(listing)
    )
    fixes = strip_json(ai_chat(settings, prompt, 4096))
    by_index = {}
    for item in fixes:
        if isinstance(item, dict) and isinstance(item.get("i"), int):
            by_index[item["i"]] = item
    changed = 0
    for index, note in enumerate(picked):
        fix = by_index.get(index)
        if not fix:
            continue
        try:
            string, fret = int(fix["s"]), int(fix["f"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (1 <= string <= len(opens) and 0 <= fret <= MAX_FRET):
            continue
        if opens[string - 1] + fret != note["p"]:
            continue
        if note.get("s") != string or note.get("f") != fret:
            note["s"], note["f"] = string, fret
            changed += 1
    return changed


def separation_models():
    found = {MODEL: {"name": MODEL, "bundled": True, "installed": True}}
    for root in (DATA / "models" / "hub", Path.home() / ".cache" / "huggingface" / "hub"):
        if not root.is_dir():
            continue
        for folder in root.iterdir():
            name = DEMUCS_REPOS.get(folder.name)
            if name:
                found.setdefault(name, {"name": name, "bundled": False, "installed": True})
    return list(found.values())


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


def detect_tempo(folder):
    """Estimate the tempo, every beat, and the Demucs file delay once with librosa."""
    meta = read_meta(folder)
    if not meta:
        meta = {"name": folder.name, "created": time.time()}
    cached = meta.get("tempo")
    if (isinstance(cached, (int, float)) and 30 <= cached <= 300
            and isinstance(meta.get("beats"), list) and meta["beats"]
            and "demucs_offset" in meta):
        return {
            "bpm": float(cached),
            "offset": float(meta.get("beat_offset") or 0),
            "beats": meta["beats"],
            "demucs_offset": float(meta.get("demucs_offset") or 0),
        }
    original = next(folder.glob("original.*"), None)
    if original is None:
        raise RuntimeError("original upload is missing")
    import numpy as np
    import librosa

    audio, rate = librosa.load(str(original), sr=22050, mono=True)
    tempo, beats = librosa.beat.beat_track(y=audio, sr=rate, hop_length=256)
    tempo = float(np.atleast_1d(tempo)[0])
    if not 30 <= tempo <= 300:
        raise RuntimeError("could not detect the tempo of this song")
    raw = librosa.frames_to_time(beats, sr=rate, hop_length=256)
    if len(raw) < 2:
        raise RuntimeError("could not detect the beats of this song")
    intervals = np.diff(raw)
    intervals = intervals[(intervals > 0.15) & (intervals < 3.0)]
    if len(intervals):
        median_bpm = 60.0 / float(np.median(intervals))
        span = float(raw[-1] - raw[0])
        if span > 0:
            span_bpm = 60.0 * (len(raw) - 1) / span
            if 30 <= span_bpm <= 300 and abs(span_bpm - median_bpm) <= 0.05 * median_bpm:
                tempo = span_bpm
    while tempo < 50:
        tempo *= 2
    while tempo > 200:
        tempo /= 2
    bpm = round(tempo, 1)
    times = [round(float(value), 3) for value in raw]
    demucs_offset = 0.0
    reference = next((folder / name for name in (
        "no_guitar.mp3", "no_bass.mp3", "guitar.mp3", "bass.mp3")
        if (folder / name).is_file()), None)
    if reference is not None:
        try:
            ref_audio, _ = librosa.load(str(reference), sr=rate, mono=True, duration=60)
            limit = min(len(audio), len(ref_audio), rate * 60)
            env_a = librosa.onset.onset_strength(y=audio[:limit], sr=rate, hop_length=256)
            env_b = librosa.onset.onset_strength(y=ref_audio[:limit], sr=rate, hop_length=256)
            count = min(len(env_a), len(env_b))
            if count > 20:
                left = env_a[:count] - env_a[:count].mean()
                right = env_b[:count] - env_b[:count].mean()
                best_lag, best_corr = 0, -2.0
                for lag in range(-13, 14):
                    if lag >= 0:
                        first, second = left[lag:], right[:count - lag]
                    else:
                        first, second = left[:lag], right[-lag:]
                    if len(first) < 10:
                        continue
                    corr = float(np.dot(first, second) / (
                        np.linalg.norm(first) * np.linalg.norm(second) + 1e-9))
                    if corr > best_corr:
                        best_corr, best_lag = corr, lag
                if best_corr >= 0.2:
                    demucs_offset = round(-best_lag * 256 / rate, 3)
        except Exception:
            demucs_offset = 0.0
    meta["tempo"] = bpm
    meta["beat_offset"] = times[0]
    meta["beats"] = times
    meta["demucs_offset"] = demucs_offset
    (folder / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "bpm": bpm, "offset": times[0], "beats": times,
        "demucs_offset": demucs_offset,
    }


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
    target = folder / config["tab"]
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(notes), encoding="utf-8")
    os.replace(temp, target)
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
        track_id, instrument, model, quality, force = prepare_pending.get()
        folder = TRACKS / track_id
        config = INSTRUMENTS[instrument]
        job = jobs.setdefault(track_id, {})
        job.update(status="running", stage=f"stem-{instrument}", model=model,
                   quality=quality, started=time.time())
        work_dir = folder / f"demucs-{instrument}"
        try:
            backing = folder / f"no_{instrument}.mp3"
            if force or not (folder / config["stem"]).exists() or not backing.exists():
                original = next(folder.glob("original.*"), None)
                if original is None:
                    raise RuntimeError("original upload is missing")
                log_path = folder / f"{instrument}_extract.log"
                log = log_path.open("w", encoding="utf-8", errors="replace")
                try:
                    result = subprocess.run(
                        [sys.executable, "-m", "demucs.separate", "-n", model,
                         "--two-stems", instrument, "--mp3",
                         "--shifts", QUALITY.get(quality, QUALITY["balanced"])[0],
                         "--overlap", QUALITY.get(quality, QUALITY["balanced"])[1],
                         "-o", str(work_dir), str(original)],
                        stdout=log, stderr=subprocess.STDOUT,
                    )
                finally:
                    log.close()
                stem = next((work_dir / model).glob(f"*/{config['stem']}"), None)
                no_stem = next((work_dir / model).glob(f"*/no_{instrument}.mp3"), None)
                if result.returncode != 0 or stem is None or no_stem is None:
                    detail = last_log_line(log_path)
                    if model != MODEL and instrument == "guitar":
                        detail = f"the {model} model has no guitar stem; use {MODEL}"
                    raise RuntimeError(detail or f"demucs exited with code {result.returncode}")
                os.replace(stem, folder / config["stem"])
                os.replace(no_stem, backing)
                if force:
                    tab_backup(folder, instrument).unlink(missing_ok=True)
                meta = read_meta(folder)
                if meta is not None:
                    meta.setdefault("models", {})[instrument] = {"name": model, "quality": quality}
                    (folder / "meta.json").write_text(
                        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

            if force or not (folder / config["tab"]).exists():
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
                            "models": meta.get("models", {}),
                        })
            return self.send_json({"tracks": tracks})
        if path == "/api/models":
            settings = read_settings()
            query = parse_qs(urlparse(self.path).query)
            probe = query.get("probe", ["0"])[0] == "1"
            return self.send_json({
                "ai": {
                    "provider": settings.get("provider", "included"),
                    "claude_model": settings.get("claude_model", ""),
                    "claude_key_set": bool(settings.get("claude_key")),
                    "local_url": settings.get("local_url", ""),
                    "local_model": settings.get("local_model", ""),
                    "local_key_set": bool(settings.get("local_key")),
                },
                "local_models": local_models(settings.get("local_url"), settings.get("local_key")) if probe else [],
                "separation": separation_models(),
                "included": MODEL,
            })
        match = re.fullmatch(r"/api/tracks/([0-9a-f]{8})/tempo", path)
        if match:
            folder = TRACKS / match.group(1)
            if not folder.is_dir():
                return self.send_json({"error": "track not found"}, 404)
            try:
                return self.send_json(detect_tempo(folder))
            except Exception as exc:
                return self.send_json({"error": str(exc)[:300]}, 502)
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
        query = parse_qs(urlparse(self.path).query)
        if path == "/api/models":
            payload = self.read_json()
            settings = read_settings()
            provider = payload.get("provider", settings.get("provider", "included"))
            if provider not in {"included", "claude", "local"}:
                return self.send_json({"error": "unknown provider"}, 400)
            settings["provider"] = provider
            settings["claude_model"] = str(payload.get("claude_model", settings.get("claude_model", "")))[:120]
            settings["local_url"] = str(payload.get("local_url", settings.get("local_url", "")))[:300]
            settings["local_model"] = str(payload.get("local_model", settings.get("local_model", "")))[:200]
            key = payload.get("claude_key")
            if isinstance(key, str) and key.strip():
                settings["claude_key"] = key.strip()
            if payload.get("clear_key"):
                settings["claude_key"] = ""
            local_key = payload.get("local_key")
            if isinstance(local_key, str) and local_key.strip():
                settings["local_key"] = local_key.strip()
            if payload.get("clear_local_key"):
                settings["local_key"] = ""
            write_settings(settings)
            return self.send_json({
                "ok": True,
                "claude_key_set": bool(settings.get("claude_key")),
                "local_key_set": bool(settings.get("local_key")),
            })
        if path == "/api/models/test":
            payload = self.read_json()
            settings = read_settings()
            settings.update({
                "provider": payload.get("provider", settings.get("provider")),
                "claude_model": payload.get("claude_model", settings.get("claude_model")),
                "local_url": payload.get("local_url", settings.get("local_url")),
                "local_model": payload.get("local_model", settings.get("local_model")),
            })
            if isinstance(payload.get("claude_key"), str) and payload["claude_key"].strip():
                settings["claude_key"] = payload["claude_key"].strip()
            if isinstance(payload.get("local_key"), str) and payload["local_key"].strip():
                settings["local_key"] = payload["local_key"].strip()
            if settings.get("provider") == "included":
                return self.send_json({"ok": True, "message": "The included models run locally and need no connection."})
            models = (local_models(settings.get("local_url"), settings.get("local_key"))
                      if settings.get("provider") == "local" else [])
            try:
                answer = ai_chat(settings, "Reply with the single word OK.", 16).strip()
            except Exception as exc:
                return self.send_json({"error": str(exc)[:400], "models": models}, 502)
            return self.send_json({
                "ok": True,
                "message": f"Connected. Model replied: {answer[:80] or 'OK'}",
                "models": models,
            })
        match = re.fullmatch(r"/api/tracks/([0-9a-f]{8})/ai_tab/(guitar|bass)", path)
        if match:
            track_id, instrument = match.groups()
            folder = TRACKS / track_id
            settings = read_settings()
            if not folder.is_dir():
                return self.send_json({"error": "track not found"}, 404)
            config = INSTRUMENTS[instrument]
            target = folder / config["tab"]
            backup = tab_backup(folder, instrument)
            try:
                payload = self.read_json()
                if payload.get("restore"):
                    if not backup.is_file():
                        return self.send_json({"error": "there is no included tab to restore"}, 404)
                    os.replace(backup, target)
                    return self.send_json({"restored": True})
                if settings.get("provider", "included") == "included":
                    return self.send_json({"error": "choose Claude or a local LLM in Models first"}, 400)
                notes = json.loads(target.read_text(encoding="utf-8"))
                if not backup.exists():
                    shutil.copy2(target, backup)
                try:
                    changed = refine_notes(
                        notes, instrument,
                        float(payload.get("from", 0)), float(payload.get("to", 1e9)),
                    )
                except Exception:
                    if backup.is_file() and not target.exists():
                        shutil.copy2(backup, target)
                    raise
                temp = target.with_suffix(".tmp")
                temp.write_text(json.dumps(notes), encoding="utf-8")
                os.replace(temp, target)
                return self.send_json({"changed": changed, "provider": settings.get("provider")})
            except Exception as exc:
                return self.send_json({"error": str(exc)[:500]}, 502)
        match = re.fullmatch(r"/api/tracks/([0-9a-f]{8})/prepare/(guitar|bass)", path)
        if match:
            track_id, instrument = match.groups()
            folder = TRACKS / track_id
            if not folder.is_dir():
                return self.send_json({"error": "track not found"}, 404)
            model = query.get("model", [MODEL])[0]
            quality = query.get("quality", ["balanced"])[0]
            force = query.get("force", ["0"])[0] == "1"
            if not re.fullmatch(r"[A-Za-z0-9._-]+", model):
                return self.send_json({"error": "invalid model name"}, 400)
            if quality not in QUALITY:
                quality = "balanced"
            config = INSTRUMENTS[instrument]
            if (not force
                    and (folder / config["stem"]).exists()
                    and (folder / config["tab"]).exists()
                    and (folder / f"no_{instrument}.mp3").exists()):
                return self.send_json({"status": "done"})
            key = (track_id, instrument)
            if key in preparing:
                return self.send_json({"error": "this instrument is already being cut"}, 409)
            preparing.add(key)
            jobs.setdefault(track_id, {}).update(
                status="queued", stage=f"stem-{instrument}", model=model, quality=quality)
            prepare_pending.put((track_id, instrument, model, quality, force))
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
        prepare_pending.put((track_id, instrument, MODEL, "balanced", False))
        self.send_json({"id": track_id})

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= 65536:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode())
        except (UnicodeDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def send_json(self, payload, code=200):
        if "lang=ru" in (self.headers.get("Cookie") or ""):
            for key in ("error", "message"):
                if isinstance(payload.get(key), str):
                    payload[key] = localize_ru(payload[key])
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
