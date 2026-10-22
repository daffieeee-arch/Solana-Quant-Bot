"""x10-jager stage C (research/x10-jager-plan-2026-10-23.md v0.2 sections 4-7; roadmap #167): the winner-pattern models on
stage B's features.parquet, one chronological split, the validation choice per target, the three primary test cells
with Holm, the day bootstrap, DSR / CSCV and the report.

  Split      train = epochs 1033-1040, val = 1041-1043, test = 1044-1046 (stage B's block column); rows with censored_24
             (the 24 h label window crosses the next block boundary or the data end) are dropped everywhere.
  Targets    hit_2_24, hit_5_24, hit_10_24 (H = 24 h is the only gated horizon; the 72 h exits are descriptive).
  Features   every numeric column of features.parquet except identity, labels, exits, the post-entry windows and the
             whole-dev farmer columns (EXCLUDE_*, plan M3); mayhem and entry_venue become 0 / 1.
  Models     lightgbm binary (LGB_PARAMS, num_leaves in LEAVES, early stopping on val) and a logistic baseline
             (numpy, standardised features, L2 = 1, fixed iterations). Both deterministic.
  Threshold  per k in KS the (1 - k / 100) quantile of the TRAIN scores: a fixed number, applied to val and test.
  Cells      per target: moment x model x num_leaves x k x exit (the 24 h exits, EXITS_24, plus the 72 h ones as
             descriptive columns); every validation cell is written out. Metrics per cell: trades, trades/day, mean and
             median return per trade, hit rate vs base rate, net SOL/day at CAPITAL with the capacity rule (positions
             of SIZE SOL, at most X / SIZE open at once, a position counts as open for 24 h after its entry (every
             position closes within the gated horizon: conservative), signals above the cap skipped in time order).
  Choice     per target on validation: X* = the largest capital level with >= MIN_TRADES_PER_DAY taken trades per day
             (a trade-count rule, nothing chosen at test); the cell with the highest validation SOL/day at X* wins
             (ties: fewer trades, then the order of the grid). Cells without any eligible X* are skipped.
  Test       the chosen cell per target: trades per UTC day, precision vs the chosen moment's test base rate (the
             all-moments rate is a report figure only), calibration (10 bins), EV per
             trade, SOL/day at 10 / 25 / 100 (nested) with a day bootstrap (BOOT draws, seed SEED), one-sided p =
             share of bootstrap means <= 0, Holm over the three targets at X*, top-1 % P&L share, stop-first share,
             feature importance (gain), the logistic baseline's chosen cell and "all coins" as references; DSR on the
             test day series with the validation cell count as trials; CSCV / PBO over the validation cells
             (S = 16 chunks of the validation rows in slot order, C(16, 8) splits, as #203).
  Gate       per target (plan section 7): Holm-adjusted p < 0.05 and the 95 % lower bound > 0 at X*, >= 5 trades/day,
             top-1 % share < 50 %, precision >= 2 x base rate.

  python -m wallets.x10.model --features DIR/features.parquet --out DIR [--boot 10000]
Writes model.json (everything), model.md, cells-val.parquet, scores-<target>.parquet (key, moment, t, block, score,
selected) and lgbm-<target>.txt (the frozen model when a target passes).
"""
import argparse
import hashlib
import itertools
import json
import math
import os
import time

import duckdb
import numpy as np

try:
    import lightgbm as lgb
except ImportError:   # the NAS may lack it: nas_x10.sh checks and the model then runs on the VPS
    lgb = None

TARGETS = (2, 5, 10)
MOMENTS = ("e10", "c30", "c2", "mig")
LEAVES = (15, 63)
KS = (1, 5, 10)
MODELS = ("lgbm", "logit")
EXITS_24 = ("trail30", "tp2", "tp5", "tp10", "half2", "half5")
EXITS_72 = tuple(f"{r}_72" for r in EXITS_24)
CAPITAL = (10, 25, 100)
SIZE = 0.5
MIN_TRADES_PER_DAY = 5
BOOT = 10_000
SEED = 7
ALPHA = 0.05
CSCV_S = 16
LGB_PARAMS = {"objective": "binary", "learning_rate": 0.05, "min_child_samples": 200, "feature_fraction": 0.8, "bagging_fraction": 0.8,
              "bagging_freq": 1, "seed": SEED, "deterministic": True, "num_threads": 4, "force_row_wise": True, "verbose": -1}
