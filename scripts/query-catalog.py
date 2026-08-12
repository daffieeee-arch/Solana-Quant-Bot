import json, subprocess, os
def api(m, p):
    e = dict(os.environ); e['TRUENAS_API_METHOD'] = m; e['TRUENAS_API_PARAMS'] = json.dumps(p)
    r = subprocess.run(['node', 'truenas-wss-admin.mjs'], capture_output=True, text=True, env=e)
    return json.loads(r.stdout) if r.stdout else {}
# zoek naar clickhouse/grafana-lite-apps in de catalogus (zonder categorie-filterkap)
r = api('app.available', [[], {'limit': 200}])
res = r.get('result') or []
names = sorted(set(a.get('name','') for a in res))
hits = [n for n in names if any(k in n.lower() for k in ['clickhouse','grafana','superset','metabase','duckdb','timescale','postgres','influx','prometheus','loki','netdata','glances','victoria'])]
print('totaal beschikbaar:', len(names))
print('relevante hits:')
for h in hits: print('  •', h)
