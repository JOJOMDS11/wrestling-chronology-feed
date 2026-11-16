#!/usr/bin/env python3
"""Fetch *documented historical event metadata* from Wikipedia list tables.

A Wikipedia row is an audit reference, NOT proof of video availability or rights.
Each site is isolated and failed fetches retain the previous successful snapshot.
The file is used by audit_ppvs.py, never blindly added as a playable event.
"""
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'config/ppv_wikipedia_reference.json'
PAGES = {
    'wwe': 'List_of_WWE_pay-per-view_and_livestreaming_supercards',
    'wcw': 'List_of_JCP/WCW_closed-circuit_events_and_pay-per-view_events',
    'ecw': 'List_of_ECW_supercards_and_pay-per-view_events',
    'tna': 'List_of_TNA_pay-per-view_and_livestreaming_events',
    'roh': 'List_of_Ring_of_Honor_pay-per-view_and_livestreaming_events',
    'aew': 'List_of_All_Elite_Wrestling_pay-per-view_events',
    'njpw': 'List_of_major_NJPW_events',
    'nxt': 'NXT_TakeOver',
}
MONTHS = {x:i+1 for i,x in enumerate('january february march april may june july august september october november december'.split())}
MONTHS.update({k[:3]:v for k,v in list(MONTHS.items())})
MONTH_RE = '|'.join(sorted(MONTHS, key=len, reverse=True))
DATE = re.compile(r'(?i)\b(' + MONTH_RE + r')\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*[,–-]\s*(\d{1,2}))?(?:,?\s*(19\d{2}|20\d{2}))?\b')
YEAR = re.compile(r'^(?:19|20)\d{2}$')
USER_AGENT = 'JOJOLibrary-CalendarAudit/2.0 (public Wikipedia metadata; no videos; contact: GitHub JOJOMDS11)'


