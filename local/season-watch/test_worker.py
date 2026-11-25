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
    return dict(hash=h,page=P,label='Show Season 2 1080p',height=1080,title='Show',original='Show',audio=['studio'],voices=['Studio'],seasons=[2],tracker='tracker.test')
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
    def jac(self,*args,**kwargs): return self.rows
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
    def test_card_update_and_archive(self):
        self.scan();self.p.rows=[jac(B)]
        self.p.candidate=[dict(name='New.S02E03.mkv',size=96),file(1,9999)]
        self.p.rows[0].update(audio=[],voices=['Other'])
        result=w.update_release(self.p,self.s,'peer',A,0)
        self.assertEqual(result['ready'],[3]);self.assertEqual(self.p.writes,[('peer',B)])
        self.assertEqual(len(self.p.inventory('peer')),2)
        self.assertEqual([c['hash'] for c in w.catalog(self.p,self.s)['cards'] if c['place']=='peer'],[B])
        w.update_release(self.p,self.s,'peer',A,0);self.assertEqual(len(self.p.writes),1)
    def test_no_new_episode(self):
        self.scan();self.p.rows=[jac(B)];self.p.candidate=[file(2,777)]
        self.assertIn('Новых',w.update_release(self.p,self.s,'peer',A,0)['message'])
        self.assertFalse(self.p.writes)
    def test_box_cache_and_outage(self):
        self.scan();self.p.rows=[jac(B)]
        w.update_release(self.p,self.s,'box',A,0);self.assertEqual(self.p.writes,[('box',B)])
        self.p.box_down=True
        catalog=w.catalog(self.p,self.s);self.assertIn('box',catalog['errors'])
        self.assertTrue(any(c['place']=='peer' for c in catalog['cards']))
    def test_ambiguous_then_selected(self):
        self.p.rows=[jac(B),{**jac(C),'page':'2'*64}]
        res=w.update_release(self.p,self.s,'peer',A,0)
        self.assertEqual(len(res['choices']),2);self.assertFalse(self.p.writes)
        with self.assertRaises(Unavailable):w.update_release(self.p,self.s,'peer',A,0,'f'*64)
        w.update_release(self.p,self.s,'peer',A,0,res['choices'][0]['value'])
        self.assertEqual(self.p.writes,[('peer',B)])
    def test_movie_and_different_sources(self):
        self.p.items['home'][0].update(movie=True,tv=False,files=[{'id':1,'name':'movie.mkv','size':60}])
        self.scan()
        cards=w.catalog(self.p,self.s)['cards'];self.assertEqual(len(cards),3)
        self.assertTrue(next(c for c in cards if c['place']=='home')['movie'])
        self.assertIn('актуальна',w.update_release(self.p,self.s,'home',A,0)['message'])
        self.p.rows=[jac(B)];w.update_release(self.p,self.s,'home',A,0)
        self.assertEqual(self.p.writes,[('home',B)])
    def test_destination_must_have_new_episode(self):
        self.scan();self.p.rows=[jac(B)];self.p.ts_files=lambda *args,**kwargs:[file(1)]
        with self.assertRaises(Unavailable):w.update_release(self.p,self.s,'peer',A,0)
        self.assertFalse(self.p.writes)
if __name__=='__main__':unittest.main()
