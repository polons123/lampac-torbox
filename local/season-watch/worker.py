import datetime as dt
import fcntl
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from providers import HASH, NAMES, VIDEO, Providers, Unavailable, digest, episode_files, season_hint
from urllib.parse import quote, urlsplit
from concurrent.futures import ThreadPoolExecutor

ROOT = Path('/var/lib/season-watch')
STATE = ROOT / 'state.json'
TODAY = lambda: dt.datetime.now(dt.timezone.utc).date().isoformat()
ERRORS = (Unavailable, OSError, ValueError, TypeError, KeyError)

def atomic(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, separators=(',', ':'))
            f.flush()
            os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)

def state():
    if not STATE.exists():
        return dict(watches={}, files={}, replaced={}, sources={}, identities={}, checked={}, totals={})
    data = json.loads(STATE.read_text())
    if data.get('parser_version') != 2:
        data['files'] = {}
    return data

def display_name(t):
    text = re.sub(r'^\[LAMPA\]\s*','',t['label'])
    return t['title'] or re.split(r'\s*/\s*|\s*[\[(]|\bS\d{1,2}\b|\b(?:Season|сезон|WEBRip|WEB-DL)\b',text,flags=re.I)[0].replace('.',' ').strip(' -!') or text[:90]

def normalize(s):
    return re.sub(r'[^а-яa-z0-9]','',str(s).casefold().replace('ё','е'))

def identity(p, t, st):
    h=t['hash']
    if t['tmdb'] and t['tv']:
        return int(t['tmdb']), []
    if h in st['identities']:
        return st['identities'][h], []
    name=display_name(t)
    results=p.calendar('search/tv',query=name).get('results',[])
    year=re.search(r'\b(19\d{2}|20\d{2})\b',t['label'])
    exact=[r for r in results if normalize(name) in {normalize(r.get('name')),normalize(r.get('original_name'))}
           and year and (r.get('first_air_date') or '').startswith(year.group(1))]
    if len(exact)==1:
        st['identities'][h]=int(exact[0]['id'])
        return st['identities'][h], []
    return None,[dict(value=str(r['id']),label=f"{r['name']} · {(r.get('first_air_date') or '?')[:4]}") for r in results[:15]]

def height(name):
    match = re.search(r'(?<!\d)(2160|1080|720|480)[pрi]?\b', name, re.I)
    if match:
        return int(match.group(1))
    return 2160 if re.search(r'\b4k\b',name,re.I) else 0

def additions(old,new):
    return sorted({int(ep) for ep in new} - {int(ep) for ep in old})

def edition_tags(label):
    # Release format and resolution identify an edition when a tracker changed the info hash.
    return set(re.findall(r'web[- .]?dlrip|web[- .]?dl|webrip|bdremux|bdrip|blu[- .]?ray|hevc|avc|sdr|hdr',label.casefold()))

def edition(rows,t):
    names={normalize(t['title']),normalize(t['original'])}-{''}
    if not names: return None
    known_height=height(t['label'])
    if not known_height: return None
    tags=edition_tags(t['label'])
    matches=[r for r in rows if names & {normalize(r.get('title')),normalize(r.get('original'))}
             and (height(r['label']) or r['height'])==known_height and edition_tags(r['label'])==tags]
    return matches[0] if len({(r['hash'],r['page']) for r in matches})==1 else None

def choice_id(row):
    return digest(row['page']+'|'+row['hash'])

def item_id(place,h,season):
    return digest(f'{place}/{h}/{season}')[:32]

def optional_label(error):
    return str(error) if isinstance(error,Unavailable) else 'Источник временно недоступен'

