import datetime as dt, importlib.util, sys, unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('arch_cfg', str(ROOT / 'scripts/discover_archive.py'))
a = importlib.util.module_from_spec(spec); sys.modules['arch_cfg'] = a; spec.loader.exec_module(a)

FILES = [{'name': 'WWE.SmackDown.2026.07.03.1080p.H265.mp4', 'length': '6000'},
         {'name': 'WWE.SmackDown.2026.07.03.720p.H264.mp4', 'length': '6000'},
         {'name': 'WWE.SmackDown.2026.07.03.trailer.mp4', 'length': '6000'},
         {'name': 'readme.txt'}]
EV = {('wwe', '2026-07-03'): [{'id': 'wwe:2026-07-03:smackdown', 'show': 'SmackDown', 'title': 'SmackDown'}]}

class T(unittest.TestCase):
    def fetch(self, url):
        if 'advancedsearch' in url:
            return {'response': {'docs': [{'identifier': 'wwesd05'}]}}
        return {'metadata': {'title': 'WWE SmackDown'}, 'files': FILES if 'wwesd04' in url else []}
    def run_it(self):
        with patch.object(a, 'targets', return_value=EV):
            return a.discover_configured(self.fetch, dt.date(2026, 11, 16),
                {'identifiers': ['wwesd04'], 'siblingSearch': True}, sleep=lambda s: None)
    def test_prefers_h264_and_skips_trailer(self):
        n, rows = self.run_it()
        self.assertEqual(n, 2)
        self.assertEqual(len(rows), 1)
        self.assertIn('720p.H264', rows[0]['url'])
        self.assertEqual(rows[0]['review'], 'pendente')
        self.assertEqual(rows[0]['possibleEventIds'], ['wwe:2026-07-03:smackdown'])
    def test_no_config_does_nothing(self):
        self.assertEqual(a.discover_configured(self.fetch, config={}), (0, []))
if __name__ == '__main__': unittest.main()
