import datetime as dt
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from update_ppvs import parse_events, merge_preserving_sources


class TestListing(unittest.TestCase):
    def test_wwe_official_upcoming(self):
        html = '''<h2>UPCOMING</h2><p>Crown Jewel 2026</p><p>Saturday, Nov. 7, 2026, Riyadh</p>
                  <p>Survivor Series: WarGames 2026</p><p>Saturday, Nov. 28, 2026, Houston</p>
                  <p>Raw</p><p>Monday, Nov. 30, 2026</p>'''
        rows = parse_events(html, 'wwe', dt.date(2026, 11, 15))
        self.assertEqual([r['date'] for r in rows], ['2026-11-07', '2026-11-28'])
        self.assertTrue(all(not r['sources'] for r in rows))

    def test_njpw_major_only(self):
        html = '''<h3>Bound for Unbound</h3><p>FRI.Nov.20.2026</p>
                  <h3>WRESTLE KINGDOM 21</h3><p>MON.Jan.4.2027</p>'''
        rows = parse_events(html, 'njpw', dt.date(2026, 11, 15))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['title'], 'WRESTLE KINGDOM 21')

    def test_dedup(self):
        html = '<h3>DOMINION</h3><p>SUN.Jun.14.2026</p><h3>DOMINION</h3><p>SUN.Jun.14.2026</p>'
        rows = parse_events(html, 'njpw', dt.date(2026, 6, 14))
        self.assertEqual(len(rows), 1)

    def test_keep_reviewed_existing_video_on_event_refresh(self):
        previous = {'id': 'x', 'sources': [{'type': 'okru', 'videoId': '123456789'}],
                    'status': 'video_available', 'sourceReview': 'approved'}
        found = {'id': 'x', 'sources': [], 'status': 'awaiting_video'}
        result = merge_preserving_sources(previous, found)
        self.assertEqual(result['sources'], previous['sources'])
        self.assertEqual(result['sourceReview'], 'approved')

    def test_no_generated_video_links(self):
        html = '<h3>SAKURA GENESIS</h3><p>SAT.Apr.3.2027</p>'
        rows = parse_events(html, 'njpw', dt.date(2027, 3, 30))
        self.assertEqual(rows[0]['status'], 'awaiting_video')
        self.assertEqual(rows[0]['sources'], [])

if __name__ == '__main__':
    unittest.main()