def examine(p,st,t,w,old,interactive=False,selection=''):
    place,h,season=w['place'],w['hash'],w['season']
    existing={int(n) for n in old}
    missing=set()
    show={}
    complete=False
    try:
        if not w.get('tmdb'): raise Unavailable('Нет календаря')
        calendar=p.calendar(f"tv/{w['tmdb']}/season/{season}")
        eps=calendar['episodes']
        missing={int(e['episode_number']) for e in eps if e.get('air_date') and e['air_date']<=TODAY()}-existing
        w['waiting']=sorted(missing)
        if eps and all(e.get('air_date') and e['air_date']<=TODAY() and int(e['episode_number']) in existing for e in eps):
            complete=True
            w['detail']='Все серии из календаря уже есть'
            show=p.calendar(f"tv/{w['tmdb']}")
            later=(show.get('last_episode_to_air') or {}).get('season_number') or 0
            if not interactive and (show.get('status') in ('Ended','Canceled') or later>season): return
    except ERRORS:
        w['detail']='Календарь временно недоступен'
    if not t['title'] and not show:
        show=p.calendar(f"tv/{w['tmdb']}")
    w['title']=t['title'] or show.get('name') or w['title']
    original=t['original'] or show.get('original_name') or w['title']
    rows=p.jac(w['title'],original)
    choices=[r for r in rows if not r['seasons'] or season in r['seasons']]
    key=w['id']
    binding=st['sources'].get(key)
    if not binding:
        exact={r['page']:r for r in choices if r['hash']==h}
        if len(exact)==1:
            binding=next(iter(exact.values()))
            st['sources'][key]=binding
    known_height=height(t['label'])
    relevant=[r for r in choices if not known_height or (height(r['label']) or r['height']) in (0,known_height)]
    unique={choice_id(r):r for r in relevant}
    w['choices']=[dict(value=k,label=r['label']+' · '+r['tracker']) for k,r in unique.items()]
    if selection:
        if selection not in unique: raise Unavailable('Вариант изменился. Проверьте раздачу ещё раз')
        binding=unique[selection]
        st['sources'][key]=binding
    if binding:
        matches={r['hash']:r for r in choices if r['page']==binding['page']}
        cand=unique[selection] if selection else next(iter(matches.values())) if len(matches)==1 else None
    else:
        cand=edition(relevant,t)
    if not cand:
        w['detail']='Выберите версию раздачи' if relevant else 'Подходящая раздача пока не найдена'
        return
    w['choices']=[]
    w['source']=cand
    if cand['hash']==h:
        return
    new=episode_files(p.candidate_files(place,cand['hash']),season)
    ready=additions(old,new.get(season,{}))
    if not ready:
        w['detail']='Новых серий пока нет'
        return
    w.update(ready=ready,waiting=sorted(missing-set(ready)),candidate=cand['hash'],new_label=cand['label'],detail='')

def item_rows(p,st,t,place,force=False):
    h=t['hash']
    if not HASH.fullmatch(h) or t['movie'] or place+':'+h in st['replaced']:
        return []
    hint=season_hint(t['label'])
    known=st['files'].get(h)
    if not t['tv'] and not hint and not re.search(r'сезон|s\d{1,2}e\d|Season',t['label'],re.I) and not episode_files(t['files']):
        return []
    if not known or force:
        raw=t['files'] or (p.ts_files(place,h) if place!='box' else [])
        known=episode_files(raw,hint)
        if known:
            st['files'][h]=known
    if not known:
        return [make_row(t,place,hint or 0,'Не удалось определить серии по файлам')]
    tmdb, ids=identity(p,t,st)
    out=[]
    for season,old in sorted(known.items(),key=lambda i:int(i[0])):
        w=make_row(t,place,int(season),'')
        w.update(tmdb=tmdb,owned=sorted(map(int,old)))
        if not tmdb:
            w.update(detail='Уточните сериал в TMDB',identities=ids)
        else:
            try:
                examine(p,st,t,w,old)
            except ERRORS as exc:
                w['detail']=optional_label(exc)
        out.append(w)
    return out

def make_row(t,place,season,detail):
    return dict(id=item_id(place,t['hash'],season),place=place,hash=t['hash'],title=display_name(t),
        label=t['label'][:360],season=season,ready=[],waiting=[],owned=[],choices=[],identities=[],detail=detail)

def invalidated(row, reason):
    row.update(ready=[],choices=[],identities=[],detail=reason)
    row.pop('candidate',None)

def scan(p,st):
    for key,receipt in list(st.get('deletions',{}).items()):
        if receipt['due']<=time.time():
            try: verify_deletion(p,st,*key.split(':',1))
            except ERRORS: pass
    for place in NAMES:
        now=int(time.time())
        try:
            inventory=p.inventory(place)
        except ERRORS as err:
            st['checked'][place]={'error':optional_label(err),'time':now}
            for w in st['watches'].values():
                if w['place']==place: invalidated(w,'Источник недоступен. Сохранён предыдущий календарь')
            continue
        available={t['hash'] for t in inventory}
        for retired, replacement in list(st['replaced'].items()):
            site,old=retired.split(':',1)
            if site==place and old in available and replacement not in available:
                del st['replaced'][retired]
        st.setdefault('catalog',{})[place]=[card(t,place) for t in inventory]
        st['totals'][place]=len(inventory)
        fresh={}
        for t in inventory:
            try:
                rows=item_rows(p,st,t,place)
            except ERRORS as err:
                rows=[r.copy() for r in st['watches'].values() if r['place']==place and r['hash']==t['hash']]
                if not rows:
                    rows=[make_row(t,place,season_hint(t['label']) or 0,'')]
                for r in rows: invalidated(r,optional_label(err))
            for w in rows: fresh[w['id']]=w
        st['watches']={k:v for k,v in st['watches'].items() if v['place']!=place}
        st['watches'].update(fresh)
        st['checked'][place]={'time':now}
        save(st)
    st['last']=int(time.time())

