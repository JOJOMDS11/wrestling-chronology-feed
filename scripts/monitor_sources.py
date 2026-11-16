#!/usr/bin/env python3
"""Monitor public OK.ru profile metadata and the health of previously known links.

This tool intentionally does not download videos, bypass login/access controls,
search for copies of removed copyrighted programs, or automatically embed unreviewed
third-party content. Any newly discovered video is a candidate for manual review.

Run: python scripts/monitor_sources.py
No dependencies beyond Python 3.12 stdlib.
"""

import datetime as dt
import html
from html.parser import HTMLParser
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config' / 'monitored_sources.json'
UPDATES = ROOT / 'updates.json'
CANDIDATES = ROOT / 'video_candidates.json'
STATUS = ROOT / 'source_monitor_status.json'
USER_AGENT = 'JOJOLibraryMetadataMonitor/1.0 (+public profile metadata; no auth)' 
VIDEO_URL = re.compile(r'(?:https?://(?:www\.)?ok\.ru)?/video/([0-9]{8,20})(?!\d)', re.I)
REMOVED_MARKERS = (
    'видео удалено', 'видео недоступно', 'video has been deleted',
    'video is unavailable', 'this video is not available',
)
MAX_NEW_PER_PROFILE = 45
MAX_RECHECK_PER_RUN = 14


def utcnow():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def read_json(path, fallback):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        print(f'AVISO: {path.name}: {exc}', file=sys.stderr)
    return fallback


def save_json(path, obj):
    data = json.dumps(obj, ensure_ascii=False, indent=2) + '\n'
    # Do not create spurious GitHub commits on every successful no-change run.
    if not path.exists() or path.read_text(encoding='utf-8') != data:
        path.write_text(data, encoding='utf-8')
        return True
    return False


def request_public(url, timeout=20):
    """Return (HTTP status, body). 403/429 are never treated as removals."""
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('ok.ru', 'www.ok.ru'):
        raise ValueError('Apenas URLs públicas HTTPS de ok.ru são permitidas')
    req = Request(url, headers={
        'User-Agent': USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'pt-BR,pt;q=0.8,ru;q=0.6,en;q=0.4',
    })
    try:
        with urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read(2_000_000).decode('utf-8', 'replace')
    except HTTPError as ex:
        return ex.code, ''


class ProfileLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.active = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.active and tag not in {'img', 'br', 'hr', 'input', 'source', 'meta', 'link', 'wbr'}:
            self.depth += 1
        if tag != 'a':
            return
        href = attrs.get('href') or ''
        matches = VIDEO_URL.search(href)
        if matches:
            self.active = {'videoId': matches.group(1),
                           'title': (attrs.get('title') or attrs.get('aria-label') or '').strip(),
                           'url': 'https://ok.ru/video/' + matches.group(1)}
            self.depth = 0

    def handle_data(self, data):
        if self.active and len(self.active['title']) < 180:
            self.active['title'] += data[:180]

    def handle_endtag(self, tag):
        if self.active:
            if tag == 'a' and self.depth == 0:
                self.active['title'] = ' '.join(self.active['title'].split())[:180]
                self.links.append(self.active)
                self.active = None
            elif self.depth:
                self.depth -= 1


def list_profile_links(html_text):
    """Only explicit <a href=/video/ID> entries; avoid unrelated JS recommendations."""
    p = ProfileLinks()
    p.feed(html_text)
    by_id = {}
    for item in p.links:
        by_id.setdefault(item['videoId'], item)
    return list(by_id.values())[:MAX_NEW_PER_PROFILE]


def profile_state(code, html_text, count):
    if code in (401, 403):
        return 'acesso_restrito'
    if code == 429:
        return 'limite_requisicoes'
    if code in (404, 410):
        return 'pagina_indisponivel'
    if code != 200:
        return 'erro_http'
    if any(m in html_text.lower() for m in ('captcha', 'проверка безопасности')):
        return 'acesso_restrito'
    if count == 0:
        return 'sem_links_visiveis'
    return 'lista_lida'


