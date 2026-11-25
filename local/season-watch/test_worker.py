import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import worker as w
from providers import Unavailable, episode_files

A='a'*40
B='b'*40
C='c'*40
P='1'*64
def file(n, size=None, suffix='1080p'):
    return dict(name=f'Show.S02E{n:02d}.{suffix}.WEB-DL.mkv',size=size or n*200)
def jac(h):
    return dict(hash=h,page=P,label='Show Season 2 1080p',height=1080,audio=['studio'],voices=['Studio'],seasons=[2],tracker='tracker.test')
def torrent(h,fs):
    return dict(hash=h,title='Show',original='Show',label='Show Season 2 1080p',files=fs,tmdb=19,tv=True,movie=False,raw={'data':'{}','download_present':True})
class Sources:
    def __init__(self):
        self.items={k:[torrent(A,[file(1),file(2)])] for k in w.NAMES}
        self.candidate=[file(1),file(2),file(3)]
        self.rows=[jac(A)]
        self.box_down=False
        self.writes=[]
    def inventory(self,place):
        if place=='box' and self.box_down:raise Unavailable('HTTP 503')
        return self.items[place]
    def calendar(self,path,**kw):
        if '/season/' not in path:return {'name':'Show','original_name':'Show'}
        return {'episodes':[dict(episode_number=i,air_date='2020-01-01') for i in range(1,4)]}
    def jac(self,*args): return self.rows
    def candidate_files(self,place,h):return self.candidate
    def ts(self,place,op,**kw):
        assert op=='set' and kw['hash']==B
    def ts_files(self,place,h,save=False):
        if save:
            self.writes.append((place,h));self.items[place].append(torrent(h,self.candidate))
        return self.candidate
    def box(self,path,form):
        assert path=='createtorrent' and form['add_only_if_cached']=='true' and form['as_queued']=='false'
        self.writes.append(('box',B));self.items['box'].append(torrent(B,self.candidate))

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name)
        for name,val in [('ROOT',root),('STATE',root/'state.json')]:
            ctx=patch.object(w,name,val);ctx.start();self.addCleanup(ctx.stop)
        self.addCleanup(self.tmp.cleanup)
        self.p=Sources();self.s=w.state()
    def scan(self):
        w.scan(self.p,self.s);w.save(self.s)
    def get(self,place='peer'):
        return self.s['watches'][w.item_id(place,A,2)]
    def test_change_and_apply(self):
        self.scan();self.assertEqual(self.get()['waiting'],[3])
        self.p.rows=[jac(B)];self.scan();row=self.get()
        self.assertEqual(row['ready'],[3]);self.assertEqual(self.p.writes,[])
        w.action(self.p,self.s,'apply',row['id'],w.revision(row),'')
        self.assertEqual(self.p.writes,[('peer',B)]);self.assertEqual(len(self.p.inventory('peer')),2)
        with self.assertRaises(Unavailable):w.action(self.p,self.s,'apply',row['id'],w.revision(row),'')
        self.scan();self.assertNotIn(row['id'],self.s['watches'])
    def test_box_cache_only(self):
        self.scan();self.p.rows=[jac(B)];self.scan();row=self.get('box')
        w.action(self.p,self.s,'apply',row['id'],w.revision(row),'');self.assertEqual(self.p.writes,[('box',B)])
    def test_stale_or_ambiguous(self):
        self.scan();self.p.rows=[jac(B)];self.scan();row=self.get()
        self.p.rows=[jac(B),jac(C)]
        with self.assertRaises(Unavailable):w.action(self.p,self.s,'apply',row['id'],w.revision(row),'')
        self.assertFalse(self.p.writes)
    def test_manual_source(self):
        self.p.rows=[jac(B)];self.scan();row=self.get()
        self.assertEqual(row['ready'],[]);self.assertEqual(len(row['choices']),1)
        w.action(self.p,self.s,'bind',row['id'],w.revision(row),P)
        self.assertEqual(self.get()['ready'],[3]);self.assertFalse(self.p.writes)
    def test_provider_outage(self):
        self.scan();self.p.box_down=True;self.p.rows=[jac(B)];self.scan()
        self.assertEqual(self.get('peer')['ready'],[3]);self.assertEqual(self.get('box')['ready'],[])
        self.assertIn('error',self.s['checked']['box'])
    def test_files_tracks_and_quality(self):
        self.scan();self.p.rows=[jac(B)]
        for prop,bad in [('height',720),('voices',[]),('audio',[])]:
            self.p.rows=[{**jac(B),prop:bad}];self.scan();self.assertFalse(self.get()['ready'])
        self.p.rows=[jac(B)];self.p.candidate=[file(1,999),file(2),file(3)];self.scan();self.assertFalse(self.get()['ready'])
        self.p.candidate=[file(2),file(3)];self.scan();self.assertFalse(self.get()['ready'])
    def test_parser(self):
        rows=episode_files([dict(name='[099] Supernatural S05E17 - 99 Problems.mkv',size=2),dict(name='Sample.S05E18.mkv',size=3)])
        self.assertEqual(list(rows[5]),['17'])
        self.assertEqual(w.file_signature('Show.S01E01.1080.WEB.mkv'),w.file_signature('Show.S01E02.1080p.WEB.mkv'))
if __name__=='__main__':unittest.main()
