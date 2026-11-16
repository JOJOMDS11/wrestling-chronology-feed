"""Coverage must never report a year without references as complete."""
import datetime as dt
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]

def load(module, path):
    spec=importlib.util.spec_from_file_location(module,str(path))
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

audit=load('jojo_coverage_module',ROOT/'scripts/audit_ppvs.py')
wiki=load('jojo_wikipedia_years_module',ROOT/'scripts/import_wikipedia_ppvs.py')

class CoverageTests(unittest.TestCase):
    def test_individual_company_year_coverage(self):
        samples={
          'wwe.json':{'catalogue':{'episodes':[{'id':'wwe1','date':'2026-04-18','title':'WrestleMania 42','show':'PPV','sources':[]},
              {'id':'wwe1','date':'2026-04-18','title':'WrestleMania 42','show':'PPV','sources':[]}]}},
          'roh.json':{'catalogue':{'episodes':[{'id':'roh1','date':'2026-11-12','title':'ROH Final Battle','show':'PPV','sources':[]}]}},
          'updates.json':{'events':[]},
          'config/ppv_reference.json':{'events':[{'id':'ref:wwe1','company':'wwe','date':'2026-04-18','year':2026,'title':'WrestleMania 42'}]},
          'config/ppv_wikipedia_reference.json':{'sources':{'wwe':{'status':'updated','count':2,'yearCounts':{'2026':2}},
              'roh':{'status':'updated','count':3,'yearCounts':{'2025':3}}},'events':[
              {'id':'wiki:wwe1','company':'wwe','date':'2026-04-18','year':2026,'title':'WrestleMania 42','referenceType':'wikipedia'},
              {'id':'wiki:wwe2','company':'wwe','date':'2026-07-10','year':2026,'title':'SummerSlam 2026','referenceType':'wikipedia'}]}}
        def sample(name):
            if name in samples:return json.loads(json.dumps(samples[name]))
            if name.endswith('.json'):return {'catalogue':{'episodes':[]}}
            raise FileNotFoundError(name)
        with patch.object(audit,'load',side_effect=sample):
            data=audit.report(dt.datetime(2026,11,16,tzinfo=dt.timezone.utc))
        rows={(row['company'],row['year']):row for row in data['coverageByCompanyYear']}
        self.assertEqual(rows['wwe',2026]['cataloguePpvs'],1)
        self.assertEqual(rows['wwe',2026]['matched'],1)
        self.assertEqual(rows['wwe',2026]['missing'],1)
        self.assertEqual(rows['wwe',2026]['coverage'],'partial')
        self.assertEqual(rows['roh',2026]['coverage'],'not_audited')
        self.assertTrue(rows['roh',2026]['unverifiedYear'])

    def test_per_year_reference_counts(self):
        self.assertEqual(wiki.count_years([{'year':2025},{'year':2025},{'year':2026}]),{'2025':2,'2026':1})

if __name__=='__main__':unittest.main()
