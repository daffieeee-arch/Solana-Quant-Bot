import json, sys
with open('data-bot-v5/opportunities.ndjson') as fp:
    for line in fp:
        try:
            o = json.loads(line)
        except Exception:
            continue
        if str(o.get('mint', '')).startswith('EDhUFVW'):
            print('pairId:', o.get('pairId'))
            print('mint:', o.get('mint'))
            break