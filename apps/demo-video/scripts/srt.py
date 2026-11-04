"""Write the film's captions as SubRip, for upload platforms that take a separate caption file.

    python3 srt.py [out.srt]       (default: ../out/CairoFlow-demo.srt)

Uses the same timeline as the edit: each scene lasts its voice track plus its hold (script.json),
and each caption keeps the start and end the voice manifest (public/audio/vo.json) recorded.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def stamp(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "out" / "CairoFlow-demo.srt"
    script = json.loads((HERE / "script.json").read_text("utf-8"))
    vo = json.loads((HERE.parent / "public" / "audio" / "vo.json").read_text("utf-8"))
    t, n, blocks = 0.0, 0, []
    for scene in script["scenes"]:
        voice = vo[scene["id"]]
        for cap in voice["captions"]:
            n += 1
            span = f"{stamp(t + cap['start'])} --> {stamp(t + cap['end'] + 0.25)}"
            blocks.append(f"{n}\n{span}\n{cap['text']}\n")
        t += voice["duration"] + scene["hold"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(blocks), "utf-8")
    print(f"{out}: {n} captions, {t:.2f} s")


if __name__ == "__main__":
    main()
