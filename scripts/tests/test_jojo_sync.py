"""Test provisional weekly schedule without external APIs or uploaded videos."""
import datetime as dt
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('test_jojo_sync_module',str(ROOT/'.github/scripts/jojo_sync.py'))
mod=importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name]=mod
SPEC.loader.exec_module(mod)

class WeeklyScheduleTests(unittest.TestCase):
    def test_missing_and_upcoming_and_no_automatic_links(self):
        rows={'events':[]}
        with patch.object(mod,'read',return_value={'catalogue':{'episodes':[]}}):
            n=mod.add_missing_weeklies(rows,dt.datetime(2026,11,16,12,tzinfo=dt.timezone.utc))
            self.assertGreater(n,25)
            self.assertEqual(mod.add_missing_weeklies(rows,dt.datetime(2026,11,16,12,tzinfo=dt.timezone.utc)),0)
        index={(e['company'],e['date'],e['show']):e for e in rows['events']}
        for key in [('wwe','2026-11-10','NXT'),('wwe','2026-11-13','SmackDown'),
                    ('wwe','2026-11-17','NXT'),('wwe','2026-11-20','SmackDown'),
                    ('roh','2026-11-19','HonorClub'),('tna','2026-11-19','Impact'),
                    ('aew','2026-11-18','Dynamite')]:
            self.assertIn(key,index)
            self.assertEqual(index[key]['sources'],[])
            self.assertTrue(index[key]['provisional'])
        self.assertIn('não confirma',index['wwe','2026-11-17','NXT']['description'])

    def test_keep_recorded_video_and_no_duplicate_day(self):
        previous={'id':'official:wwe:2026-11-20:smackdown','company':'wwe',
                  'date':'2026-11-20','show':'SmackDown','title':'Friday Night SmackDown',
                  'sources':[{'type':'okru','videoId':'1234567890111'}]}
        data={'events':[previous]}
        with patch.object(mod,'read',return_value={'catalogue':{'episodes':[]}}):
            mod.add_missing_weeklies(data,dt.datetime(2026,11,16,12,tzinfo=dt.timezone.utc))
        self.assertEqual(sum(e.get('date')=='2026-11-20' and e.get('show')=='SmackDown' for e in data['events']),1)
        self.assertEqual(previous['sources'],[{'type':'okru','videoId':'1234567890111'}])

if __name__=='__main__':unittest.main()