LGB_ROUNDS, LGB_EARLY = 2000, 100
LOGIT_L2, LOGIT_ITERS, LOGIT_LR = 1.0, 500, 0.1
EXCLUDE_PREFIX = ("hit_", "stop_first_", "peak_", "t_peak_", "end_", "dd_", "n_marks", "mult_", "x_", "trig_", "post1_", "post5_")
EXCLUDE_SUFFIX = ("_inflow_org", "_buyers_nofarm")
EXCLUDE_COLS = {"key", "mint", "moment", "t", "entry_slot", "entry_ord", "entry_venue", "segment", "regime", "day", "epoch", "create_slot", "creator", "pool",
                "block", "next_boundary", "censored_24", "censored_72", "tokens", "cost_sol", "t_max_min", "last_min", "t_stop_min", "n_curve", "n_pool",
                "h6_slots", "cut", "d_slots", "has_curve", "cr_hist_days", "mayhem"}
KEEP_BOOL = ("mayhem",)


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------- data


def feature_columns(cols):
    """The model's columns from the parquet's column list (fixed rule, no selection on any outcome)."""
    out = []
    for name, typ in cols:
        if name in EXCLUDE_COLS and name not in KEEP_BOOL:
            continue
        if name.startswith(EXCLUDE_PREFIX) or name.endswith(EXCLUDE_SUFFIX):
            continue
        t = typ.upper()
        if name in KEEP_BOOL or t in ("BOOLEAN",) or any(t.startswith(p) for p in ("DOUBLE", "FLOAT", "BIGINT", "INTEGER", "HUGEINT", "UBIGINT", "SMALLINT", "TINYINT", "DECIMAL")):
            out.append(name)
    return sorted(out) + ["entry_venue_pool"]


def load(path):
    con = duckdb.connect()
    cols = [(r[0], r[1]) for r in con.execute(f"DESCRIBE SELECT * FROM '{path}'").fetchall()]
    feats = feature_columns(cols)
    sel = ", ".join(f'"{c}"::DOUBLE AS "{c}"' for c in feats if c != "entry_venue_pool")
    exits = ", ".join(f'"x_{r}_24"::DOUBLE AS "x_{r}_24", "x_{r}_72"::DOUBLE AS "x_{r}_72"' for r in EXITS_24)
    hits = ", ".join(f'"hit_{m}_24"::INT AS "hit_{m}_24"' for m in TARGETS)
    q = f"""SELECT key, moment, t, block, entry_slot::BIGINT AS entry_slot, day::VARCHAR AS day, round(h6_slots / 3.0)::BIGINT AS h24_slots,
                   (entry_venue = 'pool')::DOUBLE AS entry_venue_pool, stop_first_24::INT AS stop_first_24, {hits}, {exits}, {sel}
            FROM '{path}' WHERE NOT censored_24 AND block IS NOT NULL ORDER BY entry_slot, key, moment, t"""
    tbl = con.execute(q).arrow().read_all()
    d = {c: tbl.column(c).to_numpy(zero_copy_only=False) for c in tbl.column_names}
    X = np.column_stack([np.asarray(d[c], dtype=np.float64) for c in feats])
    censored = con.execute(f"SELECT block, count(*) FILTER (WHERE censored_24), count(*) FROM '{path}' GROUP BY 1").fetchall()
    return d, X, feats, {b: {"censored_24": c, "rows": n} for b, c, n in censored}


# ----------------------------------------------------------------------------- models


