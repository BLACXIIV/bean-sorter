#!/usr/bin/env python3
"""Missed-defect and lost-track forensics for a simrun with BEAN_RECORD on.

Inputs (written by scripts/a3_matrix.py / simrun.py):
  <tag>_status.json  final /api/status dump (ground_truth.beans + fires)
  <tag>.jsonl        BEAN_RECORD: one JSON line per frame (t, detections[])

For every defect bean that crossed but got no fire: frames visible before the
line, detections matched, votes cast, track ids, and each track's state at the
bean's physical crossing frame. Then every track that died before the line is
classified as a real-bean fragment or a ghost.

  python scripts/miss_detail.py var/a3_harsh_traj_s11
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LINE_Y = 360.0                     # simrun sets the line at ny=0.5 of 720


def main(tag):
    status_p = Path(str(tag) + "_status.json")
    rec_p = Path(str(tag) + ".jsonl")
    status = json.loads(status_p.read_text())
    gt = status["ground_truth"]
    step_px, sim_dt = gt["step_px"], gt["sim_dt"]
    beans = {b["id"]: b for b in gt["beans"]}
    fires = gt["fires"]
    hit_ids = {f["bean_id"] for f in fires if f.get("bean_id")}

    frames = []
    for line in rec_p.read_text().splitlines():
        frames.append(json.loads(line))
    f_of_t = {}                      # t -> frame index (record order)
    for i, fr in enumerate(frames):
        f_of_t[fr["t"]] = i
    last_frame = len(frames) - 1

    def bean_pos(b, fi):
        """physical bean centre at record line fi (0-based); the camera's
        _frame_i is 1-based and increments before drawing -> fi + 1."""
        return (b["x_spawn"], b["y_spawn"] + step_px * (fi + 1 - b["spawn_f"]))

    # det -> physical bean matching, per frame (nearest bean circle hit)
    det_bean = defaultdict(dict)     # fi -> {det_index: bean_id}
    for fi, fr in enumerate(frames):
        live = [b for b in beans.values()
                if fi >= b["spawn_f"]
                and bean_pos(b, fi)[1] - b["r"] < 720 + 60]
        for di, d in enumerate(fr["detections"]):
            best, bd = None, 1e18
            for b in live:
                bx, by = bean_pos(b, fi)
                dist = ((d["x"] - bx) ** 2 + (d["y"] - by) ** 2) ** 0.5
                if dist < b["r"] and dist < bd:
                    best, bd = b["id"], dist
            if best is not None:
                det_bean[fi][di] = best

    # per-bean detection history
    bean_dets = defaultdict(list)    # bid -> [(fi, det)]
    for fi, mp in det_bean.items():
        for di, bid in mp.items():
            bean_dets[bid].append((fi, frames[fi]["detections"][di]))

    missed = [b for b in beans.values()
              if b["grade"] == 4 and b["crossed"] and b["id"] not in hit_ids]
    for b in sorted(missed, key=lambda b: b["id"]):
        cross_f = b["cross_f"] - 1          # _frame_i (1-based) -> record idx
        print(f"\n=== bean #{b['id']} (G4) crossed frame {cross_f + 1} "
              f"(x={b['x_at_line']:.0f}, frames_visible={b['frames_visible']})")
        votes = Counter()
        trks = {}
        for fi, d in bean_dets.get(b["id"], []):
            votes[d["vote"]] += d["vote"] is not None
            tid = d.get("track")
            if tid is not None:
                trks.setdefault(tid, []).append((fi, d.get("tstate"),
                                                 round(d["y"], 0),
                                                 d.get("vote"),
                                                 d.get("suspect")))
        print(f"    detections={len(bean_dets.get(b['id'], []))} "
              f"votes={dict(votes)} tracks={sorted(trks)}")
        for tid, hist in sorted(trks.items()):
            state_at_cross = next(
                (h[1] for h in reversed(hist) if h[0] <= cross_f), "gone")
            print(f"      trk#{tid}: hits={len(hist)} "
                  f"first=f{hist[0][0]} last=f{hist[-1][0]} "
                  f"state@cross={state_at_cross}")
            for h in hist:
                print(f"        f{h[0]:<5} state={h[1]:<10} y={h[2]:<6} "
                      f"vote={h[3]} suspect={h[4]}")

    # ---- lost_before_line: fragment (real bean) or ghost (no bean) ----------
    trk_frames = defaultdict(list)   # tid -> [fi]
    trk_pos = {}
    trk_beans = defaultdict(set)
    trk_counted = set()              # tracks ever flagged counted in the log
    for fi, fr in enumerate(frames):
        for di, d in enumerate(fr["detections"]):
            tid = d.get("track")
            if tid is None:
                continue
            trk_frames[tid].append(fi)
            trk_pos[tid] = (d["x"], d["y"])
            if d.get("counted"):
                trk_counted.add(tid)
            bid = det_bean.get(fi, {}).get(di)
            if bid is not None:
                trk_beans[tid].add(bid)

    # A culled uncounted track shows up in lost_before_line even when it died
    # below the line (a tentative that never emitted a crossing). Classify ALL
    # dead tracks: fragment = its dets matched a physical bean; ghost = none
    # did (phantom detection). A fragment whose bean never crossed is a real
    # miss; one whose bean crossed anyway is harmless duplication debris.
    frag = ghost = frag_uncrossed = 0
    detail = []
    for tid, fis in trk_frames.items():
        last_f, last_y = fis[-1], trk_pos[tid][1]
        if last_f >= last_frame or tid in trk_counted:
            continue                    # alive at drain, or was counted
        bids = trk_beans.get(tid, set())
        if not bids:
            ghost += 1
            detail.append((tid, "ghost", None, last_f, round(last_y)))
            continue
        frag += 1
        # cross_f is 1-based camera frame; last_f is a 0-based record index
        uncrossed = [bid for bid in bids
                     if not beans[bid]["crossed"]
                     or (beans[bid]["cross_f"] or 10 ** 9) - 1 > last_f]
        frag_uncrossed += bool(uncrossed)
        detail.append((tid, "fragment" + ("(uncrossed)" if uncrossed
                                        else ""), bids and sorted(bids)[0],
                       last_f, round(last_y)))
    trk_counters = (status.get("tracking") or {})
    print(f"\nlost_before_line={trk_counters.get('lost_before_line')} | "
          f"classified dead tracks: fragments_of_real_beans={frag} "
          f"(of which bean never crossed: {frag_uncrossed}) ghosts={ghost}")
    for tid, kind, bid, lf, ly in detail[:40]:
        print(f"    trk#{tid} {kind} bean={bid} died f{lf} y={ly}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1
                  else str(ROOT / "var" / "a3_harsh_traj_s11")))
