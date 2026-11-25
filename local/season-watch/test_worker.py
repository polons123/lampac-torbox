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
        self.removals=[]
        self.items['box'][0]['raw']['id']=11
    def inventory(self,place):
        if place=='box' and self.box_down:raise Unavailable('HTTP 503')
        return self.items[place]
    def calendar(self,path,**kw):
        if '/season/' not in path:return {'name':'Show','original_name':'Show'}
        return {'episodes':[dict(episode_number=i,air_date='2020-01-01') for i in range(1,4)]}
    def jac(self,*args,**kwargs): return self.rows
    def candidate_files(self,place,h):return self.candidate
    def ts(self,place,op,**kw):
        if op=='rem':
            self.items[place]=[t for t in self.items[place] if t['hash']!=kw['hash']]
            self.removals.append((place,kw['hash']))
        else: assert op=='set' and kw['hash']==B
    def ts_files(self,place,h,save=False):
        if save:
            self.writes.append((place,h));self.items[place].append(torrent(h,self.candidate))
        return self.candidate
    def box(self,path,form):
        assert path=='createtorrent' and form['add_only_if_cached']=='true' and form['as_queued']=='false'
        self.writes.append(('box',B));new=torrent(B,self.candidate);new['raw']['id']=12
        self.items['box'].append(new)
    def box_deletion(self,actor,body,check=False):
        assert actor and body.get('confirmed') and not check
        self.items['box']=[t for t in self.items['box'] if t['raw']['id']!=body['id']]
        self.removals.append(('box',body['id']))
        return dict(success=True,deletion_confirmed=True)

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
    def test_infuse_metadata_and_media_sources(self):
        self.p.cfg={'ts':{p:{'url':'https://'+p+'.invalid'} for p in ('home','peer')}}
        for place in w.NAMES:
            item=self.p.items[place][0]
            item.update(original='Original',year='2026',files=[dict(file(8),id=0),dict(name='cover.jpg',id=3)])
            result=w.media(self.p,place,A)
            self.assertEqual(result['movie']['original_name'],'Original')
            self.assertEqual(result['movie']['first_air_date'],'2026-01-01')
            self.assertEqual(len(result['files']),1)
            f=result['files'][0]
            self.assertEqual((f['season'],f['episode'],f['id']),(2,8,0))
            if place=='box':
                self.assertEqual(result['box_id'],11)
                self.assertNotIn('url',f)
            else:
                self.assertEqual(f['url'],'https://'+place+'.invalid/stream/'+f['name']+'?link='+A+'&index=0&play')
        item=self.p.items['home'][0]
        item.update(movie=True,tv=False)
        result=w.media(self.p,'home',A)
        self.assertEqual(result['movie']['id'],19)
        self.assertEqual(result['movie']['release_date'],'2026-01-01')
        self.assertNotIn('episode',result['files'][0])
    def test_card_update_and_archive(self):
        self.scan();self.p.rows=[jac(B)]
        self.p.candidate=[dict(name='New.S02E03.mkv',size=96),file(1,9999)]
        self.p.rows[0].update(audio=[],voices=['Other'])
        result=w.update_release(self.p,self.s,'peer',A,0)
        self.assertEqual(result['ready'],[3]);self.assertEqual(self.p.writes,[('peer',B)])
        self.assertEqual([t['hash'] for t in self.p.inventory('peer')],[B]);self.assertEqual(self.p.removals,[('peer',A)])
        self.assertEqual([c['hash'] for c in w.catalog(self.p,self.s)['cards'] if c['place']=='peer'],[B])
        with self.assertRaises(Unavailable):w.update_release(self.p,self.s,'peer',A,0)
        self.assertEqual(len(self.p.writes),1)
    def test_no_new_episode(self):
        self.scan();self.p.rows=[jac(B)];self.p.candidate=[file(2,777)]
        self.assertIn('Новых',w.update_release(self.p,self.s,'peer',A,0)['message'])
        self.assertFalse(self.p.writes)
    def test_box_cache_and_outage(self):
        self.scan();self.p.rows=[jac(B)]
        w.update_release(self.p,self.s,'box',A,0,actor='viewer');self.assertEqual(self.p.writes,[('box',B)])
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
    def test_torrserver_remove_scoped(self):
        for place in ('home','peer'):
            with self.subTest(place=place):
                self.p=Sources();self.scan();calls=[]
                def remove(where,action,**fields):
                    calls.append((where,action,fields))
                    self.p.items[where]=[]
                self.p.ts=remove
                reply=w.remove_release(self.p,self.s,place,A,None,'viewer')
                self.assertTrue(reply['confirmed'])
                self.assertEqual(calls,[(place,'rem',{'hash':A})])
                self.assertEqual(len(w.catalog(self.p,self.s)['cards']),2)
                self.assertNotIn(self.get_id(place),self.s['watches'])
    def get_id(self,place):
        return w.item_id(place,A,2)
    def test_torbox_delayed_and_retry(self):
        self.p.items['box'][0]['raw']['id']=77
        self.scan();calls=[]
        def native(actor,body,check=False):
            calls.append((actor,body,check))
            if check:
                self.p.items['box']=[]
                return dict(success=True,deletion_confirmed=True)
            return dict(success=True,ticket='receipt',scope='account',check_after=1060000)
        self.p.box_deletion=native
        with self.assertRaises(Unavailable): w.remove_release(self.p,self.s,'box',A,76,'viewer')
        with patch('worker.time.time',return_value=1000):
            self.assertTrue(w.remove_release(self.p,self.s,'box',A,77,'viewer')['pending'])
            self.assertTrue(w.remove_release(self.p,self.s,'box',A,77,'viewer')['pending'])
        self.assertEqual(calls,[('viewer',{'id':77,'confirmed':True},False)])
        with patch('worker.time.time',return_value=1061):
            self.assertTrue(w.check_removal(self.p,self.s,'box',A)['confirmed'])
        self.assertEqual(len(calls),2)
        self.assertEqual(len(w.catalog(self.p,self.s)['cards']),2)
    def test_delete_removes_whole_recorded_chain_only(self):
        unrelated='f'*40
        self.p.items['peer'] += [torrent(h,[file(1)]) for h in [B,C,unrelated]]
        self.s['replaced']={'peer:'+A:B,'peer:'+B:C}
        result=w.remove_release(self.p,self.s,'peer',C,None,'viewer')
        self.assertTrue(result['confirmed'])
        self.assertEqual(self.p.removals,[('peer',A),('peer',B),('peer',C)])
        self.assertEqual([t['hash'] for t in self.p.inventory('peer')],[unrelated])
        self.assertEqual([t['hash'] for t in self.p.inventory('home')],[A])
    def test_update_failure_keeps_original(self):
        self.scan();self.p.rows=[jac(B)];self.p.ts_files=lambda *args,**kwargs:[file(2)]
        with self.assertRaises(Unavailable): w.update_release(self.p,self.s,'peer',A,0)
        self.assertFalse(self.p.removals)
        self.assertEqual([t['hash'] for t in self.p.inventory('peer')],[A])
    def test_deferred_update_cleanup_survives_reload(self):
        self.scan();self.p.rows=[jac(B)];original=self.p.box_deletion;calls=[]
        def native(actor,body,check=False):
            calls.append(check)
            if check:return original(actor,dict(id=body['id'],confirmed=True))
            return dict(success=True,ticket='abc',scope='fixture',check_after=1060000)
        self.p.box_deletion=native
        with patch('worker.time.time',return_value=1000):
            r=w.update_release(self.p,self.s,'box',A,0,actor='viewer')
            self.assertTrue(r['pending']);self.assertEqual(r['hash'],B)
        with patch('worker.time.time',return_value=1061):
            self.s=w.state()
            self.assertTrue(w.check_removal(self.p,self.s,'box',B)['confirmed'])
        self.assertEqual([t['hash'] for t in self.p.inventory('box')],[B])
        self.assertEqual(calls,[False,True])
    def test_cleanup_stops_if_new_version_gone(self):
        self.p.items['peer'].append(torrent(B,[file(3)]))
        self.s['replaced']={'peer:'+A:B}
        key=w.stage_group(self.s,'peer',B,self.p.items['peer'],'viewer',keep=B)
        self.p.items['peer'].pop()
        self.assertFalse(w.advance_group(self.p,self.s,key)['confirmed'])
        self.assertFalse(self.p.removals)
        self.s=w.state()
        self.assertEqual([r['hash'] for r in w.catalog(self.p,self.s)['cards'] if r['place']=='peer'],[A])
        self.assertTrue(w.remove_release(self.p,self.s,'peer',A,None,'viewer')['confirmed'])
    def test_replacement_chain_stays_archived_during_outage(self):
        self.p.items['box']=[torrent(A,[file(1)]),torrent(B,[file(1),file(2)])]
        self.s['replaced']={'box:'+A:B,'box:'+B:C}
        self.scan();self.p.box_down=True
        self.assertFalse(any(c['place']=='box' for c in w.catalog(self.p,self.s)['cards']))
        self.assertEqual(len(self.s['replaced']),2)
    def test_unconfirmed_preserves_card(self):
        self.scan()
        self.p.ts=lambda *args,**kwargs:None
        with patch('worker.time.time',return_value=1000):
            self.assertTrue(w.remove_release(self.p,self.s,'home',A,None,'viewer')['pending'])
        with patch('worker.time.time',return_value=1121):
            self.assertFalse(w.check_removal(self.p,self.s,'home',A)['confirmed'])
        self.assertIn(self.get_id('home'),self.s['watches'])
    def test_destination_must_have_new_episode(self):
        self.scan();self.p.rows=[jac(B)];self.p.ts_files=lambda *args,**kwargs:[file(1)]
        with self.assertRaises(Unavailable):w.update_release(self.p,self.s,'peer',A,0)
        self.assertFalse(self.p.writes)
if __name__=='__main__':unittest.main()