def extract(html, company, source_url, today=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    soup = BeautifulSoup(html, 'html.parser')
    output = []
    for table in soup.select('table.wikitable'):
        # Never infer an entire year's completeness from a navigation box.
        heading = table.find_previous(['h2','h3','h4','h5'])
        year = None
        while heading:
            cleaned = heading.get_text(' ', strip=True)
            if YEAR.match(cleaned):
                year = int(cleaned[:4])
                break
            heading = heading.find_previous(['h2','h3','h4','h5'])
        if year is not None and (year < 1983 or year > today.year + 1):
            continue
        trs = table.find_all('tr', recursive=True)
        header = None
        for tr in trs[:4]:
            labels = [re.sub(r'\s+',' ',x.get_text(' ',strip=True)).lower() for x in tr.find_all(['th','td'],recursive=False)]
            date_index = next((i for i,x in enumerate(labels) if re.fullmatch(r'(date|air date|event date)', x)), None)
            event_index = next((i for i,x in enumerate(labels) if re.fullmatch(r'(event|show|name)', x)), None)
            if date_index is not None and event_index is not None:
                header = (date_index, event_index, next((i for i,x in enumerate(labels) if x=='type'), None))
                break
        if not header:
            continue
        di, ei, ti = header
        for tr in trs:
            cells = tr.find_all(['td','th'],recursive=False)
            if len(cells) <= max(di, ei):
                continue
            dtext = re.sub(r'\s+',' ',cells[di].get_text(' ', strip=True))
            title = re.sub(r'\s+',' ',cells[ei].get_text(' ', strip=True))
            # Aired dates are often a separate footnote; use the *event* date.
            dtext = re.split(r'\(aired|\[aired', dtext, flags=re.I)[0].strip()
            m = DATE.search(dtext)
            if not m or not title or title.lower() in ('event','name','show','tba'):
                continue
            try:
                y = int(m.group(4)) if m.group(4) else year
                if not y:
                    continue
                date = dt.date(y, MONTHS[m.group(1).lower().rstrip('.')], int(m.group(2)))
                if date > today + dt.timedelta(days=400):
                    continue
            except (ValueError, TypeError):
                continue
            if company == 'ecw' and ti is not None and len(cells) > ti:
                if 'pay-per-view' not in cells[ti].get_text(' ',strip=True).lower():
                    continue
            if company == 'tna' and 'one night only' in title.lower():
                # Weekly PPVs and monthly events are in the page, but not 
                # clip compilations/non-chronological themed collections.
                continue
            if company == 'nxt' and not title.lower().startswith('nxt '):
                title='NXT TakeOver' + (': '+title if title.lower() != 'takeover' else '')
            if company == 'wwe' and title.lower().startswith('nxt '):
                # A separate NXT brand calendar is not derived from WWE rows.
                continue
            # Do not treat disjoint or incomplete tables as officially complete.
            normalized = re.sub(r'[^a-z0-9]+', '-',title.lower()).strip('-')[:65]
            if not normalized or len(title) > 180:
                continue
            output.append({'id':'wiki:%s:%s:%s'%(company,date.isoformat(),normalized),
                           'company':company,'date':date.isoformat(),'year':date.year,
                           'title':title,'show':'PPV','official_url':source_url,
                           'referenceUrl':source_url,'referenceType':'wikipedia',
                           'status':'reference_only','sources':[], 'referenceOnly':True})
    return list({(e['company'],e['date'],e['title'].lower()):e for e in output}.values())


def fetch_page(url, timeout=18):
    req = Request(url, headers={'User-Agent':USER_AGENT, 'Accept':'text/html'})
    with urlopen(req, timeout=timeout) as response:
        return response.read(4_500_001).decode('utf-8', errors='replace')


def count_years(events):
    out={}
    for event in events:
        year=str(event.get('year',''))
        if YEAR.fullmatch(year):out[year]=out.get(year,0)+1
    return dict(sorted(out.items()))


def update(fetch=fetch_page, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        previous = json.loads(DEST.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {'events':[], 'sources':{}}
    prior = previous.get('events',[])
    prior_status = previous.get('sources',{})
    all_events = []
    state = {}
    for company, page in PAGES.items():
        url = 'https://en.wikipedia.org/wiki/' + page
        cached = [x for x in prior if x.get('company')==company]
        try:
            fetched = extract(fetch(url),company,url,now.date())
            if not fetched:
                raise ValueError('Página não apresentou tabela de eventos com datas reconhecíveis')
            state[company] = {'status':'updated','count':len(fetched), 'yearCounts':count_years(fetched), 'url':url, 'checkedAt':now.isoformat(), 'lastSuccessAt':now.isoformat()}
            all_events.extend(fetched)
        except Exception as exc:
            # Explicitly report failure; never silently change this year's zero
            # reference count into a false claim of no missing PPVs.
            last = prior_status.get(company, {})
            state[company] = {'status':'cached' if cached else 'unavailable',
                              'count':len(cached),'yearCounts':count_years(cached),'url':url,'checkedAt':now.isoformat(),
                              'lastSuccessAt':last.get('lastSuccessAt') or last.get('checkedAt') if cached else None,
                              'error':type(exc).__name__}
            all_events.extend(cached)
        if fetch is fetch_page:
            time.sleep(.25)
    output = {'schemaVersion':1, 'checkedAt':now.isoformat().replace('+00:00','Z'),
              'source':'Wikipedia (referências históricas independentes, não calendário oficial nem fontes de vídeo)',
              'sources':state, 'events':sorted(all_events,key=lambda x:(x['date'],x['company'],x['title']))}
    DEST.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(output,ensure_ascii=False,indent=2)+'\n'
    if not DEST.exists() or DEST.read_text(encoding='utf-8') != serialized:
        DEST.write_text(serialized,encoding='utf-8')
    print('Wikipédia:',', '.join('%s %d (%s)'%(c,s['count'],s['status']) for c,s in state.items()))
    return output

if __name__=='__main__':
    update()