def save(st):
    # Convert season keys to a common on-disk representation on every write.
    st['parser_version']=2
    atomic(STATE,st)
    public=[]
    for w in st['watches'].values():
        item={k:v for k,v in w.items() if k not in ('hash','candidate','new_label','source')}
        item['revision']=revision(w)
        public.append(item)
    atomic(ROOT/'report.json',dict(items=public,checked=st['checked'],last=st.get('last',0)))

def revision(w):
    return digest(json.dumps([w['hash'],w.get('candidate'),w['ready'],w['choices'],w['identities']],sort_keys=True))[:32]

def destination_has(files,w):
    if not w['season']:
        return any(VIDEO.search(f.get('path') or f.get('name') or '') for f in files)
    episodes=episode_files(files,w['season']).get(w['season'],{})
    return bool(w['ready']) and all(str(n) in episodes for n in w['ready'])

def apply(p,st,w,t):
    h=w['candidate']
    place=w['place']
    if place=='box':
        if not any(o['hash']==h for o in p.inventory('box')):
            p.box('createtorrent',form=dict(magnet='magnet:?xt=urn:btih:'+h,add_only_if_cached='true',as_queued='false',allow_zip='false',seed='1'))
    else:
        already_present=any(o['hash']==h for o in p.inventory(place))
        # The destination resolves the magnet before the persistent save.
        if not destination_has(p.ts_files(place,h),w):
            raise Unavailable('Целевой TorrServer не подтвердил файлы')
        if not already_present:
            raw=t['raw']
            source_data=raw.get('data') or ''
            try:
                info=json.loads(source_data) if source_data else {}
                if 'TorrServer' in info:
                    # The web UI records hash-specific file metadata here.
                    info.pop('TorrServer')
                    source_data=json.dumps(info,ensure_ascii=False)
            except (TypeError,ValueError):
                source_data=''
            p.ts(place,'set',hash=h,title=w.get('new_label') or st['sources'][w['id']]['label'],
                 poster=raw.get('poster') or '',category=raw.get('category') or '',data=source_data)
        p.ts_files(place,h,save=True)
    for _ in range(4):
        inventory=p.inventory(place)
        row=next((a for a in inventory if a['hash']==h),None)
        if row and (place!='box' or row['raw'].get('download_present')):
            files=row['files'] or (p.ts_files(place,h) if place!='box' else [])
            if destination_has(files,w):
                return row
        time.sleep(2)
    raise Unavailable('Источник ещё не подтвердил сохранение. Старая версия доступна')

def card(t,place):
    poster=t.get('poster') or ''
    image=re.search(r'/([a-zA-Z0-9_-]+\.(?:jpg|png|webp))$',urlsplit(poster).path)
    return dict(place=place,hash=t['hash'],title=t['title'] or display_name(t),label=t['label'][:400],
        poster='/'+image.group(1) if image else '',year=t.get('year',''),movie=t['movie'],
        box_id=t['raw'].get('id') if place=='box' else None)

def catalog(p,st):
    cards=[]
    errors={}
    with ThreadPoolExecutor(max_workers=len(NAMES)) as pool:
        jobs={place:pool.submit(p.inventory,place) for place in NAMES}
        for place,job in jobs.items():
            try:
                items=[card(t,place) for t in job.result()]
            except ERRORS as exc:
                errors[place]=optional_label(exc)
                items=st.get('catalog',{}).get(place,[])
            present={a['hash'] for a in items}
            for data in items:
                h=data['hash']
                if st['replaced'].get(place+':'+h) in present: continue
                data=dict(data)
                watches=[w for w in st['watches'].values() if w['place']==place and w['hash']==h]
                data['seasons']=sorted([dict(n=w['season'],id=w['id'],ready=w['ready'],waiting=w['waiting'],tmdb=w.get('tmdb')) for w in watches if w['season']],key=lambda x:x['n'])
                cards.append(data)
    return dict(cards=cards,errors=errors,pending=[dict(place=k.split(':')[0],hash=k.split(':')[1],due=r['due'])
        for k,r in st.get('deletions',{}).items()])

def existing(p,place,h):
    if place not in NAMES or not HASH.fullmatch(h):
        raise Unavailable('Некорректная раздача')
    t=next((t for t in p.inventory(place) if t['hash']==h),None)
    if not t: raise Unavailable('Раздача больше не находится в этой библиотеке')
    return t