def fit_lgbm(Xtr, ytr, Xva, yva, leaves):
    params = dict(LGB_PARAMS, num_leaves=leaves)
    dtr = lgb.Dataset(Xtr, ytr, free_raw_data=False)
    dva = lgb.Dataset(Xva, yva, reference=dtr, free_raw_data=False)
    booster = lgb.train(params, dtr, num_boost_round=LGB_ROUNDS, valid_sets=[dva], callbacks=[lgb.early_stopping(LGB_EARLY, verbose=False)])
    return booster


class Logit:
    """Logistic regression by full-batch gradient descent on standardised, median-imputed features (L2 = LOGIT_L2)."""

    def __init__(self, Xtr, ytr):
        self.med = np.nanmedian(Xtr, axis=0)
        self.med = np.where(np.isnan(self.med), 0.0, self.med)
        Z = self._impute(Xtr)
        self.mu, self.sd = Z.mean(axis=0), Z.std(axis=0)
        self.sd = np.where(self.sd > 0, self.sd, 1.0)
        Z = (Z - self.mu) / self.sd
        n, p = Z.shape
        self.w, self.b = np.zeros(p), float(np.log(max(ytr.mean(), 1e-6) / max(1 - ytr.mean(), 1e-6)))
        for _ in range(LOGIT_ITERS):
            s = 1 / (1 + np.exp(-(Z @ self.w + self.b)))
            g = s - ytr
            self.w -= LOGIT_LR * (Z.T @ g / n + LOGIT_L2 * self.w / n)
            self.b -= LOGIT_LR * g.mean()

    def _impute(self, X):
        return np.where(np.isnan(X), self.med, X)

    def predict(self, X):
        Z = (self._impute(X) - self.mu) / self.sd
        return 1 / (1 + np.exp(-(Z @ self.w + self.b)))


# ----------------------------------------------------------------------------- metrics


def capacity_take(entry_slot, h24, X):
    """Which selected signals (in slot order) are taken with at most X / SIZE positions open; a position stays open 24 h."""
    cap = int(X / SIZE)
    taken = np.zeros(len(entry_slot), dtype=bool)
    open_slots = []
    for i in range(len(entry_slot)):
        s = entry_slot[i]
        open_slots = [o for o in open_slots if o > s - h24[i]]
        if len(open_slots) < cap:
            taken[i] = True
            open_slots.append(s)
    return taken


def day_series(days, pnl):
    out = {}
    for d, v in zip(days, pnl):
        out[d] = out.get(d, 0.0) + v
    return out


def bootstrap(per_day, boot, seed=SEED):
    a = np.array(list(per_day.values()), dtype=np.float64)
    if len(a) == 0:
        return {"days": 0, "mean": None, "lo": None, "hi": None, "p_one_sided": None}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(boot, len(a)))
    means = a[idx].mean(axis=1)
    return {"days": int(len(a)), "mean": float(a.mean()), "lo": float(np.percentile(means, 2.5)), "hi": float(np.percentile(means, 97.5)),
            "p_one_sided": float((means <= 0).mean()), "worst_day": float(a.min()), "positive_days": int((a > 0).sum())}


def cell_metrics(sel, d, ret, all_days, boot=0):
    """sel: boolean mask over the block's rows; ret: return per trade (multiple - 1) of the exit; SOL/day per capital level."""
    n = int(sel.sum())
    out = {"trades": n, "days": len(all_days), "trades_per_day": n / max(len(all_days), 1)}
    if n == 0:
        out.update({"mean_ret": None, "median_ret": None, "hit": None, "sol_day": {str(X): None for X in CAPITAL}})
        return out
    r = ret[sel]
    out.update({"mean_ret": float(r.mean()), "median_ret": float(np.median(r)), "hit": float(d["y"][sel].mean()),
                "stop_first": float(d["stop_first_24"][sel].mean())})
    pos = r[r > 0]
    out["top1_share"] = float(np.sort(r)[-max(1, int(math.ceil(0.01 * n))):].sum() / pos.sum()) if pos.sum() > 0 else None
    es, h24, days = d["entry_slot"][sel], d["h24_slots"][sel], d["day"][sel]
    out["sol_day"], out["taken_per_day"], out["boot"] = {}, {}, {}
    for X in CAPITAL:
        taken = capacity_take(es, h24, X)
        pnl = r[taken] * SIZE
        per = {dd: 0.0 for dd in all_days}
        per.update(day_series(days[taken], pnl))
        out["sol_day"][str(X)] = float(pnl.sum() / max(len(all_days), 1))
        out["taken_per_day"][str(X)] = float(taken.sum() / max(len(all_days), 1))
        if boot:
            out["boot"][str(X)] = bootstrap(per, boot)
            out["boot"][str(X)]["per_day"] = {k: float(v) for k, v in per.items()}
    return out


