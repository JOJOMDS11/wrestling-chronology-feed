"""Offline regressions for Internet Archive approval, dates, and safe publishing."""
import datetime as dt
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path))
    mod=importlib.util.module_from_spec(spec)
    sys.modules[name]=mod
    spec.loader.exec_module(mod)
    return mod

a=load('archive_discovery_under_test',ROOT/'scripts/discover_archive.py')
s=load('jojo_sync_under_test',ROOT/'.github/scripts/jojo_sync.py')

class ArchiveTests(unittest.TestCase):
 def test_opaque_id_and_safe_url(self):
  u=a.archive_url('wwe-nxt-sample-2026','NXT.2026.11.10.mp4')
  self.assertEqual(u,'https://archive.org/download/wwe-nxt-sample-2026/NXT.2026.11.10.mp4')
  self.assertRegex(a.candidate_id(u),r'^a[0-9a-f]{32}$')
  self.assertEqual(a.archive_url('..','../source.mp4'),'')
  self.assertEqual(a.archive_url('sample','readme.txt'),'')
 def test_metadata_candidates_are_not_auto_published(self):
  def fake_fetch(url):
   if '/advancedsearch.php' in url:
    return {'response':{'docs':[{'identifier':'nxt-sample-2026','title':'WWE NXT 2026.11.10','publicdate':'2026-11-12'}]}}
   return {'metadata':{'title':'WWE NXT 2026.11.10'},'files':[{'name':'WWE.NXT.2026.11.10.mp4'}]}
  fake_targets={('wwe','2026-11-10'):[{'id':'auto:wwe:2026-11-10:nxt','show':'NXT','title':'WWE NXT'}]}
  with patch.object(a,'targets',return_value=fake_targets):
   successes,candidates=a.discover(fetch=fake_fetch,now=dt.date(2026,11,16))
  self.assertEqual(successes,len(a.TOPICS))
  self.assertEqual(len(candidates),1)
  self.assertEqual(candidates[0]['possibleEventIds'],['auto:wwe:2026-11-10:nxt'])
  self.assertEqual(candidates[0]['review'],'pendente')
 def test_approved_archive_fails_closed(self):
  u='https://archive.org/download/nxt-sample-2026/WWE.NXT.2026.11.10.mp4'
  video_id='a'+hashlib.sha256(u.encode()).hexdigest()[:32]
  good={'platform':'archive','videoId':video_id,'url':u}
  self.assertEqual(s.approved_source(good)['url'],u)
  self.assertEqual(s.source_key(s.approved_source(good)),('archive',video_id))
  self.assertIsNone(s.approved_source(dict(good,url='https://evil.example/video.mp4')))
  self.assertIsNone(s.approved_source(dict(good,url='https://archive.org/download/../x.mp4')))
 def test_approval_only_adds_source_on_manual_consent_record(self):
  url='https://archive.org/download/nxt-sample-2026/WWE.NXT.2026.11.10.mp4'
  vid='a'+hashlib.sha256(url.encode()).hexdigest()[:32]
  events={'events':[{'id':'auto:wwe:2026-11-10:nxt','company':'wwe','date':'2026-11-10','show':'NXT','title':'WWE NXT','sources':[]}]}
  with patch.object(s,'read',side_effect=OSError('not available')):
   updated=s.consume_approvals(events,{'approved':[{'platform':'archive','videoId':vid,'url':url,'eventId':'auto:wwe:2026-11-10:nxt','approvedAt':'2026-11-16'}]})
  self.assertEqual(updated,1)
  self.assertEqual(events['events'][0]['sources'][0]['type'],'archive')
  self.assertEqual(events['events'][0]['sources'][0]['url'],url)
  self.assertEqual(s.consume_approvals(events,{'approved':[{'platform':'archive','videoId':vid,'url':url,'eventId':'auto:wwe:2026-11-10:nxt'}]}),0)

if __name__=='__main__':unittest.main()
