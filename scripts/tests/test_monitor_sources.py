import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitor_sources import (list_profile_links, check_video, monitor, identify_event,
                             profile_state)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.conf = {'profiles': [
            {'id': 'okru-1', 'label': 'Conta 1', 'url': 'https://ok.ru/profile/910420669433/video', 'enabled': True},
            {'id': 'okru-2', 'label': 'Conta 2', 'url': 'https://ok.ru/profile/910148261710/video', 'enabled': True}
        ], 'video_examples_for_review': [{'id': '16916107430649', 'note': 'Exemplo informado, identidade não confirmada'}]}
        self.updates = {'events': [{'id': 'official:aew:2026-11-11:dynamite', 'title': 'Dynamite', 'date': '2026-11-11', 'company': 'aew', 'sources': []}]}

    def test_extract_exact_video_urls(self):
        html = ('<a href="/video/16975515421433">AEW Dynamite 11.11.2026</a>'
                '<a href="https://ok.ru/video/16975515421433">duplicate</a>'
                '<a href="https://ok.ru/videoembed/1234567890">not a video link</a>')
        result = list_profile_links(html)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['videoId'], '16975515421433')

    def test_profile_thumbnail_and_span(self):
        links = list_profile_links('<a href="/video/16975515421433"><img src="x"><span>Full Gear 2026</span></a>')
        self.assertEqual(len(links), 1)
        self.assertIn('Full Gear', links[0]['title'])

    def test_restricted_not_mistaken_for_deleted(self):
        self.assertEqual(check_video(403, '')['status'], 'acesso_restrito')
        self.assertEqual(check_video(429, '')['status'], 'acesso_restrito')
        self.assertEqual(check_video(0, '')['status'], 'verificacao_inconclusiva')
        self.assertEqual(profile_state(403, '', 0), 'acesso_restrito')

    def test_deletion_requires_two_checks(self):
        first = check_video(404, '')
        self.assertEqual(first['status'], 'reverificacao_pendente')
        second = check_video(404, '', first)
        self.assertEqual(second['status'], 'indisponivel')
        self.assertEqual(check_video(200, '<html>ok</html>', second)['status'], 'pagina_respondeu')

    def test_soft_missing_two_checks(self):
        one = check_video(200, 'Видео удалено')
        two = check_video(200, 'Видео удалено', one)
        self.assertEqual(one['status'], 'reverificacao_pendente')
        self.assertEqual(two['status'], 'indisponivel')

    def test_unrelated_title_does_not_match(self):
        self.assertEqual(identify_event('Soccer game 11.11.2026', self.updates['events']), [])

    def test_profile_blocked_keeps_existing_candidate(self):
        def fetch(url):
            return (403, '') if '/profile/' in url else (200, '<html>ok</html>')
        existing = {'candidates': [{'videoId': '1212121212', 'url': 'https://ok.ru/video/1212121212', 'review': 'pendente'}]}
        report, candidates = monitor(self.conf, self.updates, existing, {'links': {}}, fetcher=fetch, timestamp='2026-11-15T18:00:00Z')
        ids = {x['videoId'] for x in candidates['candidates']}
        self.assertIn('1212121212', ids)
        self.assertIn('16916107430649', ids)
        self.assertEqual(report['counts']['profilesReadable'], 0)
        self.assertEqual(len(report['profiles']), 2)

    def test_found_video_goes_to_manual_review_not_event_sources(self):
        def fetch(url):
            if '/profile/' in url:
                return 200, '<a href="/video/16975515421433">AEW Dynamite 11.11.2026</a>'
            return 200, '<html>available</html>'
        report, candidates = monitor(self.conf, self.updates, {}, {}, fetcher=fetch, timestamp='2026-11-15T18:00:00Z')
        c = next(x for x in candidates['candidates'] if x['videoId'] == '16975515421433')
        self.assertEqual(c['review'], 'pendente')
        self.assertIn('official:aew:2026-11-11:dynamite', c['possibleEventIds'])
        self.assertEqual(self.updates['events'][0]['sources'], [])
        self.assertEqual(report['counts']['profilesReadable'], 2)

    def test_lost_url_stays_listed_for_recheck(self):
        config={'profiles': [],'video_examples_for_review':[{'id':'16916107430649'}]}
        report,_=monitor(config,self.updates,{}, {'links': {'16916107430649': {'status': 'reverificacao_pendente', 'consecutiveMissing': 1}}}, fetcher=lambda url: (410,''), timestamp='2026-11-15T18:00:00Z')
        self.assertEqual(report['links']['16916107430649']['status'], 'indisponivel')
        self.assertEqual(report['counts']['linksUnavailable'], 1)

if __name__ == '__main__':
    unittest.main()
