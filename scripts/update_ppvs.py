#!/usr/bin/env python3
"""Update JOJO Library's *event listings* from official schedules.

This does NOT scrape, download or automatically embed video streams.
Only new WWE premium live events and major NJPW cards are listed,
and each entry remains 'awaiting video' until an authorized URL is provided.
"""
import datetime as dt
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parents[1]
DEST = HERE / 'updates.json'
SOURCES = {
    'wwe': 'https://www.wwe.com/article/wwe-upcoming-events',
    'njpw': 'https://www.njpw1972.com/schedule/',
}
MONTHS = {m: i for i, m in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split(), 1)}
DATE = re.compile(r'\b(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\s*[.,]?\s*'
                  r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[.]?\s*(\d{1,2})\s*[,./]?\s*(20\d{2})', re.I)
# Only broadly advertised WWE Premium Live Events. More names can be added here.
WWE_PPV = re.compile(r'\b(?:WrestleMania|Royal Rumble|SummerSlam|Survivor Series|Money in the Bank|Elimination Chamber|'
                     r'Backlash|Crown Jewel|Wrestlepalooza|Clash at the Castle|Clash in Paris|'
                     r'Night of Champions|Evolution|King and Queen of the Ring|Saturday Night.s Main Event)\b', re.I)
# NJPW publishes many local shows; restrict this list to marquee cards.
NJPW_PPV = re.compile(r'\b(?:WRESTLE KINGDOM|DOMINION|POWER STRUGGLE|SAKURA GENESIS|KING OF PRO.WRESTLING|'
                      r'DESTRUCTION IN|NEW BEGINNING IN|RESURGENCE|BATTLE IN THE VALLEY|'
                      r'CAPITAL COLLISION|WRESTLE GRAND SLAM|HISTORIC X.OVER|WINDY CITY RIOT)\b', re.I)


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.hidden += 1
        if tag in {'br', 'p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'article', 'section', 'tr'}:
            self.nodes.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {'p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'article', 'section', 'tr'}:
            self.nodes.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.nodes.append(data)


def lines_from_html(html):
    p = VisibleText()
    p.feed(html)
    # Fragmented markup can split event names across multiple text nodes;
    # preserve text segments and dates, but drop pure punctuation and noise.
    text = ''.join(p.nodes)
    lines = [re.sub(r'\s+', ' ', line).strip() for line in text.split('\n')]
    return [line for line in lines if line and len(line) < 250]


def parse_date(text):
    m = DATE.search(text)
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
    except ValueError:
        return None


def parse_events(html, company, today=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    earliest = today - dt.timedelta(days=45)
    latest = today + dt.timedelta(days=500)
    lines = lines_from_html(html)
    if company == 'wwe':
        # The WWE page has a distinct UPCOMING heading; read the following list.
        indices = [i for i, line in enumerate(lines) if line.strip().upper() == 'UPCOMING']
        if indices:
            lines = lines[indices[-1] + 1:]
    keywords = WWE_PPV if company == 'wwe' else NJPW_PPV
    events = {}
    for i, line in enumerate(lines):
        date = parse_date(line)
        if date is None or not earliest <= date <= latest:
            continue
        # Official schedules put the event name shortly before the date.
        possible = [x.strip(' :\u2014-') for x in lines[max(0, i - 4):i]][::-1]
        noise = {'IMAGE','MATCH LINEUP','TICKETS','READ MORE','MORE DETAILS'}
        meaningful = [x for x in possible if x.upper() not in noise and not x.startswith('http')]
        title = meaningful[0] if meaningful and keywords.search(meaningful[0]) and parse_date(meaningful[0]) is None and len(meaningful[0]) <= 110 else None
        if not title:
            continue
        title = re.sub(r'\s+', ' ', title).strip()
        # Prevent accidental inclusion of headings and adjacent advertisements.
        if title.upper() in {'UPCOMING', 'SCHEDULE', 'MATCH LINEUP', 'TICKETS'}:
            continue
        id_base = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:80]
        identity = f'official:{company}:{date.isoformat()}:{id_base}'
        events[identity] = {
            'id': identity,
            'company': company,
            'title': title,
            'date': date.isoformat(),
            'year': date.year,
            'show': 'PPV',
            'official_url': SOURCES[company],
            'status': 'awaiting_video',
            'sources': [],
        }
    return list(events.values())


def fetch(url):
    request = Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; JOJOLibrarySchedule/1.0)',
        'Accept': 'text/html,application/xhtml+xml',
    })
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f'HTTP {response.status}')
        return response.read(4_000_000).decode('utf-8', errors='replace')


def run():
    existing = json.loads(DEST.read_text(encoding='utf-8')) if DEST.exists() else {}
    saved = {event['id']: event for event in existing.get('events', []) if 'id' in event}
    now = dt.datetime.now(dt.timezone.utc)
    success = 0
    for company, url in SOURCES.items():
        try:
            found = parse_events(fetch(url), company, now.date())
            if not found:
                print(f'AVISO: {company} não retornou novos eventos reconhecidos. Mantendo os anteriores.')
                continue
            success += 1
            for event in found:
                saved[event['id']] = event
            print(f'{company.upper()}: {len(found)} eventos oficiais encontrados')
        except Exception as exc:
            print(f'AVISO: falha ao consultar {company}: {exc}. Mantendo os anteriores.', file=sys.stderr)
    # Preserve discovered events for the most recent year; never purge on fetch error.
    cutoff = (now - dt.timedelta(days=365)).date().isoformat()
    results = sorted((event for event in saved.values() if event.get('date', '0000') >= cutoff),
                     key=lambda e: (e['date'], e['company'], e['title']))
    if results == existing.get('events', []):
        print('Nenhuma mudança nos PPVs. Arquivo preservado.')
        return
    payload = {
        'schemaVersion': 1,
        'generatedAt': now.isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'events': results,
        'sources': list(SOURCES.values()),
    }
    DEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Atualização concluída: {len(results)} eventos no feed ({success} fontes respondendo)')


if __name__ == '__main__':
    run()
