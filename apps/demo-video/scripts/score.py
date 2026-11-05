"""Synthesise the film's original score and its sound effects, cut to the voiceover timeline.

    python score.py --out ../public/audio

Reads script.json (scene holds) and vo.json (voice durations and caption times) so every musical
event lands on the edit: a chord bloom on the title, a build into the gate, the music cut to
silence on the failed row, and a resolve under the close. The score ducks itself under each spoken
line. Everything is generated here, so there is nothing to license.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, fftconvolve, lfilter, sosfilt

SR = 48_000
BPM = 96
BEAT = 60 / BPM
HERE = Path(__file__).resolve().parent
RNG = np.random.default_rng(20261104)


def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def timeline(vo: dict, script: dict) -> tuple[dict, list[tuple[float, float]], float]:
    starts, speech, t = {}, [], 0.0
    for scene in script["scenes"]:
        sid = scene["id"]
        starts[sid] = t
        for cap in vo[sid]["captions"]:
            speech.append((t + cap["start"], t + cap["end"]))
        t += vo[sid]["duration"] + scene["hold"]
    return starts, speech, t


def env_adsr(n: int, attack: float, release: float) -> np.ndarray:
    e = np.ones(n)
    a, r = min(int(attack * SR), n), min(int(release * SR), n)
    if a:
        e[:a] = np.linspace(0, 1, a) ** 2
    if r:
        e[-r:] *= np.linspace(1, 0, r) ** 1.5
    return e


def pad_note(freq: float, dur: float, bright: float) -> np.ndarray:
    """Three detuned additive saws, harmonics rolled off; returns stereo (n, 2)."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros((n, 2))
    harmonics = max(3, int(4 + 6 * bright))
    for k, (cents, pan) in enumerate(((-7, 0.2), (0, 0.5), (7, 0.8))):
        f = freq * 2 ** (cents / 1200)
        wave = np.zeros(n)
        for h in range(1, harmonics + 1):
            if f * h > 9000:
                break
            wave += np.sin(2 * np.pi * f * h * t + RNG.uniform(0, 2 * np.pi)) / (h**1.25)
        wave *= 1 + 0.08 * np.sin(2 * np.pi * (0.13 + 0.05 * k) * t)  # slow shimmer
        out[:, 0] += wave * (1 - pan)
        out[:, 1] += wave * pan
    return out / 3


def pluck(freq: float, dur: float = 0.9) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    tone = (
        np.sin(2 * np.pi * freq * t)
        + 0.3 * np.sin(4 * np.pi * freq * t)
        + 0.08 * np.sin(6 * np.pi * freq * t)
    )
    return tone * np.exp(-t / 0.22) * np.minimum(1, t / 0.004)


def kick(dur: float = 0.45) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 46 + 70 * np.exp(-t / 0.035)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.16) * np.minimum(1, t / 0.002)


def noise_burst(dur: float, lo: float, hi: float, tau: float) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    sos = butter(4, [lo, hi], btype="band", fs=SR, output="sos")
    return sosfilt(sos, RNG.standard_normal(n)) * np.exp(-t / tau)


def impact(dur: float = 3.0) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 34 + 40 * np.exp(-t / 0.12)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.9)
    sos = butter(2, 900, btype="low", fs=SR, output="sos")
    crack = sosfilt(sos, RNG.standard_normal(n)) * np.exp(-t / 0.06) * 0.5
    return (body + crack) * np.minimum(1, t / 0.003)


