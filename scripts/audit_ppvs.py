#!/usr/bin/env python3
"""Audit documented PPV references versus the site's public catalogue.

Absence is provable only against the explicit references in config/ppv_reference.json.
The report never claims the entire historical PPV calendar is complete.
No video link is introduced or approved here.
"""
import datetime as dt
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATS=('wwe','nxt','tna','aew','roh','njpw','wcw','ecw')

def load(name):
    return json.loads((ROOT/name).read_text(encoding='utf-8'))

def norm(text):
    x=unicodedata.normalize('NFKD',str(text).lower())
    return re.sub(r'[^a-z0-9]+','', ''.join(c for c in x if not unicodedata.combining(c)))

def relevant(row):
    return str(row.get('show','')).strip().lower() in ('ppv','ple','premium live event','weekly ppv')

def keyrow(row):
    return (row.get('company'),row.get('date'))

def find_match(ref, rows):
    available=[row for row in rows if row.get('date')==ref.get('date') and row.get('company')==ref.get('company') and relevant(row)]
    target=norm(ref.get('title'))
    for row in available:
        title=norm(row.get('title'))
        if title==target or (len(target)>10 and len(title)>10 and (title in target or target in title)):
            return row
    # Only a single PPV on that date: likely the same event, but do not merge
    # named double-cards or pre-shows into unrelated recordings.
    if len(available)==1 and not any(k in target for k in ('noite1','noite2','buyin','countdown')):
        return available[0]
    return None

def report(now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    rows=[]
    sources_failed=[]
    for comp in CATS:
        try:
            eps=load(comp+'.json')['catalogue']['episodes']
            rows.extend([dict(x,company=comp) for x in eps if isinstance(x,dict)])
        except (KeyError, OSError, ValueError, TypeError):
            sources_failed.append(comp+'.json')
    try:
        rows += [dict(x) for x in load('updates.json').get('events',[]) if isinstance(x,dict)]
    except (OSError,ValueError):
        sources_failed.append('updates.json')
    refs=load('config/ppv_reference.json')['events']
    wiki_sources={}
    try:
        wiki=load('config/ppv_wikipedia_reference.json')
        wiki_sources=wiki.get('sources',{})
        # A curated reference takes precedence on identical company/date/title.
        seen={(str(e.get('company')),str(e.get('date')),norm(e.get('title'))) for e in refs}
        for e in wiki.get('events',[]):
            if not isinstance(e,dict):continue
            k=(str(e.get('company')),str(e.get('date')),norm(e.get('title')))
            if k not in seen and e.get('company') in CATS and not find_match(e,refs):
                refs.append(e);seen.add(k)
    except (OSError, ValueError, TypeError):
        pass
    out=[]
    for e in refs:
        match=find_match(e,rows)
        future=str(e.get('date',''))>now.date().isoformat()
        state='upcoming' if future else ('missing' if match is None else ('with_sources' if match.get('sources') else 'without_video'))
        out.append({'id':e['id'],'company':e['company'],'date':e['date'],'year':e['year'],
                    'title':e['title'],'official_url':e.get('official_url'),
                    'referenceType':e.get('referenceType','curated'),
                    'state':state,'eventId':match.get('id') if match else None,
                    'sourcesCount':len(match.get('sources') or []) if match else 0})
    out.sort(key=lambda x:(x['date'],x['company'],x['title']))
    years={}
    for row in rows:
        if relevant(row) and re.fullmatch(r'20\d{2}-\d{2}-\d{2}', str(row.get('date',''))):
            code=str(row.get('date',''))[:4]
            obj=years.setdefault(code, {'companies':{},'cataloguePpvs':0,'references':0,'missing':0,'withoutVideo':0,'withSources':0,'upcoming':0})
            obj['cataloguePpvs']+=1
            obj['companies'][row['company']]=obj['companies'].get(row['company'],0)+1
    for row in out:
        obj=years.setdefault(str(row['year']), {'companies':{},'cataloguePpvs':0,'references':0,'missing':0,'withoutVideo':0,'withSources':0,'upcoming':0})
        obj['references']+=1
        name={'missing':'missing','without_video':'withoutVideo','with_sources':'withSources','upcoming':'upcoming'}[row['state']]
        obj[name]+=1
    return {'schemaVersion':2,'checkedAt':now.isoformat().replace('+00:00','Z'),'referenceCoverage':'partial',
            'notice':'Referências da Wikipédia são listas históricas independentes; não substituem calendários oficiais. Anos com fonte indisponível não estão completos. A auditoria não altera vídeos.',
            'wikipediaSources':wiki_sources,
            'catalogueFailures':sources_failed,'years':years,'references':out}

def main():
    data=report()
    (ROOT/'ppv_audit.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Auditoria:',len(data['references']),'referências;',sum(x['state']=='missing' for x in data['references']),'ausentes da cronologia')

if __name__ == '__main__':main()
