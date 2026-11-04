"""Draw a dump's boxes and the NGSIM references on dev frames, for a person to look at.

    ~/.venvs/cv-bakeoff/bin/python scripts/cv_contact_sheet.py --dump dfine-l__tiled2x \
        --conf 0.25 --times 375.0,405.0,985.0

Sheets go to <scratch>/sheets and nowhere else: they hold pixels. Colours (BGR drawn, named here):
green = reference matched, yellow = reference missed, cyan = detection matched, red = detection
unmatched. A reference is its footprint; it is drawn only if in view. `--crop u1,v1,u2,v2` zooms.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for package in ("perception", "contracts"):
    path = str(ROOT / "packages" / package)
    if path not in sys.path:
        sys.path.insert(0, path)
sys.path.insert(0, str(ROOT / "scripts"))

import cv_bakeoff as H  # noqa: E402

from cairoflow_perception.local import scoring as S  # noqa: E402

GREEN, YELLOW, CYAN, RED = (0, 220, 0), (0, 230, 230), (230, 220, 0), (40, 40, 255)


def panel(
    image: np.ndarray,
    dets: list[S.Detection],
    ref: S.ReferenceSet,
    source_s: float,
    scale: int,
    crop: tuple[int, int, int, int] | None,
    title: str,
) -> np.ndarray:
    frame = S.tenths(source_s)
    placed = S.detections_to_dets(frame, dets, ref.ground)
    result = S.associate_frame(frame, placed, ref.refs[frame])
    matched_refs = {m.vehicle_id for m in result.matches}
    matched_dets = {m.det_id for m in result.matches}
    u0, v0, u1, v1 = crop or (0, 0, image.shape[1], image.shape[0])
    view = cv2.resize(image[v0:v1, u0:u1], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    def px(u: float, v: float) -> tuple[int, int]:
        return round((u - u0) * scale), round((v - v0) * scale)

    for r in ref.refs[frame]:
        if r.in_view and r.footprint_px is not None:
            x1, y1, x2, y2 = r.footprint_px
            colour = GREEN if r.vehicle_id in matched_refs else YELLOW
            cv2.rectangle(view, px(x1, y1), px(x2, y2), colour, 1)
    for d, p in zip(dets, placed, strict=True):
        colour = CYAN if p.det_id in matched_dets else RED
        cv2.rectangle(view, px(d.x1, d.y1), px(d.x2, d.y2), colour, 1)
        text = f"{d.label[0]}{d.score:.2f}".replace("0.", ".")
        x, y = px(d.x1, d.y1)
        cv2.putText(view, text, (x, max(y - 2, 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.32, colour, 1)
    n_ref = result.n_ref_in_view
    hits = sum(m.ref_in_view for m in result.matches)
    head = f"{title} s={source_s} refs {hits}/{n_ref} dets {len(result.matches)}/{result.n_det}"
    cv2.rectangle(view, (0, 0), (min(view.shape[1], 560), 15), (0, 0, 0), -1)
    cv2.putText(view, head, (3, 11), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    return view


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dump", required=True)
    parser.add_argument("--conf", type=float, required=True)
    parser.add_argument("--times", required=True, help="comma list of source seconds, dev only")
    parser.add_argument("--scale", type=int, default=2)
    parser.add_argument("--crop", default="")
    parser.add_argument("--scratch", type=Path, default=Path.home() / "scratch-cv")
    parser.add_argument("--name", default="")
    args = parser.parse_args(argv)
    out = args.scratch / "sheets"
    out.mkdir(parents=True, exist_ok=True)
    ref = H.load_reference(args.scratch, "sync120")
    with np.load(args.scratch / f"bakeoff/dumps/{args.dump}.npz", allow_pickle=False) as z:
        keep = z["score"] >= args.conf
        frames, boxes = z["frame"][keep], z["box"][keep]
        scores, labels = z["score"][keep], z["label"][keep]
    crop = tuple(int(a) for a in args.crop.split(",")) if args.crop else None
    with S.FrameSampler(ROOT / S.VIDEO) as sampler:
        for t in (float(a) for a in args.times.split(",")):
            rows = np.flatnonzero(frames == S.tenths(t))
            dets = [S.Detection(*boxes[i].tolist(), float(scores[i]), str(labels[i])) for i in rows]
            image = panel(
                sampler.read(t), dets, ref, t, args.scale, crop, f"{args.dump} c{args.conf}"
            )
            name = args.name or args.dump
            path = out / f"{name}_c{args.conf}_s{t}.png"
            cv2.imwrite(str(path), image)
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