def media(p,place,h):
    t=existing(p,place,h)
    if place=='box' and not t['raw'].get('download_present'):
        raise Unavailable('Раздача ещё загружается в TorBox')
    files=t['files'] or (p.ts_files(place,h) if place!='box' else [])
    rows=[]
    for f in files:
        name=f.get('path') or f.get('name') or ''
        if not VIDEO.search(name) or f.get('id') is None: continue
        idx=int(f['id'])
        row=dict(name=name.rsplit('/',1)[-1],path=name,id=idx)
        if place!='box':
            row['url']=p.cfg['ts'][place]['url']+'/stream/'+quote(row['name'],safe='')+'?link='+h+'&index='+str(idx)+'&play'
        rows.append(row)
    return dict(files=rows,box_id=t['raw'].get('id') if place=='box' else None)

def movie_offer(p,st,t,w,selection):
    rows=p.jac(w['title'],t['original'],movie=True,year=int(t.get('year') or 0))
    exact=[r for r in rows if r['hash']==t['hash']]
    saved=st['sources'].get(w['id'])
    if not saved and exact:
        saved=exact[0]
        st['sources'][w['id']]=saved
    choices={choice_id(r):r for r in rows}
    if selection:
        if selection not in choices: raise Unavailable('Выбранная версия устарела')
        saved=choices[selection]
        st['sources'][w['id']]=saved
    candidates={r['hash']:r for r in rows if saved and r['page']==saved['page']}
    if not choices:
        w['detail']='Исходная раздача пока не найдена'
        return
    if len(candidates)!=1:
        w['choices']=[dict(value=k,label=r['label']+' · '+r['tracker']) for k,r in choices.items()]
        return
    new=next(iter(candidates.values()))
    if new['hash']!=t['hash']:
        files=p.candidate_files(w['place'],new['hash'])
        if not any(VIDEO.search(f.get('path') or f.get('name') or '') for f in files):
            raise Unavailable('В новой версии нет видеофайлов')
        w.update(candidate=new['hash'],new_label=new['label'],source=new)

def pending_status(receipt):
    return dict(pending=True,due=receipt['due'],message='Ожидается подтверждение удаления')

