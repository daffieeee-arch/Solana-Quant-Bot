"""Agenda idea 1, step 3 (lab/reports/2026-10-17-drain-timing.md, E37): time-to-drain at the trigger, point-in-time. Fit on days 1-9 (triggers before 2026-09-20), measure on days
10-18: single-feature AUCs, a logistic model (statsmodels) and a LightGBM model for 'drain within 10 / 60 min after t';
then the veto overlay on the H4/H5 positions at score quantiles: sum per day, worst day, lost-all, with the day bootstrap."""
import duckdb, numpy as np, sys
import statsmodels.api as sm
import lightgbm as lgb
import os
DATA = os.environ.get("LAB_DATA_ROOT", "/home/chupa/Solana-project/data-old-faithful-one/lab"); W = os.path.join(os.environ.get("LAB_WALLETS_OUT", f"{DATA}/wallets"), "winners-v1")
con=duckdb.connect(); con.execute("SET memory_limit='800MB'")
con.execute(f"""CREATE TABLE f AS SELECT *, (t - create_slot) * ms / 60000 AS age_min,
  CASE WHEN first_drain_after IS NULL THEN NULL ELSE (first_drain_after - t) * ms / 60000 END AS drain_min,
  (first_drain_after IS NOT NULL AND (first_drain_after - t) * ms / 60000 <= 10) AS y10, (first_drain_after IS NOT NULL AND (first_drain_after - t) * ms / 60000 <= 60) AS y60,
  t >= 450144000 AS test, creator = '11111111111111111111111111111111' AS creator_unknown
  FROM '{W}/d2_trigger_features.parquet'""")
print("triggers:", con.execute("SELECT count(*), round(avg(y10::INT),3), round(avg(y60::INT),3), round(median(drain_min),1), sum(test::INT) FROM f").fetchone())
feats = ["age_min","b0","creator_prior_pools","creator_never_traded","creator_unknown","n_trades_pre","n_traders_pre","n_buyers_pre","inflow_pre","vol_pre","inflow_1m","inflow_5m","n_trades_1m",
         "tip_share_pre","farmer_share_pre","creator_trades_pre","creator_net_pre","wd_pre","dep_pre","depth_t","depth_max_pre","max_buy_pre","max_sell_pre"]
cols = ["pool","t","test","y10","y60","drain_min"] + feats
rows = con.execute(f"SELECT {', '.join(cols)} FROM f").fetchall()
import pandas as pd
df = pd.DataFrame(rows, columns=cols)
for c in feats: df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
df[feats] = df[feats].fillna(0.0)
def auc(y, s):
    y = np.asarray(y, bool); s = np.asarray(s, float); pos, neg = s[y], s[~y]
    if len(pos) == 0 or len(neg) == 0: return float("nan")
    from scipy.stats import rankdata
    r = rankdata(np.concatenate([pos, neg])); return (r[:len(pos)].sum() - len(pos)*(len(pos)+1)/2) / (len(pos)*len(neg))
tr, te = df[~df.test], df[df.test]
print(f"\n== single-feature AUC on days 10-18 (n {len(te)}, y10 rate {te.y10.mean():.3f}, y60 {te.y60.mean():.3f}); sign = direction of more drains")
for c in feats:
    a10, a60 = auc(te.y10, te[c]), auc(te.y60, te[c])
    print(f"{c:20s} AUC10 {a10:.3f}  AUC60 {a60:.3f}")
