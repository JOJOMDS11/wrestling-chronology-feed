#!/usr/bin/env python3
"""Publish admin-approved OK.ru associations and provisional weekly RAW entries.
No token on Oracle. Runs in GitHub Actions with contents:write.
"""
import datetime as dt
from zoneinfo import ZoneInfo
import json
import os
from pathlib import Path
import re
import urllib.request

BASE = Path(__file__).resolve().parents[2]
API = os.getenv('JOJO_APPROVAL_URL', 'https://jojo-wrestling.tail90c651.ts.net/api/jojo-monitor/approved')


def read(path):
    return json.loads((BASE / path).read_text(encoding='utf-8'))


def save(path, obj):
    (BASE / path).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def add_weekly_raw(updates, now=None):
    now = now or dt.datetime.now(ZoneInfo('America/New_York'))
    # RAW normalmente acontece segunda-feira; registrar como PROVISÓRIO na terça
    # depois das 08:00 ET. Não afirmar que houve transmissão sem fonte oficial.
    wd = now.weekday()
    if wd == 0 or (wd == 1 and now.hour < 8):
        return 0
    days_from_monday = wd - 0 if wd > 0 else 7
    monday = (now.date() - dt.timedelta(days=days_from_monday))
    if (now.date() - monday).days > 6:
        return 0
    date = monday.isoformat()
    existing = updates.get('events', [])
    if any(e.get('company') == 'wwe' and e.get('date') == date
           and ('raw' in str(e.get('show','')).lower() or 'raw' in str(e.get('title','')).lower()) for e in existing):
        return 0
    try:
        wwe = read('wwe.json').get('catalogue', {}).get('episodes', [])
        if any(e.get('date') == date and ('raw' in str(e.get('show','')).lower()
                or 'raw' in str(e.get('title','')).lower()) for e in wwe):
            return 0
    except (OSError, ValueError):
        pass
    existing.append({'id': 'auto:wwe:'+date+':raw', 'company': 'wwe', 'date': date,
                     'year': monday.year, 'title': 'Monday Night RAW', 'show': 'RAW',
                     'status': 'awaiting_video', 'provisional': True,
                     'description': 'Registro semanal provisório; confirmar a programação oficial.',
                     'sources': []})
    return 1


def consume_approvals(updates, data):
    events = {str(x.get('id')): x for x in updates.get('events', []) if isinstance(x, dict) and x.get('id')}
    used = set()
    for cat in ('wwe','aew','nxt','tna','roh','njpw','wcw','ecw'):
        try:
            episodes = read(cat+'.json').get('catalogue', {}).get('episodes', [])
        except (OSError, ValueError):
            continue
        for ep in episodes:
            for src in ep.get('sources', []) if isinstance(ep,dict) else []:
                if isinstance(src,dict) and src.get('type') == 'okru':
                    used.add(str(src.get('videoId') or ''))
    for e in events.values():
        for src in e.get('sources', []):
            if isinstance(src,dict) and src.get('type') == 'okru':
                used.add(str(src.get('videoId') or ''))
    changed = 0
    for a in data.get('approved', [])[:250]:
        if not isinstance(a,dict):
            continue
        vid = str(a.get('videoId',''))
        eid = str(a.get('eventId',''))
        if not re.fullmatch(r'[0-9]{8,20}',vid) or not re.fullmatch(r'[A-Za-z0-9:_-]{8,180}',eid):
            continue
        event = events.get(eid)
        if not event or vid in used or event.get('sources'):
            continue
        event['sources'] = [{'type': 'okru', 'videoId': vid, 'url': 'https://ok.ru/video/'+vid,
                             'label': 'OK.ru · Revisão administrativa'}]
        event['status'] = 'video_available'
        event['provisional'] = False
        event['approvedAt'] = str(a.get('approvedAt',''))[:40]
        used.add(vid)
        changed += 1
    return changed


def main():
    updates = read('updates.json')
    nraw = add_weekly_raw(updates)
    approved = 0
    try:
        req = urllib.request.Request(API, headers={'User-Agent':'JOJO-GitHub-Action/2.0'})
        with urllib.request.urlopen(req, timeout=18) as response:
            content = response.read(250_001)
        if len(content) > 250_000:
            raise ValueError('Fila grande demais')
        data = json.loads(content.decode('utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('approved'),list):
            raise ValueError('Fila inválida')
        approved = consume_approvals(updates,data)
    except Exception as ex:
        # Never destroy existing catalogues or print tokens. RAW can still be added.
        print('Fila de aprovações temporariamente indisponível:',type(ex).__name__)
    if nraw or approved:
        updates['generatedAt'] = dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00','Z')
        save('updates.json',updates)
    print('RAW provisórias adicionadas:',nraw,'Aprovações publicadas:',approved)


if __name__ == '__main__':
    main()