def check_video(code, page, previously=None):
    """Do not infer 404 from a timeout, 403, or an OK.ru soft HTML page."""
    previously = previously or {}
    misses = int(previously.get('consecutiveMissing') or 0)
    if code in (404, 410):
        misses += 1
        status = 'indisponivel' if misses >= 2 else 'reverificacao_pendente'
    elif code in (401, 403, 429):
        status = 'acesso_restrito'
    elif code == 200:
        # Only explicit removal notices; a generic 200 page is not playback proof.
        lower = html.unescape(page or '').lower()
        if any(marker in lower for marker in REMOVED_MARKERS):
            misses += 1
            status = 'indisponivel' if misses >= 2 else 'reverificacao_pendente'
        else:
            status, misses = 'pagina_respondeu', 0
    else:
        status = 'verificacao_inconclusiva'
    return {'status': status, 'consecutiveMissing': misses}


def normalize(text):
    return re.sub(r'[^\w]+', ' ', (text or '').lower()).strip()


def identify_event(title, events):
    """Loose suggestions only; never auto-approve a video from fuzzy title matches."""
    t = normalize(title)
    if len(t) < 7:
        return []
    keywords = {
        'aew': ('aew', 'dynamite', 'collision', 'full gear', 'даинамит', 'дайнамит', 'коллижн', 'фулл гир'),
        'wwe': ('wwe', 'crown jewel', 'корон', 'роял рамбл', 'саммерслэм', 'кроун джуэл'),
        'njpw': ('njpw', 'dominion', 'домин', 'реслинг кингдом'),
    }
    date_patterns = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        key = ev.get('date', '')
        if len(key) == 10:
            yyyy, mm, dd = key.split('-')
            date_patterns[ev.get('id', '')] = [key, f'{dd}.{mm}.{yyyy}', f'{mm}.{dd}.{yyyy}']
    matches = []
    for ev in events:
        company = ev.get('company', '')
        if company not in keywords:
            continue
        tokens = keywords[company]
        if not any(word in t for word in tokens):
            continue
        if not any(normalize(token) in t for token in date_patterns.get(ev.get('id', ''), [])):
            continue
        matches.append(ev.get('id'))
    return matches[:6]


