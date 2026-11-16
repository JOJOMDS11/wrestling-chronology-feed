#!/usr/bin/env python3
"""Discover Internet Archive *metadata* candidates; never approve or publish links.

Bounded official API searches; no videos are downloaded. Items may be copyrighted,
unavailable, unrelated, or have playback restrictions. Human review is mandatory.
"""
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'archive_candidates.json'
SEARCH = 'https://archive.org/advancedsearch.php'
META = 'https://archive.org/metadata/'
TOPICS = (
    ('wwe', 'RAW', 'WWE RAW'),
    ('wwe', 'SmackDown', 'WWE SmackDown'),
    ('wwe', 'NXT', 'WWE NXT'),
    ('roh', 'HonorClub', 'ROH HonorClub'),
    ('aew', 'Dynamite', 'AEW Dynamite'),
    ('aew', 'Collision', 'AEW Collision'),
    ('tna', 'Impact', 'TNA Impact'),
)
VIDEO = re.compile(r'\.(?:mp4|m4v|webm|ogv)$', re.I)
DATE = re.compile(r'(?<!\d)(20\d\d)[.\-_ ](0?[1-9]|1[0-2])[.\-_ ]([0-2]?\d|3[01])(?!\d)')


def get_json(url, timeout=9):
    req = Request(url, headers={'User-Agent': 'JOJOLibrary-AutoMatchMetadata/1.0 (public metadata only)'})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(1_500_001).decode('utf-8'))


def episode_date(text):
    m = DATE.search(str(text))
    if not m:
        return ''
    try:
        return dt.date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        return ''


def archive_url(identifier, filename):
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{3,120}', identifier) or '..' in identifier:
        return ''
    if not isinstance(filename, str) or len(filename) > 240 or not VIDEO.search(filename):
        return ''
    if any(part in ('', '.', '..') for part in filename.split('/')):
        return ''
    return 'https://archive.org/download/' + quote(identifier, safe='') + '/' + quote(filename, safe='/')


def candidate_id(url):
    return 'a' + hashlib.sha256(url.encode('utf-8')).hexdigest()[:32]


def targets(now=None):
    now = now or dt.datetime.now(dt.timezone.utc).date()
    earliest = now - dt.timedelta(days=75)
    ids = {}
    for company in ('wwe', 'nxt', 'roh', 'aew', 'tna'):
        path = ROOT / (company + '.json')
        try:
            for e in json.loads(path.read_text(encoding='utf-8')).get('catalogue', {}).get('episodes', []):
                if isinstance(e, dict) and earliest.isoformat() <= str(e.get('date', '')) <= now.isoformat() and not e.get('sources'):
                    ids.setdefault((company, str(e['date'])), []).append(e)
        except (OSError, ValueError, KeyError):
            pass
    try:
        for e in json.loads((ROOT/'updates.json').read_text(encoding='utf-8')).get('events', []):
            if isinstance(e, dict) and earliest.isoformat() <= str(e.get('date', '')) <= now.isoformat() and not e.get('sources'):
                ids.setdefault((str(e.get('company')), str(e['date'])), []).append(e)
    except (OSError, ValueError, KeyError):
        pass
    return ids


def likely_match(text, company, show):
    t = str(text).lower()
    if re.search(r'\b(?:trailer|teaser|highlights?|recap|reaction|promo|review|podcast|clip|shorts?)\b', t):
        return False
    patterns = {'raw': r'\braw\b', 'smackdown': r'smack[ ._-]?down', 'nxt': r'\bnxt\b',
                'honorclub': r'\broh\b|honor.?club|ring of honor', 'dynamite': r'dynamite',
                'collision': r'collision', 'impact': r'tna|impact wrestling'}
    if not re.search(patterns.get(show.lower(), r'\bNO_MATCH\b'), t):
        return False
    # Cross-company title overlap should not imply a match.
    if company == 'roh' and re.search(r'\bwwe\b|\baew\b', t) and not re.search(r'\broh\b|ring.of.honor',t):
        return False
    return True


