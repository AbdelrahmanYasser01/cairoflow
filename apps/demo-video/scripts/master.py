"""Master the film's audio from its stems and mux it with the rendered picture.

    python master.py --video ../out/CairoFlow-demo.mp4 --out ../out/CairoFlow-demo-final.mp4

Rebuilds the mix in floating point on the edit's own timeline (the same frame-rounded scene starts
as src/timeline.ts, the same gains and effect cues as src/CairoFlowDemo.tsx), so coincident voice
and score peaks can't clip, then normalises it with ffmpeg's loudnorm to -16 LUFS integrated and a
-1.5 dBTP ceiling, and copies the picture stream untouched.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

HERE = Path(__file__).resolve().parent
AUDIO = HERE.parent / "public" / "audio"
FPS = 30
SR = 48_000
WHOOSH_BEFORE = ["s03_problem", "s07_gate", "s09_muse", "s11_close"]


def frames(seconds: float) -> int:
    return round(seconds * FPS)


def load(path: Path) -> np.ndarray:
    data, rate = sf.read(path, dtype="float64", always_2d=True)
    if rate != SR:
        data = resample_poly(data, SR, rate, axis=0)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    return data


def add(bus: np.ndarray, sound: np.ndarray, at_frame: int, gain: float) -> None:
    i = round(at_frame / FPS * SR)
    j = min(len(bus), i + len(sound))
    if i < len(bus):
        bus[i:j] += sound[: j - i] * gain


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    script = json.loads((HERE / "script.json").read_text("utf-8"))
    vo = json.loads((AUDIO / "vo.json").read_text("utf-8"))

    starts, t = {}, 0.0
    for scene in script["scenes"]:
        starts[scene["id"]] = frames(t)
        t += vo[scene["id"]]["duration"] + scene["hold"]
    total = frames(t)
    bus = np.zeros((round(total / FPS * SR), 2))

    for scene in script["scenes"]:
        add(bus, load(AUDIO / f"{scene['id']}.wav"), starts[scene["id"]], 1.0)
    add(bus, load(AUDIO / "score.wav"), 0, 0.9)
    tick, whoosh = load(AUDIO / "sfx_tick.wav"), load(AUDIO / "sfx_whoosh.wav")
    six = vo["s07_gate"]["captions"][4]["start"]  # "Six checks passed."; rows report from here
    for k in range(6):
        add(bus, tick, starts["s07_gate"] + frames(six + 1.3 + k * 0.19), 0.55)
    for sid in WHOOSH_BEFORE:
        add(bus, whoosh, max(0, starts[sid] - frames(0.45)), 0.5)

    peak = float(np.max(np.abs(bus)))
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "mix.wav"
        sf.write(raw, (bus / max(peak, 1.0)).astype(np.float32), SR, subtype="FLOAT")
        probe = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(raw), "-af",
             "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
            capture_output=True, text=True, check=True,
        )  # fmt: skip
        m = json.loads(
            re.search(r"\{[^{}]*\}", probe.stderr[probe.stderr.rfind("{") - 1 :]).group(0)
        )
        norm = (
            "loudnorm=I=-16:TP=-1.5:LRA=11:linear=true"
            f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", args.video, "-i", str(raw),
             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-af", f"{norm},aresample=48000",
             "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", "-shortest", args.out],
            check=True,
        )  # fmt: skip
    print(
        f"mix peak before normalising {20 * np.log10(peak):.2f} dBFS; measured {m['input_i']} LUFS"
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