def recent_okru_catalogue_links(root=ROOT, today=None, months=6):
    """Scan only recent catalogued OK.ru videos; no video bytes are downloaded."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    cutoff = (today - dt.timedelta(days=months * 31)).isoformat()
    result = {}
    for name in ('aew', 'wwe', 'njpw', 'roh', 'tna', 'nxt', 'wcw', 'ecw'):
        data = read_json(root / (name + '.json'), {})
        for ep in data.get('catalogue', {}).get('episodes', []):
            if not isinstance(ep, dict) or ep.get('date', '') < cutoff:
                continue
            for source in ep.get('sources', []):
                if source.get('type') == 'okru':
                    vid = str(source.get('videoId') or '')
                    if re.fullmatch(r'\d{8,20}', vid):
                        result[vid] = 'https://ok.ru/videoembed/' + vid
    return result


def monitor(config, updates, previous_candidates, previous_status, fetcher=request_public, timestamp=None, catalogue_links=None):
    timestamp = timestamp or utcnow()
    events = updates.get('events', [])
    known = {c['videoId']: dict(c) for c in previous_candidates.get('candidates', [])
             if isinstance(c, dict) and c.get('videoId')}
    for example in config.get('video_examples_for_review', []):
        vid = str(example.get('id', ''))
        if not re.fullmatch(r'\d{8,20}', vid):
            continue
        known.setdefault(vid, {
            'videoId': vid, 'url': 'https://ok.ru/video/' + vid,
            'title': '', 'origin': 'exemplo_fornecido',
            'note': example.get('note', ''),
            'review': 'pendente', 'possibleEventIds': [],
        })
    profiles = []
    for profile in config.get('profiles', []):
        if not profile.get('enabled'):
            continue
        url = profile.get('url', '')
        platform = str(profile.get('platform') or 'okru')
        if platform == 'archive' and (url.startswith('https://archive.org/search?') or re.fullmatch(r'https://archive\.org/details/[A-Za-z0-9_.-]{3,120}/?', url)):
            profiles.append({'id': profile.get('id'), 'label': profile.get('label'), 'url': url,
                             'state': 'coleta_archive_separada', 'found': None})
            continue
        if platform == 'bilibili' and re.fullmatch(r'https://www\.bilibili\.tv/en/space/\d+/?', url):
            profiles.append({'id': profile.get('id'), 'label': profile.get('label'), 'url': url,
                             'state': 'coleta_oracle_pendente', 'found': None})
            continue
        if not re.fullmatch(r'https://ok\.ru/(?:profile|group)/\d+/video/?', url):
            profiles.append({'id': profile.get('id'), 'label': profile.get('label'), 'url': url,
                             'state': 'url_invalida', 'found': 0})
            continue
        try:
            code, page = fetcher(url)
            links = list_profile_links(page) if code == 200 else []
            state = profile_state(code, page, len(links))
            for link in links:
                vid = link['videoId']
                if vid not in known:
                    known[vid] = {
                        **link, 'origin': profile.get('id'), 'review': 'pendente',
                        'possibleEventIds': identify_event(link['title'], events),
                    }
                elif not known[vid].get('title') and link['title']:
                    known[vid]['title'] = link['title']
                    known[vid]['possibleEventIds'] = identify_event(link['title'], events)
            profiles.append({'id': profile.get('id'), 'label': profile.get('label'),
                             'url': url, 'state': state, 'found': len(links)})
        except (OSError, URLError, ValueError, TimeoutError) as exc:
            profiles.append({'id': profile.get('id'), 'label': profile.get('label'),
                             'url': url, 'state': 'consulta_falhou', 'found': 0})
            print(f'AVISO: perfil {url}: {exc}', file=sys.stderr)

    health_prev = previous_status.get('links', {})
    health = dict(health_prev)
    # Health-check only registered links from updates.json + explicit sample IDs,
    # not all 16k historical episodes (avoids hammering third-party providers).
    tracked = {}
    for ev in events:
        for source in ev.get('sources', []):
            if isinstance(source, dict) and source.get('type') == 'okru':
                vid = str(source.get('videoId') or '')
                if re.fullmatch(r'\d{8,20}', vid):
                    tracked[vid] = 'https://ok.ru/videoembed/' + vid
    for example in config.get('video_examples_for_review', []):
        vid = str(example.get('id') or '')
        if re.fullmatch(r'\d{8,20}', vid):
            tracked.setdefault(vid, 'https://ok.ru/videoembed/' + vid)
    # Rotate through existing catalogues: check oldest verifications first.
    # Never hammer 16k historical links every six hours.
    tracked.update(catalogue_links or {})
    def due_key(item):
        checked = health_prev.get(item[0], {}).get('checkedAt', '')
        return (checked, item[0])
    for vid, url in sorted(tracked.items(), key=due_key)[:MAX_RECHECK_PER_RUN]:
        prev = health_prev.get(vid, {})
        try:
            code, page = fetcher(url)
            result = check_video(code, page, prev)
            result['httpStatus'] = code
        except (OSError, URLError, ValueError, TimeoutError):
            result = check_video(0, '', prev)
            result['httpStatus'] = None
        result['checkedAt'] = timestamp
        health[vid] = result

    report = {'schemaVersion': 1, 'checkedAt': timestamp,
              'profiles': profiles,
              'links': health,
              'counts': {
                  'profilesConfigured': len(profiles),
                  'profilesReadable': sum(p['state'] == 'lista_lida' for p in profiles),
                  'candidatesPendingReview': sum(c.get('review') == 'pendente' for c in known.values()),
                  'linksUnavailable': sum(v.get('status') == 'indisponivel' for v in health.values()),
              },
              'note': 'Acesso público pode ser bloqueado. Links não são substituídos automaticamente; candidatos exigem revisão de direitos e correspondência.'}
    pending = {'schemaVersion': 1, 'candidates': sorted(known.values(), key=lambda c: c['videoId'])}
    return report, pending


def run():
    config = read_json(CONFIG, {'profiles': [], 'video_examples_for_review': []})
    report, candidates = monitor(
        config,
        read_json(UPDATES, {}),
        read_json(CANDIDATES, {'candidates': []}),
        read_json(STATUS, {'links': {}}),
        catalogue_links=recent_okru_catalogue_links(),
    )
    save_json(CANDIDATES, candidates)
    # Keep an accurate six-hour last-checked timestamp in the public status.
    save_json(STATUS, report)
    print('Perfis:', ', '.join(f"{p['id']}={p['state']}({p['found']})" for p in report['profiles']))
    print('Candidatos para revisão:', report['counts']['candidatesPendingReview'])
    print('Links indisponíveis:', report['counts']['linksUnavailable'])


if __name__ == '__main__':
    run()