def discover(fetch=get_json, now=None):
    now = now or dt.datetime.now(dt.timezone.utc).date()
    tgt = targets(now)
    result = {}
    successful = 0
    for company, show, label in TOPICS:
        query = 'mediatype:movies AND title:("' + label + '")'
        args = urlencode({'q': query, 'output': 'json', 'rows': 40, 'page': 1,
                          'fl[]': ['identifier','title','date','publicdate'],
                          'sort[]': 'publicdate desc'}, doseq=True)
        try:
            docs = fetch(SEARCH + '?' + args).get('response', {}).get('docs', [])
            successful += 1
        except (OSError, ValueError, KeyError, TimeoutError) as ex:
            print('Archive indisponível para', label, type(ex).__name__)
            continue
        requests = 0
        for doc in docs:
            if requests >= 4:
                break
            identifier = str(doc.get('identifier') or '')
            title = str(doc.get('title') or '')
            if not re.fullmatch(r'[a-zA-Z0-9_.-]{3,120}', identifier):
                continue
            # At most five metadata calls per topic; protect quota and weak VPS.
            try:
                info = fetch(META + quote(identifier, safe=''))
            except (OSError, ValueError, KeyError, TimeoutError):
                continue
            requests += 1
            if str(info.get('is_dark') or '') in ('True','1') or not info.get('metadata'):
                continue
            for file in info.get('files', [])[:250]:
                filename = file.get('name') or ''
                url = archive_url(identifier, filename)
                if not url:
                    continue
                combined = title+' '+filename
                date = episode_date(combined)
                if not date or not likely_match(combined, company, show):
                    continue
                try:
                    if file.get('length') is not None and float(file['length']) < 25*60:
                        continue
                except (ValueError, TypeError, OverflowError):
                    pass
                companies = ('wwe','nxt') if show == 'NXT' else (company,)
                matches = []
                for cat in companies:
                    for e in tgt.get((cat,date), []):
                        names = (str(e.get('show',''))+' '+str(e.get('title',''))).lower()
                        if likely_match(names, cat if cat!='nxt' else 'wwe', show) and e.get('id'):
                            matches.append(str(e['id']))
                if not matches:
                    continue
                uid = candidate_id(url)
                length = file.get('length')
                duration = ''
                try:
                    seconds = int(float(length))
                    if 0 < seconds < 24*3600:
                        duration = '{}:{:02d}:{:02d}'.format(seconds//3600, seconds%3600//60, seconds%60)
                except (ValueError, TypeError, OverflowError):
                    pass
                result[uid] = {'videoId': uid, 'platform': 'archive', 'url': url,
                               'duration': duration,
                               'detail': 'https://archive.org/details/'+quote(identifier,safe=''),
                               'title': (title or filename)[:220], 'filename': filename[:240],
                               'publishedAt': str(doc.get('publicdate') or doc.get('date') or '')[:40],
                               'thumbnailUrl': 'https://archive.org/services/img/'+quote(identifier,safe=''),
                               'possibleEventIds': list(dict.fromkeys(matches))[:8],
                               'confidence': 'candidatura por título e data; reprodução e direitos não verificados',
                               'review': 'pendente', 'sourceProfiles': ['Internet Archive']}
    return successful, list(result.values())


def recent_ppvs_without_video(now=None):
    """Recent, already documented specials with no approved source in any catalogue.

    These are metadata SEARCH TARGETS, never confirmed recordings. Keep the daily
    work bounded, avoid old unlicensed catalogues and exclude future shows.
    """
    now = now or dt.datetime.now(dt.timezone.utc).date()
    earliest = now - dt.timedelta(days=90)
    catalogues = {}
    for company in ('wwe', 'aew', 'tna', 'nxt', 'roh', 'njpw'):
        try:
            episodes=json.loads((ROOT/(company+'.json')).read_text(encoding='utf-8'))['catalogue']['episodes']
            catalogues[company]=episodes
        except (OSError, ValueError, KeyError):
            catalogues[company]=[]
    updates=json.loads((ROOT/'updates.json').read_text(encoding='utf-8')).get('events',[])
    result=[]
    seen=set()
    for e in updates:
        if e.get('show','').lower() not in ('ppv','ple','premium live event') or e.get('sources'):
            continue
        date=e.get('date','')
        try:
            day=dt.date.fromisoformat(date)
        except (ValueError,TypeError):
            continue
        if not earliest <= day <= now:
            continue
        company=e.get('company')
        if company not in catalogues or not e.get('id') or not e.get('title'):
            continue
        if any(x.get('date')==date and x.get('show','').lower() in ('ppv','ple') and x.get('sources')
               for x in catalogues[company]):
            continue
        key=(company,date,e['title'].lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(e)
    result.sort(key=lambda x:x.get('date',''),reverse=True)
    return result[:6]


def discover_missing_ppvs(fetch=get_json, now=None):
    """Metadata-only PPV search; date/year match is a suggestion, not proof."""
    now=now or dt.datetime.now(dt.timezone.utc).date()
    candidates={}
    done=0
    for event in recent_ppvs_without_video(now):
        title=re.sub(r'\s+',' ',str(event['title'])).strip()
        # At most six short searches and two metadata calls per PPV per day.
        query='mediatype:movies AND title:("'+title.replace('"','')[:90]+'")'
        args=urlencode({'q':query,'output':'json','rows':12,'page':1,
                        'fl[]':['identifier','title','publicdate'], 'sort[]':'publicdate desc'},doseq=True)
        try:
            docs=fetch(SEARCH+'?'+args).get('response',{}).get('docs',[])
            done+=1
        except (OSError, ValueError, KeyError, TimeoutError):
            continue
        calls=0
        needle=set(re.findall(r'[a-z0-9]{4,}',title.lower()))-{'2026','2025','event'}
        for doc in docs:
            if calls>=2:break
            identifier=str(doc.get('identifier') or '')
            doc_title=str(doc.get('title') or '')
            if not re.fullmatch(r'[a-zA-Z0-9_.-]{3,120}',identifier):continue
            if needle and len(needle & set(re.findall(r'[a-z0-9]{4,}',doc_title.lower()))) < min(2,len(needle)):
                continue
            try:info=fetch(META+quote(identifier,safe=''))
            except (OSError, ValueError, KeyError, TimeoutError):continue
            calls+=1
            if str(info.get('is_dark') or '') in ('True','1') or not info.get('metadata'):continue
            for item in (info.get('files') or [])[:100]:
                filename=str(item.get('name') or '')
                url=archive_url(identifier,filename)
                if not url:continue
                combined=(doc_title+' '+filename).lower()
                if any(x in combined for x in ('trailer','teaser','highlights','countdown','buy in','press conference')):
                    continue
                date=episode_date(combined)
                # Exact date OR name+year is enough for review, NOT approval.
                if date and date!=event['date']:continue
                if not date and event['date'][:4] not in combined:continue
                if needle and len(needle & set(re.findall(r'[a-z0-9]{4,}',combined))) < min(2,len(needle)):
                    continue
                try:
                    if item.get('length') is not None and float(item['length']) < 25*60:continue
                except (ValueError,TypeError,OverflowError):pass
                uid=candidate_id(url)
                candidates[uid]={'videoId':uid,'platform':'archive','url':url,
                                 'detail':'https://archive.org/details/'+quote(identifier,safe=''),
                                 'title':(doc_title or filename)[:220], 'filename':filename[:240],
                                 'thumbnailUrl':'https://archive.org/services/img/'+quote(identifier,safe=''),
                                 'possibleEventIds':[str(event['id'])],
                                 'confidence':'Metadados: nome e '+('data' if date else 'ano')+' semelhantes; reprodução e direitos NÃO verificados',
                                 'review':'pendente','sourceProfiles':['Internet Archive - PPV pendente']}
    return done,list(candidates.values())


def main():
    existing = []
    if DEST.exists():
        try:
            existing = json.loads(DEST.read_text(encoding='utf-8')).get('candidates', [])
        except (OSError, ValueError):
            pass
    success, rows = discover()
    ppv_queries, ppv_rows = discover_missing_ppvs()
    rows += ppv_rows
    if not success and not ppv_queries:
        print('Nenhuma consulta respondida; preservando os candidatos já cadastrados.')
        return
    merged = {x['videoId']: x for x in existing if isinstance(x, dict) and x.get('videoId')}
    merged.update({x['videoId']: x for x in rows})
    output={'schemaVersion': 1, 'source': 'Internet Archive (metadados sem aprovação automática)',
            'candidates': list(merged.values())[-500:]}
    if not existing and not output['candidates'] and DEST.exists():
        return
    blob=json.dumps(output, ensure_ascii=False, indent=2)+'\n'
    if not DEST.exists() or DEST.read_text(encoding='utf-8')!=blob:
        DEST.write_text(blob,encoding='utf-8')
    print('Internet Archive:',success,'consultas semanais;',ppv_queries,'consultas de PPV;',len(rows),'candidatos para revisão')

if __name__ == '__main__':
    main()
