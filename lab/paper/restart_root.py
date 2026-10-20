"""M3-09b's injected restart (research/m3-09b-follow-source-spec-2026-10-20.md v0.2 §4.3): a copy of a recorder's root
in which segments AT .. AT + N - 1 come again, with new seqs, right after the last of them: as a recorder restarted
below its last segment writes them (the same rows, new segments). LiveFeed and lab-live's follow source both read it.

  python -m paper.restart_root --root REC --out DIR --at SEQ [--n 3]

The copy is made of symlinks: each segment directory of REC/segments (complete or not) becomes OUT/segments/<new seq>
holding a link to each entry of the original but `_segment.json`, which is written with the new seq (the rest of it as
it was). Seqs before AT + N stay; the N copies take AT + N .. AT + 2N - 1; the later segments move up by N. Nothing under
REC is written. Refused: an OUT that exists and is not empty, an OUT inside REC, and copies of segments that are not all
there and complete."""

import argparse
import json
import os


def _segments(root):
    """[(seq, directory name, meta)] of every segment directory under root/segments, by seq."""
    base = os.path.join(root, "segments")
    out = []
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name, "_segment.json")
        if name.startswith(".") or not os.path.isfile(path):
            continue
        with open(path) as f:
            meta = json.load(f)
        out.append((int(meta["seq"]), name, meta))
    return sorted(out, key=lambda s: (s[0], s[1]))


def make(root, out, at, n=3):
    """Write the copy (module doc) under `out`; returns what it wrote: segments, the copied seqs and their new seqs."""
    root, out = os.path.abspath(root), os.path.abspath(out)
    if os.path.commonpath([root, out]) == root:
        raise SystemExit(f"restart_root: {out} is inside {root}: refused")
    if os.path.exists(out) and os.listdir(out):
        raise SystemExit(f"restart_root: {out} exists and is not empty: refused")
    segs = _segments(root)
    by_seq = {}
    for seq, name, meta in segs:
        by_seq.setdefault(seq, []).append((name, meta))
    copied = list(range(at, at + n))
    for seq in copied:
        found = by_seq.get(seq, [])
        if len(found) != 1 or not found[0][1].get("complete"):
            raise SystemExit(f"restart_root: segment {seq} is not there once and complete: refused")
    plan = []  # (new seq, source directory name, meta)
    for seq, name, meta in segs:
        plan.append((seq if seq < at + n else seq + n, name, meta))
    for i, seq in enumerate(copied):
        name, meta = by_seq[seq][0]
        plan.append((at + n + i, name, meta))
    base = os.path.join(root, "segments")
    os.makedirs(os.path.join(out, "segments"), exist_ok=True)
    for new, name, meta in sorted(plan, key=lambda p: p[0]):
        src, dst = os.path.join(base, name), os.path.join(out, "segments", f"{new:09d}")
        os.mkdir(dst)
        for entry in sorted(os.listdir(src)):
            if entry != "_segment.json":
                os.symlink(os.path.join(src, entry), os.path.join(dst, entry))
        with open(os.path.join(dst, "_segment.json"), "w") as f:
            json.dump(dict(meta, seq=new), f)
    return {"segments": len(plan), "copied": copied, "copies": list(range(at + n, at + 2 * n)), "moved_up_by": n}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", required=True, help="the recorder's directory (its segments/ is read)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--at", type=int, required=True, help="the first segment that comes again")
    ap.add_argument("--n", type=int, default=3)
    a = ap.parse_args(argv)
    if a.n < 1:
        ap.error("--n must be at least 1")
    print(json.dumps(make(a.root, a.out, a.at, a.n)))


if __name__ == "__main__":
    main()
