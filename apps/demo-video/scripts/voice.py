"""Synthesise the voiceover, one WAV per scene, and write the timing manifest.

    python voice.py --model kokoro-v1.0.onnx --voices voices-v1.0.bin --out ../public/audio

Reads script.json beside this file. Each line is spoken from its "say" text and captioned with its
"show" text; "pause" seconds of silence follow it and "lead" seconds precede a scene. The manifest
(vo.json) gives every scene's duration and each caption's start and end, in seconds from the scene
start, so the edit and the captions are cut to the audio, never the other way round.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

HERE = Path(__file__).resolve().parent


def trim(samples: np.ndarray, threshold: float = 0.004) -> np.ndarray:
    """Cut leading and trailing near-silence so the pauses in the script are the only gaps."""
    loud = np.flatnonzero(np.abs(samples) > threshold)
    if loud.size == 0:
        return samples
    pad = 240  # 10 ms at 24 kHz keeps consonant onsets and tails
    return samples[max(loud[0] - pad, 0) : min(loud[-1] + pad, samples.size)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--voices", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", default="", help="comma-separated scene ids to redo")
    args = parser.parse_args()

    script = json.loads((HERE / "script.json").read_text("utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "vo.json"
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {}
    only = {s for s in args.only.split(",") if s}
    kokoro = Kokoro(args.model, args.voices)

    for scene in script["scenes"]:
        if only and scene["id"] not in only:
            continue
        rate = 24000
        pieces = [np.zeros(int(scene["lead"] * rate), dtype=np.float32)]
        cursor = scene["lead"]
        captions = []
        for line in scene["lines"]:
            samples, rate = kokoro.create(
                line["say"], voice=script["voice"], speed=script["speed"], lang="en-us"
            )
            speech = trim(np.asarray(samples, dtype=np.float32))
            start = cursor
            cursor += speech.size / rate
            captions.append(
                {"text": line["show"], "start": round(start, 3), "end": round(cursor, 3)}
            )
            pieces.append(speech)
            pieces.append(np.zeros(int(line["pause"] * rate), dtype=np.float32))
            cursor += line["pause"]
        audio = np.concatenate(pieces)
        peak = float(np.max(np.abs(audio))) or 1.0
        audio = audio * (0.89 / peak)  # about -1 dBFS
        sf.write(out / f"{scene['id']}.wav", audio, rate, subtype="PCM_16")
        manifest[scene["id"]] = {
            "file": f"audio/{scene['id']}.wav",
            "duration": round(audio.size / rate, 3),
            "captions": captions,
        }
        print(f"{scene['id']}: {audio.size / rate:6.2f} s")

    ordered = {s["id"]: manifest[s["id"]] for s in script["scenes"] if s["id"] in manifest}
    manifest_path.write_text(json.dumps(ordered, indent=1) + "\n", "utf-8")
    total = sum(v["duration"] for v in ordered.values())
    print(f"total voice timeline: {total:6.2f} s")


if __name__ == "__main__":
    main()
