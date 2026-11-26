import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import worker as w
from providers import Providers, Unavailable, episode_files

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
        if '/season/' not in path:return {'id':19,'name':'Show','original_name':'Show'}
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
    def update(self,*args,**kwargs):
        preview=w.update_release(self.p,self.s,*args,**kwargs)
        if 'offer' not in preview: return preview
        return w.update_release(self.p,self.s,*args,**kwargs,confirmation=preview['confirmation'])
    def scan(self):
        w.scan(self.p,self.s);w.save(self.s)
    def get(self,place='peer'):
        return self.s['watches'][w.item_id(place,A,2)]
    def test_file_numbers_and_folder_season(self):
        files=[dict(name=n,size=100) for n in ['Season 1/Второй сезон/03.mkv','Мультфильм.2x2.mkv','Сериал.2x05.mkv','Show.S02E07-E08.mkv']]
        self.assertEqual(set(episode_files(files)[2]),{'3','5','7','8'})

    def test_explicit_episode_pairs_share_one_file(self):
        for name in ('Show.S03E01E02','Show.S03E01-E02','Show.S03E01-S03E02','Show.3x01-02','Show.3x01-3x02'):
            t=self.p.items['home'][0]
            t['files']=[dict(id=7,name=name+'.mkv',size=100)]
            self.p.cfg={'ts':{'home':{'url':'https://home.invalid'}}}
            files=w.media(self.p,'home',A)['files']
            self.assertEqual(len(files),1)
            self.assertEqual((files[0]['id'],files[0]['season'],files[0]['episodes']),(7,3,[1,2]))
            self.assertTrue(w.destination_has(t['files'],3,[1,2]))
            self.assertFalse(w.destination_has(t['files'],3,[3]))
        for name in ('Show.S03E01-S04E02','Show.S03E01E03','Show.S03E01E02E03'):
            self.assertEqual(episode_files([dict(name=name+'.mkv',size=100)]),{})

    def test_channel_tag_and_ambiguous_endpoint(self):
        for separator in ('.','-'):
            files=[dict(name='Сериал.S01E05'+separator+'2x2.mkv',size=100)]
            self.assertEqual(set(episode_files(files)[1]),{'5'})
        for ending in ('-2','-02','-10'):
            files=[dict(id=3,name='Show.S03E01'+ending+'.mkv',size=100)]
            self.p.items['box'][0]['files']=files
            self.assertFalse(w.destination_has(files,3,[2]))
            self.assertIn('episode_note',w.media(self.p,'box',A)['files'][0])

    def test_empty_box_inventory_keeps_calendar_and_blocks_update(self):
        self.scan()
        self.p.items['box'][0]['files']=[]
        self.s['pending_file_index']=[A]
        self.scan()
        row=self.get('box')
        self.assertTrue(row['stale'])
        self.assertEqual(row['owned'],[1,2])
        with self.assertRaises(Unavailable):
            w.update_release(self.p,self.s,'box',A,2)
        self.assertFalse(self.p.writes or self.p.removals)

    def test_cached_single_episodes_reindexed_with_new_pairs(self):
        self.s.update(parser_version=3,files={A:{'2':{'1':[['old.mkv',10]]}}})
        w.atomic(w.STATE,self.s)
        self.s=w.state()
        self.assertIn(A,self.s['pending_file_index'])
        self.p.items['home'][0]['files']=[dict(name='Show.S02E01E02.mkv',size=10)]
        self.scan()
        self.assertNotIn('pending_file_index',w.state())
        self.assertEqual(w.state()['watches'][w.item_id('home',A,2)]['owned'],[1,2])

    def test_metadata_applies_only_to_exact_hash(self):
        self.p.rows=[dict(jac(B),tags={'source':'WEB-DLRip'}),dict(jac(A),tags={'resolution':'720p'},measured={'resolution':'1080p'})]
        self.p.items['home'][0]['files']=[file(1)]
        self.scan()
        self.assertNotIn(B,self.s['release_metadata'])
        self.assertIn('1080p',self.s['catalog']['home'][0]['release_summary'])
        self.assertIn('720p',self.s['catalog']['box'][0]['release_summary'])
        self.assertNotIn('WEB-DLRip',self.s['catalog']['home'][0]['release_summary'])

    def test_new_movie_gets_exact_tags_on_first_catalog_read(self):
        def movie(h,label):
            return dict(torrent(h,[]),title='The Weight',original='The Weight',label=label,
                        year='2026',movie=True,tv=False,poster='/weight.jpg')
        self.p.items={'home':[movie(A,'[LAMPA] The Weight'),movie(B,'[LAMPA] Other Film [2022, 2160p]')],'peer':[],'box':[]}
        def search(title,original,movie,year,**kwargs):
            if (title,year)==('The Weight',2026):return [dict(jac(A),tags={'source':'WEB-DL','hdr':['SDR']})]
            if (title,year)==('Other Film',2022):return [dict(jac(B),tags={'source':'BDRemux'})]
            return []
        self.p.jac=search
        cards={c['hash']:c for c in w.catalog(self.p,self.s)['cards']}
        self.assertEqual(cards[A]['release_summary'],'WEB-DL · SDR')
        self.assertIn('BDRemux',cards[B]['release_summary'])
        self.assertNotIn('WEB-DL',cards[B]['release_summary'])
        with patch.object(self.p,'jac',side_effect=AssertionError('cached hash looked up twice')):
            self.assertEqual(w.catalog(self.p,w.state())['cards'],list(cards.values()))

    def test_direct_media_gets_tags_before_scan_or_catalog(self):
        self.p.cfg={'ts':{'home':{'url':'https://home.invalid'}}}
        t=self.p.items['home'][0]
        t.update(movie=True,tv=False,year='2026',files=[dict(id=1,name='Film.mkv',size=123)])
        self.p.rows=[dict(jac(A),tags={'source':'WEB-DL'},measured={'codec':'hevc'})]
        result=w.media(self.p,'home',A)
        self.assertEqual(result['files'][0]['release_summary'],'WEB-DL · HEVC')
        self.assertEqual(w.state()['release_metadata'][A]['measured'],{'codec':'hevc'})

    def test_catalog_outage_cooldown_survives_requests(self):
        self.p.items['home'].append(torrent(B,[]))
        with patch.object(w.time,'time',return_value=1000) as clock, patch.object(self.p,'jac',side_effect=Unavailable('offline')) as search:
            self.assertTrue(w.catalog(self.p,self.s)['cards'])
            self.assertEqual(search.call_count,2)
            clock.return_value=1059
            w.catalog(self.p,w.state())
            self.assertEqual(search.call_count,2)
            clock.return_value=1061
            search.side_effect=None
            search.return_value=[dict(jac(A),tags={'source':'WEB-DL'})]
            w.catalog(self.p,w.state())
            self.assertEqual(search.call_count,4)
            self.assertEqual(w.metadata_health(),{})

    def test_scan_reuses_misses_and_refreshes_claims_with_measurements(self):
        with patch.object(w,'lookup_release',return_value={}) as lookup, patch.object(w.time,'time',return_value=1000) as clock:
            self.scan()
            clock.return_value+=21600
            self.scan()
            self.assertEqual(lookup.call_count,1)
            clock.return_value+=21601
            lookup.return_value={'tags':{'source':'WEB-DL'}}
            self.scan()
            self.assertEqual(lookup.call_count,2)
            lookup.return_value={'measured':{'codec':'hevc'}}
            clock.return_value+=86401
            self.scan()
            self.assertEqual(lookup.call_count,3)
            entry=self.s['release_metadata'][A]
            self.assertEqual(entry['tags'],{'source':'WEB-DL'})
            self.assertEqual(entry['measured'],{'codec':'hevc'})

    def test_scan_shares_one_metadata_budget_across_libraries(self):
        self.p.items['home']=[torrent(f'{n:040x}',[]) for n in range(5)]
        self.p.items['peer']=[torrent(f'{n:040x}',[]) for n in range(5,10)]
        with patch.object(w.time,'monotonic',return_value=0) as timer:
            def slow_lookup(p,t,timeout):
                timer.return_value+=timeout
                return {}
            with patch.object(w,'lookup_release',side_effect=slow_lookup) as lookup:
                self.scan()
                self.assertEqual(timer.return_value,120)
                self.assertEqual(lookup.call_count,7)
                self.assertEqual(len(self.s['catalog']['peer']),5)

    def test_media_uses_leaf_and_excludes_attached_picture(self):
        self.p.cfg={'ts':{'home':{'url':'https://home.invalid'}}}
        self.p.items['home'][0].update(movie=True,tv=False,files=[
            dict(id=1,name='WEB-DL/Film.DV.HDR10+.mkv',ffprobe=[
                dict(codec_type='video',height=900,disposition={'attached_pic':1}),
                dict(codec_type='video',height=1600,codec_name='hevc'),
                dict(codec_type='audio',codec_name='aac',channel_layout='stereo'),
                dict(codec_type='audio',codec_name='AAC',channels=2)])])
        result=w.media(self.p,'home',A)
        self.assertEqual(result['files'][0]['release_summary'],'1600p · Dolby Vision · HDR10+ · HEVC · AAC · 2.0')

    def test_metadata_write_migrates_old_file_cache(self):
        self.s.update(files={A:{'1':{}}},parser_version=2)
        w.atomic(w.STATE,self.s)
        w.cache_metadata({B:{'tags':{'source':'WEB-DL'}}},{})
        persisted=json.loads(w.STATE.read_bytes())
        self.assertEqual((persisted['parser_version'],persisted['files']),(w.FILE_PARSER_VERSION,{}))

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
        self.p.candidate=[dict(name='New.S02E03.mkv',size=96),file(1,9999),file(2,8999)]
        self.p.rows[0].update(audio=[],voices=['Other'])
        result=self.update('peer',A,0)
        self.assertEqual(result['ready'],[3]);self.assertEqual(self.p.writes,[('peer',B)])
        self.assertEqual([t['hash'] for t in self.p.inventory('peer')],[B]);self.assertEqual(self.p.removals,[('peer',A)])
        self.assertEqual([c['hash'] for c in w.catalog(self.p,self.s)['cards'] if c['place']=='peer'],[B])
        with self.assertRaises(Unavailable):self.update('peer',A,0)
        self.assertEqual(len(self.p.writes),1)
    def test_no_new_episode(self):
        self.scan();self.p.rows=[jac(B)];self.p.candidate=[file(2,777)]
        self.assertIn('Новых',self.update('peer',A,0)['message'])
        self.assertFalse(self.p.writes)
    def test_box_cache_and_outage(self):
        self.scan();self.p.rows=[jac(B)]
        self.update('box',A,0,actor='viewer');self.assertEqual(self.p.writes,[('box',B)])
        self.p.box_down=True
        catalog=w.catalog(self.p,self.s);self.assertIn('box',catalog['errors'])
        self.assertTrue(any(c['place']=='peer' for c in catalog['cards']))
    def test_ambiguous_then_selected(self):
        self.p.rows=[jac(B),{**jac(C),'page':'2'*64}]
        res=self.update('peer',A,0)
        self.assertEqual(len(res['choices']),2);self.assertFalse(self.p.writes)
        with self.assertRaises(Unavailable):self.update('peer',A,0,'f'*64)
        self.update('peer',A,0,res['choices'][0]['value'])
        self.assertEqual(self.p.writes,[('peer',B)])
    def test_movie_and_different_sources(self):
        self.p.items['home'][0].update(movie=True,tv=False,files=[{'id':1,'name':'movie.mkv','size':60}])
        self.scan()
        cards=w.catalog(self.p,self.s)['cards'];self.assertEqual(len(cards),3)
        self.assertTrue(next(c for c in cards if c['place']=='home')['movie'])
        self.assertIn('актуальна',self.update('home',A,0)['message'])
        self.p.rows=[jac(B)]
        choices=self.update('home',A,0)['choices']
        self.update('home',A,0,choices[0]['value'])
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
        with self.assertRaises(Unavailable): self.update('peer',A,0)
        self.assertFalse(self.p.removals)
        self.assertEqual([t['hash'] for t in self.p.inventory('peer')],[A])
    def test_deferred_update_cleanup_survives_reload(self):
        self.scan();self.p.rows=[jac(B)];self.p.candidate=[file(3)]
        original=self.p.box_deletion;calls=[]
        def native(actor,body,check=False):
            calls.append(check)
            if check:return original(actor,dict(id=body['id'],confirmed=True))
            return dict(success=True,ticket='abc',scope='fixture',check_after=1060000)
        self.p.box_deletion=native
        with patch('worker.time.time',return_value=1000):
            r=self.update('box',A,0,actor='viewer')
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
        with self.assertRaises(Unavailable):self.update('peer',A,0)
        self.assertFalse(self.p.writes)
    def test_update_accepts_only_new_episode_from_multiseason_pack(self):
        for items in self.p.items.values():
            items[0]['files'].extend([dict(name='Show.S01E01.mkv',size=100),
                                      dict(name='Unknown episode.mkv',size=100)])
        self.scan();self.p.rows=[jac(B)];self.p.candidate=[file(3)]
        result=self.update('peer',A,2)
        self.assertEqual(result['hash'],B)
        self.assertEqual(result['ready'],[3])
        self.assertEqual(self.p.removals,[('peer',A)])
        self.assertEqual([row['hash'] for row in self.p.inventory('peer')],[B])

    def test_preview_pins_candidate_and_removal_set(self):
        self.scan();self.p.rows=[jac(B)]
        def request(token=''):
            return w.update_release(self.p,self.s,'peer',A,2,confirmation=token)
        preview=request()
        self.assertEqual(preview['offer']['versions'][0]['hash'],A)
        self.assertEqual(preview['offer']['ready'],[3])
        self.assertFalse(self.p.removals or self.p.writes)
        self.p.rows=[jac(C)]
        with self.assertRaisesRegex(Unavailable,'изменился'): request(preview['confirmation'])
        self.p.rows=[jac(B)]
        self.s['replaced']['peer:'+C]=A
        self.p.items['peer'].append(torrent(C,[file(1)]))
        with self.assertRaisesRegex(Unavailable,'изменился'): request(preview['confirmation'])
        self.assertFalse(self.p.removals or self.p.writes)

    def test_incomplete_destination_after_valid_preview(self):
        self.scan();self.p.rows=[jac(B)]
        self.p.candidate=[file(3),file(4)]
        self.p.ts_files=lambda *a,**kw:[file(3)]
        with self.assertRaises(Unavailable): self.update('peer',A,2)
        self.assertFalse(self.p.removals)

    def test_identity_choice_from_current_results_only(self):
        self.p.items['peer'][0]['tmdb']=None
        calendar=self.p.calendar
        self.p.calendar=lambda path,**kw: ({'results':[{'id':31,'name':'Show A'},{'id':32,'name':'Show B'}]}
                                           if path=='search/tv' else calendar(path,**kw))
        self.scan()
        c=next(c for c in w.catalog(self.p,self.s)['cards'] if c['place']=='peer')
        self.assertEqual(len(c['identities']),2)
        with self.assertRaises(Unavailable):w.choose_identity(self.p,self.s,'peer',A,33)
        w.choose_identity(self.p,self.s,'peer',A,32)
        self.assertEqual(w.state()['identities'][A],32)
        self.assertEqual(self.get()['tmdb'],32)

    def test_catalog_posters_for_external_add_and_replacement(self):
        record=dict(id=72,title='Arrival',original_title='Arrival',release_date='2016-01-01',
                    media_type='movie',poster_path='/arrival.jpg')
        t=dict(torrent(A,[]),tmdb=None,tv=False,title='',label='Arrival.2016.1080p')
        self.p.calendar=lambda *args,**kw:{'results':[record]}
        first=w.enriched(self.p,self.s,t,'home')
        self.assertEqual((first['tmdb'],first['poster']),(72,'/arrival.jpg'))
        w.remember_card(self.s,first,'home')
        self.p.calendar=lambda *args,**kw: self.fail('poster already cached')
        other=w.enriched(self.p,self.s,dict(t,hash=B,label='Arrival.2016.2160p'),'home')
        self.assertEqual(other['poster'],'/arrival.jpg')
        self.p.calendar=lambda *args,**kw:{'results':[record,dict(record,id=84)]}
        self.assertFalse(w.enriched(self.p,self.s,t,'peer')['poster'])
        self.assertEqual(w.poster_url('https://covers.example/art?id=4'),'https://covers.example/art?id=4')
        self.assertFalse(w.poster_url('javascript:fake'))

    def test_manual_choice_updates_title_type_and_poster(self):
        self.scan()
        self.p.calendar=lambda *a,**k:dict(id=55,title='Correct film',original_title='Original',
                                           poster_path='/right.jpg',release_date='2019-02-03')
        writes=[]
        self.p.write_film=lambda place,row,film,actor,manual:writes.append((place,film,manual))
        w.set_film(self.p,self.s,'peer',A,'movie',55,'actor')
        self.assertEqual(writes[0][1]['poster_path'],'/right.jpg')
        self.assertTrue(writes[0][2])
        self.assertEqual(self.s['catalog']['peer'][0]['title'],'Correct film')
        self.assertNotIn(w.item_id('peer',A,2),self.s['watches'])
        self.assertIn(w.item_id('home',A,2),self.s['watches'])

    def test_update_writes_poster_before_cleanup_in_all_libraries(self):
        for place in w.NAMES:
            with self.subTest(place=place):
                self.p=Sources()
                self.s['replaced']={}
                self.p.items[place][0]['poster']='/original.jpg'
                self.p.rows=[jac(B)]
                recorded=[]
                def write(site,row,film,actor,manual):
                    self.assertFalse(self.p.removals)
                    recorded.append((site,row['hash'],film['poster_path']))
                self.p.write_film=write
                self.update(place,A,2,actor='actor')
                self.assertEqual(recorded,[(place,B,'/original.jpg')])
                self.assertEqual(next(c for c in self.s['catalog'][place] if c['hash']==B)['poster'],'/original.jpg')
        self.p=Sources()
        self.s=w.state();self.s['replaced']={}
        self.p.rows=[jac(B)];self.p.items['box'][0]['poster']='/original.jpg'
        def fail(*args): raise Unavailable('metadata unavailable')
        self.p.write_film=fail
        with self.assertRaises(Unavailable): self.update('box',A,2,actor='actor')
        self.assertFalse(self.p.removals)

    def test_provider_schema_cannot_confirm_removal(self):
        p=Providers.__new__(Providers)
        p.memo={};p.cfg={'jacred':dict(base_url='https://jac.invalid',api_key='fixture')}
        for invalid in ({},None,'',{'unexpected':[]},[None],[{}]):
            p.ts=lambda *args:invalid
            self.s['deletions']={'peer:'+A:dict(id=None,due=0,until=0)}
            with self.assertRaises(Unavailable): w.verify_deletion(p,self.s,'peer',A)
            self.assertIn('peer:'+A,self.s['deletions'])
        with patch('providers.http',return_value=[]):
            with self.assertRaises(Unavailable): p.jac('Show','Show')

    def test_tmdb_and_torbox_reject_malformed_responses(self):
        p=Providers.__new__(Providers);p.memo={};p.cfg={'tmdb':'fixture'}
        with patch('providers.http',return_value={'episodes':{}}):
            with self.assertRaises(Unavailable): p.calendar('tv/1/season/1')
        with patch.object(p,'json_file',return_value={'api_key':'fixture'}),patch('providers.http',return_value=[]):
            with self.assertRaises(Unavailable): p.box('mylist')

    def test_archived_versions_removed_without_requesting_their_files(self):
        self.scan();self.p.rows=[jac(B)]
        self.s['replaced']['peer:'+C]=A
        self.p.items['peer'].append(torrent(C,[]))
        original=self.p.ts_files
        def get_files(place,h,**kw):
            self.assertNotEqual(h,C)
            return original(place,h,**kw)
        self.p.ts_files=get_files
        self.assertEqual(self.update('peer',A,2)['hash'],B)
        self.assertEqual(self.p.removals,[('peer',C),('peer',A)])

    def test_old_cleanup_receipt_ignores_old_episode_list(self):
        self.p.items['peer'].append(torrent(B,[file(3)]))
        self.s['replaced']={'peer:'+A:B}
        key=w.stage_group(self.s,'peer',B,self.p.items['peer'],'viewer',keep=B)
        job=self.s['removal_groups'][key]
        del job['ready'],job['season']
        job.update(required=[[2,1],[2,2],[2,3]],hint=2)
        w.save(self.s)
        self.assertTrue(w.check_removal(self.p,w.state(),'peer',B)['confirmed'])
        self.assertEqual(self.p.removals,[('peer',A)])

    def test_stale_data_and_shared_hash_pruning(self):
        self.p.rows=[jac(B)]
        self.scan();self.p.box_down=True; self.scan()
        self.assertEqual(self.get('box')['ready'],[3])
        self.assertTrue(self.get('box')['stale'])
        self.assertGreater(self.get('box')['asof'],0)
        self.s['identities'][A]=19
        self.p.items['peer']=[];self.p.items['home']=[]
        self.scan()
        self.assertIn(A,self.s['files'])
        self.p.box_down=False;self.scan()
        self.assertIn(A,self.s['identities'])
        self.p.items['box']=[];self.scan()
        self.assertNotIn(A,self.s['files']);self.assertNotIn(A,self.s['identities'])
        self.assertFalse(self.s['sources'])
if __name__=='__main__':unittest.main()
