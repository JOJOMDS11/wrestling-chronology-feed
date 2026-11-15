#!/usr/bin/env python3
"""JOJO Library | OK.ru metadata monitor for a separate Oracle VPS.

Authenticated Playwright browser; reads public/authorized profile metadata only.
Never downloads streams, bypasses authentication gates or auto-publishes embeds.
A 200 HTML response does not prove a video is playable.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import re
import sys
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('JOJO_DATA_DIR', str(BASE / 'data')))
AUTH_FILE = Path(os.environ.get('OKRU_AUTH_STATE', str(DATA_DIR / 'okru_state.json')))
REPO = os.environ.get('GITHUB_REPO', 'JOJOMDS11/wrestling-chronology-feed')
BRANCH = os.environ.get('GITHUB_BRANCH', 'main')
TOKEN = os.environ.get('GITHUB_TOKEN', '')
USER_AGENT = 'JOJOLibraryMonitor/2.0 (metadata only)'
HTTP_TIMEOUT = 25
MAX_LINKS_PER_PROFILE = 65
MAX_LINK_HEALTH = 16
MAX_CANDIDATES = 2000
GENERIC_TITLES = {'смотреть', 'watch', 'video', 'видео', 'play', 'assistir', 'видеозаписи', 'videos'}
REMOVED_MARKERS = ('видео удалено', 'видео недоступно', 'video has been deleted',
                   'video is unavailable', 'this video is not available')
ALIASES = {
    'dynamite': ('dynamite', 'дайнамит', 'динамайт', 'динамит', 'динаміт'),
    'collision': ('collision', 'коллижн', 'колизион'),
    'full gear': ('full gear', 'фулл гир', 'фулл гиар'),
    'crown jewel': ('crown jewel', 'кроун джуел', 'краун джуэл', 'кроун джуэл'),
    'raw': ('monday night raw', 'wwe raw', 'ро', 'raw'),
    'smackdown': ('smackdown', 'смэкдаун', 'смэкдаун'),
}
VIDEO_RE = re.compile(r'(?:https?://(?:www\.)?ok\.ru)?/video/(\d{8,20})(?!\d)', re.I)
PROFILE_RE = re.compile(r'^https://(?:www\.)?ok\.ru/profile/\d+/video/?$')


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def note(message):
    print(message, flush=True)


def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def normalized(value):
    value = unicodedata.normalize('NFKD', str(value or '')).casefold()
    return re.sub(r'\s+', ' ', ''.join(c for c in value if not unicodedata.combining(c))).strip()


def clean_title(value):
    value = re.sub(r'\s+', ' ', html.unescape(str(value or ''))).strip()[:220]
    return '' if normalized(value) in GENERIC_TITLES else value


def date_variants(iso):
    if not re.fullmatch(r'\d{4}-\d\d-\d\d', str(iso or '')):
        return set()
    yyyy, mm, dd = iso.split('-')
    return {iso, f'{dd}.{mm}.{yyyy}', f'{dd}/{mm}/{yyyy}', f'{dd}-{mm}-{yyyy}',
            f'{mm}.{dd}.{yyyy}', f'{dd}.{mm}.{yyyy[2:]}', f'{dd}/{mm}/{yyyy[2:]}'}


def suggest_events(title, events):
    """Advisory IDs only; never auto-associate or embed third-party videos."""
    t = normalized(title)
    if len(t) < 7:
        return []
    suggestions = []
    for ev in events:
        if not isinstance(ev, dict) or not ev.get('id'):
            continue
        company = normalized(ev.get('company', ''))
        ev_title = normalized(ev.get('title', ''))
        aliases = ALIASES.get(ev_title, (ev_title,))
        has_name = any(a and a in t for a in aliases)
        has_company = company in t or (company == 'aew' and 'all elite' in t)
        # Name without a date can match many episodes; never call that a match.
        has_date = any(x in t for x in date_variants(ev.get('date', '')))
        if has_name and has_date and (has_company or ev_title in ('full gear', 'crown jewel')):
            suggestions.append(ev['id'])
    return suggestions[:8]


def evaluate_video_cards(page):
    """Collect metadata from already-loaded OK.ru video cards; never play videos.

    Accept canonical /video/<id> links and explicit data-video-id attributes.
    DOM structures vary across profiles, so this is best-effort, not proof of
    complete coverage.
    """
    return page.evaluate(r'''() => {
      const result = [];
      const nodes = document.querySelectorAll('a[href*="/video/"], [data-video-id]');
      const generic = new Set(['смотреть','watch','video','видео','play','assistir','видеозаписи','videos']);
      const clean = x => (x || '').replace(/\s+/g, ' ').trim();
      for (const el of nodes) {
        if (result.length >= 700) break;
        let id = '';
        if (el.tagName === 'A') {
          try {
            const link = new URL(el.getAttribute('href') || '', location.href);
            if (['ok.ru','www.ok.ru'].includes(link.hostname)) {
              const match = link.pathname.match(/^\/video\/(\d{8,20})(?:\/|$)/);
              if (match) id = match[1];
            }
          } catch (_) {}
        }
        if (!id) {
          const raw = el.getAttribute('data-video-id') || '';
          if (/^\d{8,20}$/.test(raw)) id = raw;
        }
        if (!id) continue;
        const card = el.closest('[data-video-id], [data-video], .video-card, .vid-card, .video-item, .video-card__wrapper') || el.parentElement?.parentElement || el;
        const heading = card.querySelector('h2,h3,h4,[class*="title"],[class*="name"]');
        const choices = [el.getAttribute('title'), el.getAttribute('aria-label'),
          heading?.getAttribute('title'), heading?.textContent,
          card.getAttribute('title'), el.textContent];
        const title = choices.map(clean).find(x => x && !generic.has(x.toLowerCase())) || '';
        result.push({videoId:id, url:`https://ok.ru/video/${id}`,
                     title:title.slice(0,220), context:clean(card.textContent).slice(0,320)});
      }
      return result;
    }''')


def scan_profile_video_cards(page, scroll_steps=10):
    """Accumulate IDs *before and after* scrolling; lazy cards may be removed.

    Never click, play or download video, and keep the original 65-ID limit.
    """
    all_rows = []
    for step in range(scroll_steps + 1):
        all_rows.extend(evaluate_video_cards(page))
        if len(unique_rows(all_rows)) >= MAX_LINKS_PER_PROFILE:
            break
        if step == scroll_steps:
            break
        page.evaluate(r'''() => {
          const distance = Math.max(600, Math.floor(window.innerHeight * 0.85));
          window.scrollBy(0, distance);
          // Some OK.ru views use a scrollable list instead of window scrolling.
          const candidates = [...document.querySelectorAll('main, section, [class*="scroll"], [class*="list"], [class*="feed"]')]
            .filter(el => el.clientHeight > 240 && el.scrollHeight - el.clientHeight > 180)
            .sort((a,b) => (b.scrollHeight-b.clientHeight) - (a.scrollHeight-a.clientHeight));
          for (const el of candidates.slice(0, 2)) el.scrollTop += distance;
        }''')
        page.wait_for_timeout(850)
    return unique_rows(all_rows)


def unique_rows(rows):
    result = {}
    for entry in rows:
        vid = str(entry.get('videoId', ''))
        if not re.fullmatch(r'\d{8,20}', vid):
            continue
        title = clean_title(entry.get('title'))
        context = str(entry.get('context') or '')[:320]
        if vid not in result:
            result[vid] = {'videoId': vid, 'url': f'https://ok.ru/video/{vid}',
                           'title': title, 'context': context}
        elif title and not result[vid]['title']:
            result[vid].update(title=title, context=context)
    return list(result.values())[:MAX_LINKS_PER_PROFILE]


def health_update(previous, http_code, body, checked_at):
    """Only two confirmed 404/410/explicit removal observations mean removed."""
    missing = int(previous.get('consecutiveMissing', 0) or 0)
    if http_code in (404, 410):
        missing += 1
        status = 'indisponivel' if missing >= 2 else 'reverificacao_pendente'
    elif http_code in (401, 403, 429):
        status = 'acesso_restrito'
    elif http_code == 200:
        plain = html.unescape(body or '').lower()
        if any(marker in plain for marker in REMOVED_MARKERS):
            missing += 1
            status = 'indisponivel' if missing >= 2 else 'reverificacao_pendente'
        else:
            missing = 0
            status = 'pagina_respondeu'
    else:
        status = 'verificacao_inconclusiva'
    return {'status': status, 'consecutiveMissing': missing,
            'httpStatus': http_code or None, 'checkedAt': checked_at}


class GitHub:
    def __init__(self, repo, branch, token):
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo) or not re.fullmatch(r'[\w.\-/]+', branch):
            raise ValueError('GITHUB_REPO ou GITHUB_BRANCH inválido')
        self.repo, self.branch, self.token = repo, branch, token
        self.base = f'https://api.github.com/repos/{repo}/contents/'

    def request(self, path, data=None, method='GET'):
        url = self.base + quote(path, safe='/') + '?ref=' + quote(self.branch, safe='') if method == 'GET' else self.base + quote(path, safe='/')
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': USER_AGENT,
                   'X-GitHub-Api-Version': '2022-11-28'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = Request(url, data=json.dumps(data).encode() if data is not None else None,
                      headers=headers, method=method)
        try:
            with urlopen(req, timeout=HTTP_TIMEOUT) as response:
                return json.load(response)
        except HTTPError as exc:
            # Never log request headers or token.
            raise RuntimeError(f'GitHub {path}: HTTP {exc.code} ({method})') from None
        except URLError as exc:
            raise RuntimeError(f'GitHub {path}: erro de conexão ({type(exc.reason).__name__})') from None

    def get(self, path, default=None):
        try:
            doc = self.request(path)
            raw = base64.b64decode(doc['content'].replace('\n', ''))
            return json.loads(raw.decode('utf-8')), doc['sha']
        except RuntimeError as exc:
            if default is not None and 'HTTP 404' in str(exc):
                return default, None
            raise

    def put(self, path, obj, old_sha):
        if not self.token:
            raise RuntimeError('GITHUB_TOKEN ausente: só será gravado no disco local')
        raw = json.dumps(obj, ensure_ascii=False, indent=2) + '\n'
        # A timestamp-only run would create a commit each time by design; keep an audit trail.
        payload = {'message': 'Monitor Oracle: atualizar fontes e candidatos OK.ru',
                   'content': base64.b64encode(raw.encode('utf-8')).decode('ascii'),
                   'branch': self.branch}
        if old_sha:
            payload['sha'] = old_sha
        return self.request(path, data=payload, method='PUT')


def catalogue_ids(episodes):
    ids = set()
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        for src in ep.get('sources', []):
            if isinstance(src, dict) and src.get('type') == 'okru':
                video_id = str(src.get('videoId') or '')
                if re.fullmatch(r'\d{8,20}', video_id):
                    ids.add(video_id)
    return ids


def targets(config, updates, previous_status, candidates, gh=None, include_catalogues=True):
    values = {str(k) for k in (previous_status.get('links') or {}) if re.fullmatch(r'\d{8,20}', str(k))}
    for example in config.get('video_examples_for_review', []):
        vid = str(example.get('id', ''))
        if re.fullmatch(r'\d{8,20}', vid):
            values.add(vid)
    for ev in updates.get('events', []):
        values |= catalogue_ids([ev])
    for item in candidates.get('candidates', []):
        if item.get('review') == 'aprovado':
            vid = str(item.get('videoId') or '')
            if re.fullmatch(r'\d{8,20}', vid):
                values.add(vid)
    if include_catalogues and gh:
        for catalog in ('aew','wwe','njpw','roh','tna','nxt','wcw','ecw'):
            try:
                doc, _ = gh.get(f'{catalog}.json', {})
                # Only recently added catalogue videos are checked; old entries stay untouched.
                all_episodes = doc.get('catalogue', {}).get('episodes', [])
                cutoff = (datetime.now(timezone.utc) - timedelta(days=186)).date().isoformat()
                recent = [ep for ep in all_episodes if isinstance(ep, dict) and str(ep.get('date', '')) >= cutoff]
                values.update(catalogue_ids(recent))
            except Exception as exc:
                note(f'AVISO: catálogo {catalog}: {type(exc).__name__}; seguindo com demais links')
    return sorted(values, key=lambda vid: ((previous_status.get('links') or {}).get(vid, {}).get('checkedAt', ''), vid))


def browser_probe(page, url, need_cards):
    try:
        response = page.goto(url, wait_until='domcontentloaded', timeout=25_000)
        code = response.status if response else 0
        page.wait_for_timeout(1200)
        rows = scan_profile_video_cards(page) if need_cards and code == 200 else []
        current = page.url.lower()
        html_body = page.content()[:600_000]
        visible = page.locator('body').inner_text(timeout=6000).lower()[:10000]
        if any(word in current for word in ('st.cmd=login', '/login')) or any(word in visible for word in ('captcha', 'проверка безопасности', 'подтвердите, что', 'access denied')):
            return 403, html_body, [], 'sessao_ou_acesso_restrito'
        if need_cards and code == 200 and len(rows) <= 2:
            note(f'AVISO: somente {len(rows)} IDs de vídeo visíveis em {url}; a coleta pode estar incompleta')
        return code, html_body if need_cards else visible, rows, None
    except Exception as exc:
        note(f'AVISO: consulta inconclusiva ({type(exc).__name__})')
        return 0, '', [], 'consulta_falhou'


def build_report(config, updates, old_candidates, old_status, probe, gh=None, include_catalogues=True, checked_at=None):
    checked_at = checked_at or now_iso()
    events = updates.get('events', [])
    known = {str(i['videoId']): dict(i) for i in old_candidates.get('candidates', [])
             if isinstance(i, dict) and re.fullmatch(r'\d{8,20}', str(i.get('videoId', '')))}
    for example in config.get('video_examples_for_review', []):
        vid = str(example.get('id', ''))
        if re.fullmatch(r'\d{8,20}', vid):
            known.setdefault(vid, {'videoId': vid, 'url': f'https://ok.ru/video/{vid}',
                                   'origin': 'exemplo_fornecido', 'title': '',
                                   'note': example.get('note', ''), 'review': 'pendente',
                                   'possibleEventIds': []})
    profiles = []
    new_count = 0
    for item in config.get('profiles', []):
        if not item.get('enabled'):
            continue
        url = item.get('url', '')
        if not PROFILE_RE.fullmatch(url):
            profiles.append({'id': item.get('id'), 'label': item.get('label'), 'url': url,
                             'state': 'url_invalida', 'found': 0})
            continue
        code, page_text, entries, error = probe(url, True)
        if error:
            state = error
        elif code in (401,403):
            state = 'acesso_restrito'
        elif code == 429:
            state = 'limite_requisicoes'
        elif code == 200 and not entries:
            state = 'sem_links_visiveis'
        elif code == 200:
            state = 'lista_parcial' if len(entries) <= 2 else 'lista_lida'
        else:
            state = 'consulta_falhou'
        # Ignore misleading links from access-denied/login pages.
        if state not in ('lista_lida', 'lista_parcial'):
            entries = []
        for entry in entries:
            vid = entry['videoId']
            title = entry.get('title', '')
            if vid not in known:
                if len(known) >= MAX_CANDIDATES:
                    continue
                new_count += 1
                known[vid] = {'videoId': vid, 'url': entry['url'], 'title': title,
                              'origin': item.get('id', 'okru'), 'review': 'pendente',
                              'possibleEventIds': suggest_events(title, events)}
            elif title and not clean_title(known[vid].get('title')):
                known[vid]['title'] = title
                known[vid]['possibleEventIds'] = suggest_events(title, events)
        profiles.append({'id': item.get('id'), 'label': item.get('label'),
                         'url': url, 'state': state, 'found': len(entries)})
        note(f'Perfil {item.get("id")}: {state} ({len(entries)} links)')

    health = dict(old_status.get('links', {}))
    due = targets(config, updates, old_status, old_candidates, gh, include_catalogues)
    for vid in due[:MAX_LINK_HEALTH]:
        code, body, _, _ = probe(f'https://ok.ru/videoembed/{vid}', False)
        health[vid] = health_update(health.get(vid, {}), code, body, checked_at)
    report = {'schemaVersion': 1, 'checkedAt': checked_at, 'profiles': profiles,
              'links': health,
              'counts': {'profilesConfigured': len(profiles),
                         'profilesReadable': sum(p['state'] in ('lista_lida', 'lista_parcial') for p in profiles),
                         'candidatesPendingReview': sum(x.get('review') == 'pendente' for x in known.values()),
                         'linksUnavailable': sum(x.get('status') == 'indisponivel' for x in health.values()),
                         'newCandidates': new_count},
              'note': 'Monitor Oracle autenticado. Detecção de vídeos e disponibilidade são indicativas; fonte exige revisão e autorização.'}
    candidates = {'schemaVersion': 1, 'candidates': sorted(known.values(), key=lambda x: x['videoId'])}
    return report, candidates


def main():
    parser = argparse.ArgumentParser(description='Monitor OK.ru JOJO Library (Oracle)')
    parser.add_argument('--once', action='store_true', help='Executar uma consulta; agendamento é via systemd timer')
    parser.add_argument('--no-catalogues', action='store_true', help='Não consultar JSONs de catálogo para reverificação')
    parser.add_argument('--dry-run', action='store_true', help='Gerar só os arquivos locais, sem enviar ao GitHub')
    args = parser.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    gh = GitHub(REPO, BRANCH, TOKEN)
    required = ['config/monitored_sources.json', 'updates.json']
    data = {}
    shas = {}
    for path in required + ['video_candidates.json', 'source_monitor_status.json']:
        default = None if path in required else ({} if path.endswith('status.json') else {'candidates': []})
        data[path], shas[path] = gh.get(path, default)
    if not AUTH_FILE.exists():
        note(f'ERRO: sessão OK.ru não encontrada: {AUTH_FILE}. Consulte GUIA.md')
        return 2
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        note('ERRO: instale dependências: python -m pip install -r requirements.txt')
        return 2
    note(f'Consultando {len(data[required[0]].get("profiles", []))} perfis. Publicar: {bool(TOKEN) and not args.dry_run}')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--disable-dev-shm-usage'])
        context = browser.new_context(storage_state=str(AUTH_FILE), locale='ru-RU',
                                      viewport={'width': 1280, 'height': 900})
        page = context.new_page()
        def probe(url, need_cards):
            return browser_probe(page, url, need_cards)
        report, pending = build_report(
            data[required[0]], data[required[1]], data['video_candidates.json'],
            data['source_monitor_status.json'], probe, gh,
            include_catalogues=not args.no_catalogues)
        # Refresh session tokens only after a usable response, not a login/captcha page.
        if report['counts']['profilesReadable'] and context.cookies('https://ok.ru/'):
            context.storage_state(path=str(AUTH_FILE), indexed_db=True)
            try:
                AUTH_FILE.chmod(0o600)
            except OSError:
                pass
        context.close()
        browser.close()
    atomic_json(DATA_DIR / 'video_candidates.json', pending)
    atomic_json(DATA_DIR / 'source_monitor_status.json', report)
    if TOKEN and not args.dry_run:
        # Read SHA just before writing, protect against concurrent edits/409 conflict.
        for path, new_obj in (('video_candidates.json', pending), ('source_monitor_status.json', report)):
            current, sha = gh.get(path, {'candidates': []} if path.startswith('video_') else {})
            # Candidate merge preserves manual review updates that arrived mid-run.
            if path.startswith('video_'):
                revised = {c['videoId']: c for c in current.get('candidates', []) if c.get('videoId')}
                for c in new_obj['candidates']:
                    old = revised.get(c['videoId'])
                    if old and old.get('review') != c.get('review'):
                        c['review'] = old['review']
                new_obj['candidates'] = sorted({c['videoId']: c for c in new_obj['candidates']}.values(), key=lambda x: x['videoId'])
            gh.put(path, new_obj, sha)
            note(f'Publicado em {REPO}: {path}')
    else:
        note('Modo local (sem token/dry-run): NÃO publicou no GitHub.')
    note(f"Finalizado: {report['counts']['newCandidates']} candidatos novos; {report['counts']['linksUnavailable']} links indisponíveis")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        note(f'ERRO controlado: {type(exc).__name__}: {exc}')
        sys.exit(1)