def verify_deletion(p,st,place,h):
    key=place+':'+h
    receipt=st.get('deletions',{}).get(key)
    if not receipt:
        return dict(pending=False,message='Список раздач обновлён')
    now=int(time.time())
    if now<receipt['due']: return pending_status(receipt)
    if receipt.get('ticket'):
        try:
            reply=p.box_deletion(receipt['actor'],dict(ticket=receipt['ticket'],scope=receipt['scope'],id=receipt['id']),check=True)
            if reply.get('pending'):
                receipt['due']=max(now+5,int(reply.get('check_after',0))//1000)
                save(st)
                return pending_status(receipt)
        except ERRORS:
            # A missing ticket or expired Lampac session can still be reconciled from live inventory.
            pass
    inventory=p.inventory(place)
    same=[t for t in inventory if t['hash']==h]
    still_present=any(t['raw'].get('id')==receipt['id'] for t in same) if place=='box' else bool(same)
    if still_present and now<receipt['until']:
        receipt['due']=now+5
        save(st)
        return pending_status(receipt)
    del st['deletions'][key]
    if still_present:
        save(st)
        return dict(pending=False,confirmed=False,message='Источник пока показывает раздачу. Удаление не подтверждено')
    st.setdefault('catalog',{})[place]=[card(t,place) for t in inventory]
    st['totals'][place]=len(inventory)
    if not same:
        for wid,w in list(st['watches'].items()):
            if w['place']==place and w['hash']==h:
                del st['watches'][wid]
                st['sources'].pop(wid,None)
        st['sources'].pop(item_id(place,h,0),None)
        st['replaced'].pop(key,None)
    save(st)
    return dict(pending=False,confirmed=True,message='Раздача удалена · '+NAMES[place])

def remove_release(p,st,place,h,expected_id,actor):
    key=place+':'+h
    if key in st.get('deletions',{}):
        return verify_deletion(p,st,place,h)
    t=existing(p,place,h)
    if place=='box' and (type(expected_id) is not int or t['raw'].get('id')!=expected_id):
        raise Unavailable('Карточка TorBox изменилась. Откройте её заново')
    now=int(time.time())
    receipt=dict(id=expected_id,due=now,until=now+120)
    st.setdefault('deletions',{})[key]=receipt
    # A retry after a network failure must only verify the first request.
    save(st)
    try:
        if place=='box':
            reply=p.box_deletion(actor,dict(id=expected_id,confirmed=True))
            if reply.get('ticket') and reply.get('scope'):
                receipt.update(actor=actor,ticket=reply['ticket'],scope=reply['scope'],
                               due=max(now,int(reply.get('check_after',0))//1000))
                save(st)
        else:
            p.ts(place,'rem',hash=h)
    except ERRORS:
        pass
    try: return verify_deletion(p,st,place,h)
    except ERRORS:
        receipt['due']=max(int(time.time())+5,receipt['due'])
        save(st)
        return pending_status(receipt)

def update_release(p,st,place,h,season,selection='',preview=False):
    if place+':'+h in st.get('deletions',{}): raise Unavailable('Проверяется удаление этой раздачи')
    t=existing(p,place,h)
    if st['replaced'].get(place+':'+h) and any(a['hash']==st['replaced'][place+':'+h] for a in p.inventory(place)):
        return dict(message='Эта раздача уже обновлена')
    known={} if t['movie'] else (st['files'].get(h) or {})
    if not t['movie'] and not known:
        known=episode_files(t['files'] or (p.ts_files(place,h) if place!='box' else []),season_hint(t['label']))
        if known: st['files'][h]=known
    if not season and len(known)>1:
        return dict(seasons=sorted(map(int,known),reverse=True))
    if not season and known: season=int(next(iter(known)))
    w=make_row(t,place,season,'')
    if known:
        old=known.get(str(season)) or known.get(season)
        if not old: raise Unavailable('Такого сезона нет в этой раздаче')
        tmdb=t.get('tmdb') or st['identities'].get(h) or next((r.get('tmdb') for r in st['watches'].values() if r['hash']==h and r.get('tmdb')),None)
        if not tmdb:
            try: tmdb,_=identity(p,t,st)
            except ERRORS: pass
        w.update(tmdb=tmdb,owned=sorted(map(int,old)))
        # Interactive search also works from the stored release title without TMDB metadata.
        if not tmdb and not t['title']: t=dict(t,title=display_name(t))
        examine(p,st,t,w,old,interactive=True,selection=selection)
        st['watches'][w['id']]=w
    elif t['tv']:
        raise Unavailable('Пока не удалось распознать номера серий')
    else:
        movie_offer(p,st,t,w,selection)
    if not preview: save(st)
    if w['choices']: return dict(choices=w['choices'])
    if not w.get('candidate'):
        return dict(message=w['detail'] or ('Новых серий пока нет' if known else 'Раздача актуальна'))
    if preview: return dict(candidate=w['candidate'],ready=w['ready'])
    new=apply(p,st,w,t)
    newh=new['hash']
    st['replaced'][place+':'+h]=newh
    if w.get('tmdb'):st['identities'][newh]=w['tmdb']
    for oldrow in list(st['watches'].values()):
        if oldrow['place']==place and oldrow['hash']==h:
            bound=st['sources'].get(oldrow['id'])
            if bound: st['sources'][item_id(place,newh,oldrow['season'])]=bound
            del st['watches'][oldrow['id']]
    st['sources'][item_id(place,newh,season)]=w['source']
    save(st)
    return dict(message='Раздача обновлена',hash=newh,ready=w['ready'])

def main():
    os.umask(0o077)
    args=sys.argv[1:]
    p=Providers()
    if args==['catalog']:
        result=catalog(p,state())
    elif len(args)==3 and args[0]=='media':
        result=media(p,*args[1:])
    else:
        with (ROOT/'worker.lock').open('a') as lock:
            try: fcntl.flock(lock,fcntl.LOCK_EX if args==['scan'] else fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise Unavailable('Идёт проверка раздач. Повторите через минуту')
            st=state()
            if args==['scan']:
                scan(p,st)
                save(st)
                result=dict(inventory=st['totals'],seasons=len(st['watches']))
            elif len(args)==4 and args[0]=='remove':
                result=remove_release(p,st,args[1],args[2],int(args[3]) if args[3] else None,os.environ['SEASON_WATCH_ACTOR'])
            elif len(args)==3 and args[0]=='verify-remove':
                result=verify_deletion(p,st,args[1],args[2])
            elif len(args)==5 and args[0]=='update':
                result=update_release(p,st,args[1],args[2],int(args[3]),args[4])
            else: raise Unavailable('Некорректная команда')
    print(json.dumps(result,ensure_ascii=False,separators=(',',':')))
if __name__=='__main__':
    try: main()
    except ERRORS as exc:
        print(optional_label(exc))
        sys.exit(1)
