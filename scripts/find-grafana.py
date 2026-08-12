import json, subprocess, os
def api(m, p):
    e = dict(os.environ); e['TRUENAS_API_METHOD'] = m; e['TRUENAS_API_PARAMS'] = json.dumps(p)
    r = subprocess.run(['node', 'truenas-wss-admin.mjs'], capture_output=True, text=True, env=e)
    return json.loads(r.stdout) if r.stdout else {}
# zoek grafana specifiek met train-info in de catalogus
r = api('app.available', [[['name','=','grafana']], {'limit': 10}])
res = r.get('result') or []
for a in res:
    print('name:', a.get('name'), '| train:', a.get('train'), '| version:', a.get('latest_version') or a.get('version'), '| scheme_version:', a.get('latest_app_version'))