def fit_eval(y, name):
    X = tr[feats].values; Xt = te[feats].values
    mu, sd = X.mean(0), X.std(0) + 1e-9; Xs, Xts = (X-mu)/sd, (Xt-mu)/sd
    lr = sm.Logit(tr[y].astype(float).values, sm.add_constant(Xs)).fit(disp=0, maxiter=200, method="lbfgs")
    p_lr = lr.predict(sm.add_constant(Xts))
    ds = lgb.Dataset(X, label=tr[y].astype(int).values, feature_name=feats)
    gbm = lgb.train({"objective": "binary", "learning_rate": 0.03, "num_leaves": 15, "min_data_in_leaf": 30, "bagging_fraction": 0.8, "bagging_freq": 1, "feature_fraction": 0.8, "verbose": -1, "seed": 7}, ds, num_boost_round=200)
    p_gb = gbm.predict(Xt)
    print(f"\n== {name}: logistic AUC {auc(te[y], p_lr):.3f}, LightGBM AUC {auc(te[y], p_gb):.3f} (fit on days 1-9 n {len(tr)}, test days 10-18 n {len(te)})")
    imp = sorted(zip(feats, gbm.feature_importance("gain").round(0)), key=lambda x: -x[1])[:8]
    print("   top LightGBM features:", imp)
    return p_lr, p_gb
p10_lr, p10_gb = fit_eval("y10", "drain within 10 min")
p60_lr, p60_gb = fit_eval("y60", "drain within 60 min")
te = te.assign(p10_gb=p10_gb, p60_gb=p60_gb, p10_lr=p10_lr, p60_lr=p60_lr)
con.register("sc", te[["pool","t","p10_gb","p60_gb","p10_lr","p60_lr"]])
con.execute(f"CREATE TABLE pos AS SELECT j.h, j.d, j.pool, j.t, j.day, j.pnl, j.lost_all, sc.p10_gb, sc.p60_gb, sc.p10_lr, sc.p60_lr FROM '{W}/d1_h45_pools.parquet' j JOIN sc USING (pool, t) WHERE j.day >= '2026-09-20'")
print("\n== positions on days 10-18 with a score:", con.execute("SELECT h, d, count(*) FROM pos GROUP BY 1,2 ORDER BY 1,2").fetchall())
rng = np.random.default_rng(7)
def boot_lower(day_sums):
    a = np.array(day_sums); n = len(a); 
    if n < 3: return float("nan")
    m = np.array([a[rng.integers(0, n, n)].mean() for _ in range(2000)]); return float(np.percentile(m, 2.5))
print("\n== veto overlay on days 10-18: score | quantile cut (share vetoed) | h d | n kept | Σ kept | Σ all | worst day kept/all | lost-all kept/all | mean/day kept, boot 2.5 % | mean/day all, boot 2.5 %")
for score in ("p60_gb", "p10_gb", "p60_lr"):
    for q in (0.0, 0.1, 0.2, 0.3, 0.5):
        cut = float(np.quantile(te[score], 1-q)) if q > 0 else 1e9
        for h, d in (("H5",1),("H5",2),("H4",1),("H4",2)):
            r = con.execute(f"""WITH v AS (SELECT *, {score} >= {cut} AS veto FROM pos WHERE h='{h}' AND d={d}),
                dd AS (SELECT day, sum(pnl) s_all, sum(CASE WHEN NOT veto THEN pnl ELSE 0 END) s_keep FROM v GROUP BY 1)
                SELECT (SELECT count(*) FROM v WHERE NOT veto), (SELECT round(sum(pnl)/1e9,1) FROM v WHERE NOT veto), (SELECT round(sum(pnl)/1e9,1) FROM v),
                  round(min(s_keep)/1e9,1), round(min(s_all)/1e9,1), (SELECT round(avg(lost_all::INT),3) FROM v WHERE NOT veto), (SELECT round(avg(lost_all::INT),3) FROM v),
                  list(s_keep/1e9 ORDER BY day), list(s_all/1e9 ORDER BY day) FROM dd""").fetchone()
            keep, all_ = r[7], r[8]
            print(score, f"q{q:.1f}", h, d, r[0], r[1], r[2], r[3], r[4], r[5], r[6], f"{np.mean(keep):.2f} [{boot_lower(keep):.2f}]", f"{np.mean(all_):.2f} [{boot_lower(all_):.2f}]")
te[["pool","t","y10","y60","drain_min","p10_gb","p60_gb","p10_lr","p60_lr"]].to_parquet(f"{W}/d3_scores_test.parquet")
print("saved")
