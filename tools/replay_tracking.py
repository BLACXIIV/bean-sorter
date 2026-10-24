#!/usr/bin/env python3
"""Replay a BEAN_RECORD JSONL file through the trajectory tracker offline.

  python tools/replay_tracking.py <recording.jsonl>

Prints counts, suppressed, estimated, lost_before_line and a per-track summary.
Votes come from the recorded "vote" field (the grade the live classifier cast).
"""
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C
import vision
from sorter import Line
from tracking import TrajectoryTracker, TTrack


def main(path):
    C.FEATURES["trajectory_tracking"] = True
    line = Line((0, 360), (1280, 360))
    tracker = TrajectoryTracker(line)
    n_frames = 0
    for raw in open(path, encoding="utf-8"):
        rec = json.loads(raw)
        n_frames += 1
        dets = []
        for d in rec["detections"]:
            pal = [vision.PaletteColor(np.array(p[:3], np.float32), p[3], "")
                   for p in d.get("palette") or []]
            det = {"centroid": (d["x"], d["y"]), "area": d["area"],
                   "contour": None, "palette": pal or None}
            det["_vote"] = d.get("vote")
            dets.append(det)
        res = tracker.update(dets, float(rec["t"]))
        for tr, det in res["votes"]:
            if det.get("_vote") is not None:
                tr.votes[det["_vote"]] += 1
        for cr in res["crossings"]:
            g = cr.track.decide()
            if g is None:
                continue
            if tracker.dedupe_check(cr.track, cr.point, cr.t_c, g):
                continue
            tracker.register_crossing(cr.point, cr.t_c, g, cr.crossing_type,
                                      getattr(cr.track, "pal", None))

    c = tracker.counters()
    print(f"frames={n_frames}  tracks={len(tracker.tracks)} "
          f"(+{len(tracker.lost)} recently lost)")
    print(f"unique_crossed={c['unique_crossed']}  suppressed={c['suppressed']}  "
          f"estimated={c['estimated']}  lost_before_line={c['lost_before_line']}  "
          f"uncertain={c['uncertain']}  vote_resets={c['vote_resets']}  "
          f"active={c['active']}  coasting={c['coasting']}")
    print()
    print(f"{'track':>5} {'state':<10} {'hits':>4} {'votes':>10} "
          f"{'speed':>7} {'crossing':>10}")
    for tr in sorted(tracker.tracks + tracker.lost, key=lambda t: t.id):
        v = dict(tr.votes)
        print(f"{tr.id:>5} {tr.state:<10} {tr.hits:>4} {str(v):>10} "
              f"{tr.speed_px_s:>7.0f} {str(tr.crossing_type):>10}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]) if len(sys.argv) > 1
                else print("usage: replay_tracking.py <file.jsonl>"))
