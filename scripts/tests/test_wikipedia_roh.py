"""Offline regressions for wikipedia historical audits and ROH subject discovery."""
import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path))
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

wiki=load('wiki_import_test',ROOT/'scripts/import_wikipedia_ppvs.py')
archive=load('archive_subject_test',ROOT/'scripts/discover_archive.py')

class WikiAndROHTests(unittest.TestCase):
    def test_wikipedia_month_and_year_tables(self):
        html='''<h4>1998</h4><table class="wikitable"><tr><th>Date</th><th>Event</th></tr>
            <tr><td>February 22</td><td>SuperBrawl VIII</td></tr>
            <tr><td>March 15</td><td>Uncensored</td></tr></table>
            <h4>1999</h4><table class="wikitable"><tr><th>Event</th><th>Date</th></tr>
            <tr><td>Starrcade</td><td>December 19</td></tr></table>'''
        rows=wiki.extract(html,'wcw','https://en.wikipedia.org/wiki/example',dt.date(2026,11,16))
        self.assertEqual([x['date'] for x in rows],['1998-02-22','1998-03-15','1999-12-19'])
        self.assertTrue(all(x['referenceOnly'] and not x['sources'] for x in rows))
    def test_nxt_dates_with_year_in_cell(self):
        html='''<h2>Events</h2><table class="wikitable"><tr><th>No.</th><th>Event</th><th>Date</th></tr>
        <tr><td>1</td><td>TakeOver</td><td>May 29, 2014</td></tr>
        <tr><td>2</td><td>Fatal 4-Way</td><td>September 11, 2014</td></tr></table>'''
        rows=wiki.extract(html,'nxt','https://en.wikipedia.org/wiki/NXT_TakeOver',dt.date(2026,11,16))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['title'],'NXT TakeOver')
        self.assertEqual(rows[1]['title'],'NXT TakeOver: Fatal 4-Way')
    def test_failed_wikipedia_fetch_retains_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'config').mkdir()
            dest=root/'config/ppv_wikipedia_reference.json'
            sample={'company':'wcw','date':'1998-02-22','year':1998,'title':'SuperBrawl VIII'}
            dest.write_text(json.dumps({'events':[sample],'sources':{'wcw':{'checkedAt':'2026-11-15'}}}))
            with patch.object(wiki,'DEST',dest), patch.object(wiki,'PAGES',{'wcw':'some_page'}):
                result=wiki.update(fetch=lambda u: (_ for _ in ()).throw(TimeoutError()),now=dt.datetime(2026,11,16,tzinfo=dt.timezone.utc))
            self.assertEqual(result['sources']['wcw']['status'],'cached')
            self.assertEqual(result['events'][0],sample)
    def test_roh_subject_and_european_episode_date(self):
        self.assertEqual(archive.episode_date('ROH on HonorClub Episode 192 12-11-26'),'2026-11-12')
        def fetch(url):
            if 'advancedsearch.php' in url:
                self.assertIn('ROH+Wrestling',url)
                return {'response':{'docs':[{'identifier':'roh-192','title':'ROH on HonorClub Episode 192 12-11-26'}]}}
            return {'metadata':{'title':'ROH on HonorClub'},'files':[{'name':'ROH episode 192 12-11-26.mp4','length':'4100'}]}
        with patch.object(archive,'targets',return_value={('roh','2026-11-12'):[{'id':'auto:roh:2026-11-12:honorclub','show':'HonorClub','title':'HonorClub'}]}):
            ok,rows=archive.discover_roh_subject(fetch=fetch,now=dt.date(2026,11,16))
        self.assertTrue(ok)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['review'],'pendente')

if __name__=='__main__':unittest.main()