def choose_x(cell):
    """X* = the largest capital level with >= MIN_TRADES_PER_DAY taken trades per day; None when no level qualifies."""
    for X in sorted(CAPITAL, reverse=True):
        if cell["trades"] and cell["taken_per_day"][str(X)] >= MIN_TRADES_PER_DAY:
            return X
    return None


def holm(pvals):
    """Holm step-down: adjusted p per hypothesis (None stays None)."""
    items = [(p, i) for i, p in enumerate(pvals) if p is not None]
    items.sort()
    n = len(items)
    adj, running = [None] * len(pvals), 0.0
    for rank, (p, i) in enumerate(items):
        running = max(running, (n - rank) * p)
        adj[i] = min(1.0, running)
    return adj


def dsr(per_day, n_trials):
    """Deflated Sharpe ratio (Bailey & Lopez de Prado) of a daily P&L series against the expected best of n_trials."""
    a = np.array(list(per_day.values()), dtype=np.float64)
    T = len(a)
    if T < 3 or a.std(ddof=1) == 0:
        return {"sr": None, "dsr": None, "trials": n_trials, "days": T}
    sr = a.mean() / a.std(ddof=1)
    z = (a - a.mean()) / a.std(ddof=1)
    skew, kurt = float((z ** 3).mean()), float((z ** 4).mean())
    gamma = 0.5772156649
    from statistics import NormalDist
    nd = NormalDist()
    n = max(n_trials, 2)
    sr_max = (1 - gamma) * nd.inv_cdf(1 - 1 / n) + gamma * nd.inv_cdf(1 - 1 / (n * math.e))
    denom = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr * sr, 1e-9))
    stat = (sr - sr_max) * math.sqrt(T - 1) / denom
    return {"sr": float(sr), "sr_max_expected": float(sr_max), "dsr": float(nd.cdf(stat)), "trials": n_trials, "days": T, "skew": skew, "kurt": kurt}


