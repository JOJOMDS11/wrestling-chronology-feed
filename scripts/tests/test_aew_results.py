import datetime as dt
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from update_ppvs import parse_aew_results

TODAY = dt.date(2026, 11, 15)

class TestAEWOfficialResults(unittest.TestCase):
    def test_recent_dynamite_and_collision_and_ppv(self):
        html = '''<div><a href="/post/aew-dynamite-blood-and-guts-results-november-4-2026"><span>AEW Dynamite Blood and Guts Results: November 4, 2026 – Title</span></a>
        <a href="/post/aew-dynamite-collision-results-november-11-2026">AEW Dynamite &amp; Collision Results: November 11, 2026 – more</a>
        <a href="/post/aew-full-gear-results-november-14-2026">AEW Full Gear Results: November 14, 2026 – more</a></div>'''
        rows = parse_aew_results(html, TODAY)
        self.assertEqual([(r['date'],r['show']) for r in rows], [
            ('2026-11-04','Dynamite'), ('2026-11-11','Collision'),
            ('2026-11-11','Dynamite'), ('2026-11-14','PPV')])
        self.assertTrue(all(r['sources']==[] and r['status']=='awaiting_video' for r in rows))
        self.assertTrue(all(r['official_url'].startswith('https://www.allelitewrestling.com/post/') for r in rows))

    def test_slug_fallback_no_anchor(self):
        html = '''<script type="application/json">{"url":"/post/aew-collision-rock-n-wrestling-rager-results-november-7-2026"}</script>'''
        rows = parse_aew_results(html, TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['show'], 'Collision')
        self.assertEqual(rows[0]['date'], '2026-11-07')

    def test_no_future_or_unrelated(self):
        html = '''<a href="/post/aew-collision-results-november-28-2026">Collision Results Nov 28</a>
        <a href="/post/aew-collision-results-november-7-2026">Collision Results November 7, 2026</a>
        <a href="/post/aew-new-gear-updates-november-6-2026">Other post</a>'''
        rows = parse_aew_results(html, TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['date'],'2026-11-07')

    def test_duplicate_anchor_and_slug(self):
        html = '''<a href="/post/aew-fright-night-collision-results-october-31-2026">AEW Collision Results: October 31, 2026</a>'''
        rows = parse_aew_results(html, TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['show'],'Collision')

if __name__ == '__main__': unittest.main()
