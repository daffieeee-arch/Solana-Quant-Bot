import hashlib, pathlib, os
ROOT = '/opt/data/solana-paper-scanner'
SKIP = {'node_modules', '.git', 'dist', 'data-bot-v5', '__pycache__'}
def collect(root_abs):
    out = []
    for dirpath, dirnames, filenames in os.walk(root_abs):
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if os.path.islink(full): continue
            # rel t.o.v. de collect-root én prefix met de root-naam
            rel = os.path.relpath(full, root_abs)
            out.append((full, rel))
    return sorted(out, key=lambda x: x[1])
items = collect(os.path.join(ROOT, 'src')) + collect(os.path.join(ROOT, 'frontend'))
h = hashlib.sha256()
names = sorted(set(rel for _, rel in items))
h.update('\n'.join(names).encode())
for full, rel in items:
    with open(full, 'rb') as fh:
        h.update(fh.read())
for extra in ['package.json', 'Dockerfile', 'tsconfig.json']:
    ep = os.path.join(ROOT, extra)
    if os.path.exists(ep):
        with open(ep, 'rb') as fh: h.update(fh.read())
src_count = len(collect(os.path.join(ROOT, 'src')))
fr_count = len(collect(os.path.join(ROOT, 'frontend')))
print('src:', src_count, '| frontend:', fr_count, '| totaal:', len(items))
print('CORRECTE hash:', h.hexdigest())