def cscv(cell_chunk_pnl):
    """PBO over validation cells: cell_chunk_pnl (cells x S) of P&L per chunk; all C(S, S/2) splits; omega = oos rank of the is-best."""
    M = np.asarray(cell_chunk_pnl, dtype=np.float64)
    if M.shape[0] < 2 or M.shape[1] != CSCV_S:
        return {"pbo": None, "splits": 0}
    masks = np.array([[i in c for i in range(CSCV_S)] for c in itertools.combinations(range(CSCV_S), CSCV_S // 2)], dtype=np.float64)
    IS = M @ masks.T              # cells x splits
    OOS = M @ (1 - masks).T
    best = IS.argmax(axis=0)
    oos_best = OOS[best, np.arange(OOS.shape[1])]
    ranks = (OOS < oos_best[None, :]).sum(axis=0) + 1
    omega = ranks / (M.shape[0] + 1)
    lam = np.log(omega / (1 - omega))
    return {"pbo": float((lam < 0).mean()), "splits": int(masks.shape[0]), "cells": int(M.shape[0]), "omega_mean": float(omega.mean())}


def calibration(score, y, bins=10):
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (score >= lo) & (score < hi) if hi < 1 else (score >= lo) & (score <= hi)
        out.append({"lo": float(lo), "hi": float(hi), "n": int(m.sum()), "pred": float(score[m].mean()) if m.any() else None, "obs": float(y[m].mean()) if m.any() else None})
    return out


# ----------------------------------------------------------------------------- the study


def run(features_path, out_dir, boot=BOOT, moments=MOMENTS, models=MODELS, leaves=LEAVES):
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    d, X, feats, censored = load(features_path)
    blocks = {b: d["block"] == b for b in ("train", "val", "test")}
    days = {b: sorted(set(d["day"][blocks[b]])) for b in blocks}
    exits24 = {r: d[f"x_{r}_24"] - 1.0 for r in EXITS_24}
    exits72 = {r: d[f"x_{r}_72"] - 1.0 for r in EXITS_24}
    result = {"features_sha256": _sha(features_path), "feature_columns": feats, "n_features": len(feats), "rows": {b: int(m.sum()) for b, m in blocks.items()},
              "days": {b: len(v) for b, v in days.items()}, "censored_24": censored, "params": {"lgbm": LGB_PARAMS, "rounds": LGB_ROUNDS, "early": LGB_EARLY,
              "logit": {"l2": LOGIT_L2, "iters": LOGIT_ITERS, "lr": LOGIT_LR}, "ks": KS, "leaves": leaves, "moments": moments, "models": models, "exits_24": EXITS_24,
              "capital": CAPITAL, "size_sol": SIZE, "min_trades_per_day": MIN_TRADES_PER_DAY, "boot": boot, "seed": SEED, "cscv_s": CSCV_S},
              "lightgbm_version": getattr(lgb, "__version__", None), "targets": {}, "cells_val": []}
    scores_out = []
    for m in TARGETS:
        y_all = d[f"hit_{m}_24"].astype(np.float64)
        d["y"] = y_all
        tres = {"target": f"hit_{m}_24", "base_rate": {b: float(y_all[blocks[b]].mean()) if blocks[b].any() else None for b in blocks}, "cells": 0}
        cells = []        # (key dict, val metrics, selection masks on val / test, score arrays)
        chunk_pnl = []
        for moment in moments:
            mm = d["moment"] == moment
            tr, va, te = mm & blocks["train"], mm & blocks["val"], mm & blocks["test"]
            if tr.sum() < 1000 or va.sum() < 100 or y_all[tr].sum() < 20:
                tres.setdefault("skipped_moments", []).append({"moment": moment, "train": int(tr.sum()), "val": int(va.sum()), "pos": int(y_all[tr].sum())})
                continue
            fitted = []
            for model in models:
                if model == "lgbm":
                    if lgb is None:
                        continue
                    for nl in leaves:
                        b = fit_lgbm(X[tr], y_all[tr], X[va], y_all[va], nl)
                        fitted.append((model, nl, b, b.predict(X[tr], num_iteration=b.best_iteration), b.predict(X[va], num_iteration=b.best_iteration),
                                       b.predict(X[te], num_iteration=b.best_iteration)))
                else:
                    lg = Logit(X[tr], y_all[tr])
                    fitted.append((model, None, lg, lg.predict(X[tr]), lg.predict(X[va]), lg.predict(X[te])))
            va_idx, te_idx = np.flatnonzero(va), np.flatnonzero(te)
            # CSCV chunks: the validation rows of this moment in slot order, S contiguous chunks
            chunk_of = np.minimum((np.arange(len(va_idx)) * CSCV_S) // max(len(va_idx), 1), CSCV_S - 1)
            dva = {k: v[va] for k, v in d.items() if isinstance(v, np.ndarray)}
            dte = {k: v[te] for k, v in d.items() if isinstance(v, np.ndarray)}
            for model, nl, obj, s_tr, s_va, s_te in fitted:
                for k in KS:
                    thr = float(np.quantile(s_tr, 1 - k / 100))
                    sel_va, sel_te = s_va >= thr, s_te >= thr
                    for r in EXITS_24:
                        met = cell_metrics(sel_va, dva, exits24[r][va], days["val"])
                        key = {"target": m, "moment": moment, "model": model, "num_leaves": nl, "k": k, "exit": r, "threshold": thr,
                               "best_iteration": int(getattr(obj, "best_iteration", 0) or 0)}
                        met["x_star"] = choose_x(met)
                        cells.append((key, met, sel_va, sel_te, s_va, s_te, obj))
                        pnl = np.where(sel_va, exits24[r][va] * SIZE, 0.0)
                        chunk_pnl.append([float(pnl[chunk_of == c].sum()) for c in range(CSCV_S)])
                        result["cells_val"].append(key | {"val": {kk: vv for kk, vv in met.items() if kk not in ("boot", "taken_per_day")}})
        tres["cells"] = len(cells)
        tres["cscv"] = cscv(chunk_pnl) if chunk_pnl else {"pbo": None}
        eligible = [(c[1]["sol_day"][str(c[1]["x_star"])], -c[1]["trades"], -i, i) for i, c in enumerate(cells) if c[1]["x_star"] is not None]
        if not eligible:
            tres["chosen"] = None
            result["targets"][f"x{m}"] = tres
            continue
        best_i = max(eligible)[3]
        key, met, sel_va, sel_te, s_va, s_te, obj = cells[best_i]
        key["x_star"] = met["x_star"]
        tres["chosen"] = key | {"val": {kk: vv for kk, vv in met.items() if kk != "boot"}}
        # the logistic baseline's own best cell (descriptive) and "all coins" (no selection) on test
        logit_el = [(c[1]["sol_day"][str(c[1]["x_star"])], -c[1]["trades"], -i, i) for i, c in enumerate(cells) if c[1]["x_star"] is not None and c[0]["model"] == "logit"]
        te_mask_m = (d["moment"] == key["moment"]) & blocks["test"]
        dte = {kk: v[te_mask_m] for kk, v in d.items() if isinstance(v, np.ndarray)}
        xs = key["x_star"] = met["x_star"]
        test = cell_metrics(sel_te, dte, exits24[key["exit"]][te_mask_m], days["test"], boot=boot)
        # the ratio and the gate use the CHOSEN MOMENT's test base rate (LAB must-fix on #530): mig and curve entries differ;
        # the all-moments rate stays a report figure (base_rate["test"])
        moment_base = float(dte["y"].mean()) if len(dte["y"]) else None
        test["base_rate_moment"] = moment_base
        test["precision_vs_base"] = (test["hit"] / moment_base) if test["hit"] is not None and moment_base else None
        test["calibration"] = calibration(s_te, dte["y"])
        test["auc"] = _auc(s_te, dte["y"])
        test["trades_per_test_day"] = {dd: int((dte["day"][sel_te] == dd).sum()) for dd in days["test"]}
        test["dsr"] = dsr(test["boot"][str(xs)]["per_day"], len(cells)) if test["trades"] else None
        test["exits_descriptive"] = {f"{r}_{H}": cell_metrics(sel_te, dte, (exits24 if H == "24" else exits72)[r][te_mask_m], days["test"])
                                     for r in EXITS_24 for H in ("24", "72")}
        for ex in test["exits_descriptive"].values():
            ex.pop("boot", None)
        tres["test"] = test
        tres["all_coins_test"] = cell_metrics(np.ones(int(te_mask_m.sum()), dtype=bool), dte, exits24[key["exit"]][te_mask_m], days["test"], boot=boot)
        tres["all_coins_test"]["boot"] = {X: {kk: vv for kk, vv in b.items() if kk != "per_day"} for X, b in tres["all_coins_test"]["boot"].items()}
        if logit_el:
            li = max(logit_el)[3]
            lkey, lmet, _, lsel_te, _, _, _ = cells[li]
            lkey = lkey | {"x_star": lmet["x_star"]}
            lte = (d["moment"] == lkey["moment"]) & blocks["test"]
            dlt = {kk: v[lte] for kk, v in d.items() if isinstance(v, np.ndarray)}
            lt = cell_metrics(lsel_te, dlt, exits24[lkey["exit"]][lte], days["test"], boot=boot)
            lt["boot"] = {X: {kk: vv for kk, vv in b.items() if kk != "per_day"} for X, b in lt["boot"].items()}
            lbase = float(dlt["y"].mean()) if len(dlt["y"]) else None
            lt["base_rate_moment"] = lbase
            lt["precision_vs_base"] = (lt["hit"] / lbase) if lt["hit"] is not None and lbase else None
            tres["logit_reference"] = lkey | {"val_sol_day": lmet["sol_day"], "test": lt}
        if key["model"] == "lgbm":
            imp = obj.feature_importance(importance_type="gain")
            order = np.argsort(imp)[::-1][:15]
            tres["importance_gain"] = [{"feature": feats[i], "gain": float(imp[i]), "share": float(imp[i] / imp.sum()) if imp.sum() else None} for i in order]
            obj.save_model(f"{out_dir}/lgbm-x{m}.txt")
            tres["model_file_sha256"] = _sha(f"{out_dir}/lgbm-x{m}.txt")
        scores_out.append({"target": f"x{m}", "moment": key["moment"], "keys": dte["key"], "ts": dte["t"], "scores": s_te, "selected": sel_te})
        result["targets"][f"x{m}"] = tres
    # Holm over the targets at X*, then the gate
    names = [n for n in result["targets"] if result["targets"][n].get("test")]
    ps = [result["targets"][n]["test"]["boot"][str(result["targets"][n]["chosen"]["x_star"])]["p_one_sided"] for n in names]
    for n, p_adj in zip(names, holm(ps)):
        t = result["targets"][n]
        xs = str(t["chosen"]["x_star"])
        b = t["test"]["boot"][xs]
        t["holm_p"] = p_adj
        t["gate"] = {"holm_p_below_alpha": p_adj is not None and p_adj < ALPHA, "lower_bound_positive": b["lo"] is not None and b["lo"] > 0,
                     "trades_per_day_ok": t["test"]["taken_per_day"][xs] >= MIN_TRADES_PER_DAY,
                     "top1_share_ok": t["test"]["top1_share"] is not None and t["test"]["top1_share"] < 0.5,
                     "precision_ok": t["test"]["precision_vs_base"] is not None and t["test"]["precision_vs_base"] >= 2}
        t["gate"]["passed"] = all(t["gate"].values())
        for X, bb in t["test"]["boot"].items():
            bb.pop("per_day", None)
    result["seconds"] = round(time.time() - t0, 1)
    with open(f"{out_dir}/model.json", "w") as f:
        json.dump(result, f, indent=1, default=_json_default)
    import pyarrow as pa
    import pyarrow.parquet as pq
    pq.write_table(pa.Table.from_pylist([{k: (v if not isinstance(v, float) or not math.isnan(v) else None) for k, v in c.items() if k != "val"} | {"val_json": json.dumps(c["val"], default=_json_default)}
                                         for c in result["cells_val"]]), f"{out_dir}/cells-val.parquet")
    for s in scores_out:
        pq.write_table(pa.table({"key": s["keys"], "t": s["ts"], "moment": [s["moment"]] * len(s["keys"]), "score": s["scores"], "selected": s["selected"]}),
                       f"{out_dir}/scores-{s['target']}.parquet")
    write_md(result, f"{out_dir}/model.md")
    return result


def _auc(score, y):
    pos, neg = score[y == 1], score[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return str(o)


def write_md(res, path):
    f = lambda v, nd=2: "–" if v is None else (f"{v:,.{nd}f}" if isinstance(v, (int, float)) else str(v))  # noqa: E731
    lines = [f"# x10-jager stage C — one chronological split, test once (features {res['features_sha256'][:8]}…, lightgbm {res['lightgbm_version']})", "",
             f"Rows train / val / test: {res['rows'].get('train')} / {res['rows'].get('val')} / {res['rows'].get('test')} (censored_24 dropped: "
             + ", ".join(f"{b} {v['censored_24']} of {v['rows']}" for b, v in res["censored_24"].items()) + f"); days {res['days']}; {res['n_features']} features.", "",
             "| target | base rate test | chosen (moment, model, leaves, k, exit) | X* | val SOL/day | test trades/day | test hit | prec/base | test SOL/day at X* [95 %] | p | Holm p | top-1 % | gate |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, t in res["targets"].items():
        if not t.get("chosen"):
            lines.append(f"| {n} | {f(t['base_rate'].get('test'), 4)} | no eligible cell (cells {t['cells']}) | – | – | – | – | – | – | – | – | – | – |")
            continue
        c, te, xs = t["chosen"], t["test"], str(t["chosen"]["x_star"])
        b = te["boot"][xs]
        lines.append(f"| {n} | {f(t['base_rate'].get('test'), 4)} | {c['moment']}, {c['model']}, {c['num_leaves']}, {c['k']} %, {c['exit']} | {xs} | {f(c['val']['sol_day'][xs])} | "
                     f"{f(te['taken_per_day'][xs])} | {f(te['hit'], 4)} | {f(te['precision_vs_base'])} | {f(b['mean'])} [{f(b['lo'])}; {f(b['hi'])}] | {f(b['p_one_sided'], 4)} | "
                     f"{f(t.get('holm_p'), 4)} | {f(te['top1_share'], 3)} | {'PASS' if t['gate']['passed'] else 'fail'} |")
    lines += ["", "## Per target", ""]
    for n, t in res["targets"].items():
        lines.append(f"### {n} ({t['target']}), validation cells {t['cells']}, CSCV PBO {f(t.get('cscv', {}).get('pbo'), 3)}")
        if not t.get("chosen"):
            lines.append("no eligible validation cell (no capital level with >= 5 taken trades/day)\n")
            continue
        te = t["test"]
        lines.append("| X | taken/day | SOL/day | 95 % | p | positive days |\n|---|---|---|---|---|---|")
        for X in CAPITAL:
            b = te["boot"][str(X)]
            lines.append(f"| {X} | {f(te['taken_per_day'][str(X)])} | {f(b['mean'])} | [{f(b['lo'])}; {f(b['hi'])}] | {f(b['p_one_sided'], 4)} | {b.get('positive_days')}/{b['days']} |")
        lines.append(f"\nEV per trade: mean {f(te['mean_ret'], 4)}, median {f(te['median_ret'], 4)}; stop first {f(te.get('stop_first'), 3)}; AUC {f(te['auc'], 3)}; "
                     f"trades per test day {te['trades_per_test_day']}; DSR {f((te.get('dsr') or {}).get('dsr'), 3)} (trials {t['cells']}).")
        lines.append("\nExits (descriptive, same selection): " + ", ".join(f"{k} {f(v['sol_day']['100'])}" for k, v in te["exits_descriptive"].items()) + " SOL/day at 100.")
        if t.get("importance_gain"):
            lines.append("\nImportance (gain share): " + ", ".join(f"{i['feature']} {f(i['share'], 3)}" for i in t["importance_gain"][:10]))
        ac = t.get("all_coins_test", {})
        lines.append(f"\nAll coins of the moment on test, same exit: SOL/day at 100 {f((ac.get('boot') or {}).get('100', {}).get('mean'))}, hit {f(ac.get('hit'), 4)}.")
        lr = t.get("logit_reference")
        if lr:
            lines.append(f"Logistic reference ({lr['moment']}, k {lr['k']} %, {lr['exit']}): test SOL/day at {lr['x_star']} {f(lr['test']['boot'][str(lr['x_star'])]['mean'])}.")
        lines.append("")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="x10-jager stage C: models, validation choice, test once, Holm, bootstrap, DSR / CSCV")
    ap.add_argument("--features", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--boot", type=int, default=BOOT)
    a = ap.parse_args(argv)
    res = run(a.features, a.out, a.boot)
    print(json.dumps({n: {"chosen": t.get("chosen") and {k: t["chosen"][k] for k in ("moment", "model", "num_leaves", "k", "exit", "x_star")},
                          "gate": t.get("gate"), "cells": t["cells"]} for n, t in res["targets"].items()}, default=_json_default))


if __name__ == "__main__":
    main()