def riser(dur: float) -> np.ndarray:
    """Noise through a lowpass that opens over the riser, plus a rising tone."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    noise = RNG.standard_normal(n)
    out = np.zeros(n)
    block = 2400
    state = np.zeros(1)
    for i in range(0, n, block):
        frac = i / n
        cutoff = 300 * (1 - frac) + 7000 * frac
        a = np.exp(-2 * np.pi * cutoff / SR)
        seg, state = lfilter([1 - a], [1, -a], noise[i : i + block], zi=state)
        out[i : i + block] = seg
    tone = np.sin(2 * np.pi * np.cumsum(220 + 440 * (t / dur) ** 2) / SR) * 0.15
    return (out * 0.8 + tone) * (t / dur) ** 2.2


def reverb_ir(seconds: float = 2.8) -> np.ndarray:
    n = int(seconds * SR)
    t = np.arange(n) / SR
    decay = np.exp(-6.9 * t / seconds)
    sos = butter(2, 5000, btype="low", fs=SR, output="sos")
    ir = np.stack([sosfilt(sos, RNG.standard_normal(n)) * decay for _ in range(2)], axis=1)
    fade = int(0.012 * SR)
    ir[:fade] *= np.linspace(0, 1, fade)[:, None]
    return ir / np.sqrt(np.sum(ir**2, axis=0))


def place(bus: np.ndarray, sound: np.ndarray, at: float, gain: float, pan: float = 0.5) -> None:
    i = int(at * SR)
    if i >= len(bus) or i < 0:
        return
    if sound.ndim == 1:
        sound = np.stack([sound * (1 - pan) * 2**0.5, sound * pan * 2**0.5], axis=1)
    j = min(len(bus), i + len(sound))
    bus[i:j] += sound[: j - i] * gain


def curve(t: np.ndarray, points: list[tuple[float, float]]) -> np.ndarray:
    xs, ys = zip(*sorted(points), strict=True)
    return np.interp(t, xs, ys)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    script = json.loads((HERE / "script.json").read_text("utf-8"))
    vo = json.loads((out / "vo.json").read_text("utf-8"))
    s, speech, total = timeline(vo, script)
    length = total + 1.5
    n = int(length * SR)
    t = np.arange(n) / SR

    gate_caps = vo["s07_gate"]["captions"]
    reveal = s["s07_gate"] + gate_caps[5]["start"]  # "Entering counts came in at ..."
    six_pass = s["s07_gate"] + gate_caps[4]["start"]  # "Six checks passed."
    lamp = s["s01_cold"] + 1.55  # the cold-open counter crosses its limit
    close = s["s11_close"]
    grid0 = s["s03_problem"]  # the first downbeat of the groove

    # Harmony on a two-bar grid that puts a downbeat on the title.
    chords = [
        ([50, 57, 62, 65, 76], 38),  # Dm(add9)
        ([46, 53, 57, 62, 65], 34),  # Bbmaj7
        ([45, 53, 57, 60, 64], 33),  # Fmaj7/A
        ([48, 55, 62, 64, 67], 36),  # Csus2
    ]
    final = ([41, 53, 60, 64, 67, 69], 29)  # Fmaj9, the resolve
    bar2 = 8 * BEAT
    events = [(0.0, chords[0])]  # the opening and the cold open hold one dark chord
    k = 0
    while grid0 + k * bar2 < close - 0.01:
        events.append((grid0 + k * bar2, chords[k % 4]))
        k += 1
    events.append((close, final))

    pad = np.zeros((n, 2))
    bass = np.zeros((n, 2))
    for idx, (at, (notes, root)) in enumerate(events):
        nxt = events[idx + 1][0] if idx + 1 < len(events) else length
        last = at == close
        dur = (length - at) if last else (nxt - at) + 1.6
        bright = 0.3 if at < grid0 else 0.6
        m_env = env_adsr(int(dur * SR), 1.4 if at > 0 else 2.5, 3.5 if last else 1.6)[:, None]
        for m in notes:
            place(pad, pad_note(hz(m), dur, bright) * m_env, at, 0.09)
        b_n = int(dur * SR)
        bt = np.arange(b_n) / SR
        f0 = hz(root + 12)
        sub = np.sin(2 * np.pi * f0 * bt) + 0.25 * np.sin(4 * np.pi * f0 * bt)
        place(bass, sub * env_adsr(b_n, 0.6, 1.2), at, 0.16)

    # Arpeggio in eighths, chord tones an octave up, with a ping-pong delay.
    arp = np.zeros((n, 2))
    pattern = [0, 2, 3, 4, 3, 2, 1, 2]
    for z0, z1 in ((s["s03_problem"] + 6.0, s["s07_gate"] + 2.0), (s["s09_muse"], close)):
        step = 0
        at = grid0 + np.ceil((z0 - grid0) / (BEAT / 2)) * (BEAT / 2)
        while at < z1:
            chord = max((e for e in events if e[0] <= at + 1e-6), key=lambda e: e[0])[1][0]
            note = chord[pattern[step % 8] % len(chord)] + 12
            place(arp, pluck(hz(note)), at, 0.05, pan=0.35 if step % 2 else 0.65)
            step += 1
            at += BEAT / 2
    delay = int(0.75 * BEAT * SR)
    wet = np.zeros_like(arp)
    wet[delay:, 1] += arp[:-delay, 0] * 0.45
    wet[2 * delay :, 0] += arp[: -2 * delay, 1] * 0.3
    arp += wet

    # Pulse: a soft kick on the beat; hats on the offbeat through the gate build.
    perc = np.zeros((n, 2))
    for z0, z1, g in (
        (s["s04_vision"] + 8.0, reveal - 0.02, 0.22),
        (s["s10_decision"], close - 0.3, 0.2),
    ):
        at = grid0 + np.ceil((z0 - grid0) / BEAT) * BEAT
        while at < z1:
            place(perc, kick(), at, g)
            at += BEAT
    at = grid0 + np.ceil((s["s07_gate"] - grid0) / BEAT) * BEAT + BEAT / 2
    while at < reveal - 0.05:
        ramp = (at - s["s07_gate"]) / (reveal - s["s07_gate"])
        place(perc, noise_burst(0.08, 6000, 12000, 0.025), at, 0.05 + 0.08 * ramp, pan=0.6)
        at += BEAT / 2

    fx = np.zeros((n, 2))
    place(fx, riser(3.2), grid0 - 3.2, 0.08)
    place(fx, riser(reveal - six_pass), six_pass, 0.10)
    place(fx, impact(), lamp, 0.55)
    place(fx, impact(), reveal, 0.65)
    place(fx, impact(2.5) * 0.6, close, 0.35)

    # The arc of the film.
    music = (pad + arp) * curve(
        t,
        [
            (0, 0.0),
            (0.5, 0.75),
            (s["s01_cold"] - 0.4, 0.75),
            (s["s01_cold"] + 0.3, 0.3),
            (grid0 - 0.5, 0.45),
            (grid0, 1.0),
            (grid0 + 3.0, 0.85),
            (s["s04_vision"], 0.9),
            (s["s07_gate"], 1.0),
            (reveal - 0.05, 1.15),
            (reveal + 0.06, 0.0),
            (reveal + 2.2, 0.0),
            (reveal + 4.0, 0.55),
            (s["s09_muse"], 0.8),
            (s["s10_decision"], 0.95),
            (close, 1.1),
            (length - 2.0, 0.9),
            (length, 0.0),
        ],
    )[:, None]
    bass *= curve(
        t,
        [
            (0, 0.0),
            (grid0, 0.0),
            (grid0 + 0.3, 1.0),
            (reveal - 0.05, 1.1),
            (reveal + 0.06, 0.0),
            (s["s09_muse"], 0.0),
            (s["s09_muse"] + 2.0, 0.8),
            (close, 1.0),
            (length, 0.0),
        ],
    )[:, None]
    perc *= curve(
        t,
        [
            (0, 1),
            (reveal - 0.02, 1),
            (reveal + 0.02, 0),
            (s["s10_decision"] - 0.1, 0),
            (s["s10_decision"], 1),
            (length, 1),
        ],
    )[:, None]

    ir = reverb_ir()
    verb_in = music + fx * 0.6
    verb = np.stack([fftconvolve(verb_in[:, c], ir[:, c])[:n] for c in range(2)], axis=1)
    mix = music + bass + perc + fx + verb * 0.32

    duck = np.ones(n)  # under the voice
    for a, b in speech:
        duck[max(int((a - 0.15) * SR), 0) : int((b + 0.25) * SR)] = 0.5
    duck = sosfilt(butter(1, 3.0, btype="low", fs=SR, output="sos"), duck)
    mix *= duck[:, None]

    mix = np.tanh(mix * 1.2) / 1.2
    mix *= 0.5 / np.max(np.abs(mix))  # about -6 dBFS peak, under a -1 dBFS voice
    sf.write(out / "score.wav", mix.astype(np.float32), SR, subtype="PCM_16")
    rms = 20 * np.log10(np.sqrt(np.mean(mix**2)) + 1e-9)
    print(
        f"score.wav: {length:.2f} s, RMS {rms:.1f} dBFS, lamp {lamp:.2f} s, reveal {reveal:.2f} s"
    )

    # Effects the edit places frame-exactly.
    m = int(0.09 * SR)
    tick = np.sin(2 * np.pi * 1760 * np.arange(m) / SR) * np.exp(-np.arange(m) / SR / 0.018)
    tick += noise_burst(0.09, 3000, 9000, 0.004) * 0.3
    sf.write(
        out / "sfx_tick.wav",
        (tick / np.max(np.abs(tick)) * 0.5).astype(np.float32),
        SR,
        subtype="PCM_16",
    )
    thunk = impact(1.6)
    sf.write(
        out / "sfx_thunk.wav",
        (thunk / np.max(np.abs(thunk)) * 0.7).astype(np.float32),
        SR,
        subtype="PCM_16",
    )
    whoosh = noise_burst(0.9, 400, 4000, 0.3) * np.sin(np.linspace(0, np.pi, int(0.9 * SR))) ** 2
    sf.write(
        out / "sfx_whoosh.wav",
        (whoosh / np.max(np.abs(whoosh)) * 0.35).astype(np.float32),
        SR,
        subtype="PCM_16",
    )
    print("sfx: tick, thunk, whoosh")


if __name__ == "__main__":
    main()
