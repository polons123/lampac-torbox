import datetime as dt
import fcntl
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from providers import HASH, NAMES, Providers, Unavailable, digest, episode_files, season_hint

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

def same_files(old, new):
    # Keep every previously playable variant. A re-encode or ambiguous duplicate requires review.
    return bool(old) and all(ep in new and sorted(files)==sorted(new[ep]) for ep,files in old.items())

def full_preserved(old, new):
    return bool(old) and all(same_files(eps,new.get(season,{})) for season,eps in old.items())

def file_signature(name):
    name=re.sub(r'(?<!\d)(2160|1080|720|480)[pр](?![a-z0-9])',r'\1',name,flags=re.I)
    return re.sub(r'(s\d{1,2}[ ._-]*e)\d{1,3}',r'\1#',name.casefold(),flags=re.I)

def verified_additions(old, new, row, previous, label):
    if not same_files(old,new):
        return []
    old_height=height(label)
    if old_height and (height(row['label']) or row['height'])!=old_height:
        return []
    if previous:
        if previous.get('height') and row['height']!=previous['height']:
            return []
        if not set(previous.get('voices',[])).issubset(row['voices']) or not set(previous.get('audio',[])).issubset(row['audio']):
            return []
    if not row['audio'] and not row['voices']:
        return []
    patterns={file_signature(name) for files in old.values() for name,_ in files}
    result=[]
    for ep in set(new)-set(old):
        if len(new[ep])==1 and file_signature(new[ep][0][0]) in patterns:
            result.append(int(ep))
    return sorted(result)

def item_id(place,h,season):
    return digest(f'{place}/{h}/{season}')[:32]

def optional_label(error):
    return str(error) if isinstance(error,Unavailable) else 'Источник временно недоступен'

def examine(p,st,t,w,all_files,old):
    place,h,season=w['place'],w['hash'],w['season']
    existing={int(n) for n in old}
    missing=set()
    show={}
    complete=False
    try:
        calendar=p.calendar(f"tv/{w['tmdb']}/season/{season}")
        eps=calendar['episodes']
        missing={int(e['episode_number']) for e in eps if e.get('air_date') and e['air_date']<=TODAY()}-existing
        w['waiting']=sorted(missing)
        if eps and all(e.get('air_date') and e['air_date']<=TODAY() and int(e['episode_number']) in existing for e in eps):
            complete=True
            w['detail']='Все серии из календаря уже есть'
            show=p.calendar(f"tv/{w['tmdb']}")
            later=(show.get('last_episode_to_air') or {}).get('season_number') or 0
            if show.get('status') in ('Ended','Canceled') or later>season: return
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
    if choices:
        known_height=height(t['label'])
        picks={}
        for r in choices:
            if known_height and (height(r['label']) or r['height']) not in (0,known_height):
                continue
            picks[r['page']]=dict(value=r['page'],label=r['label']+' · '+r['tracker'])
        w['choices']=[v for k,v in picks.items() if not binding or binding['page']!=k]
    if not binding:
        if not complete:
            w['detail']='Выберите исходную раздачу'
        return
    matches={r['hash']:r for r in choices if r['page']==binding['page']}
    if len(matches)!=1:
        w['detail']='Исходная страница отсутствует или содержит несколько версий'
        return
    cand=next(iter(matches.values()))
    if cand['hash']==h:
        return
    new=episode_files(p.candidate_files(place,cand['hash']),season)
    if not full_preserved(all_files,new):
        w['detail']='Изменился состав старых файлов. Требуется ручная проверка'
        return
    ready=verified_additions(old,new.get(season,{}),cand,binding,t['label'])
    if not ready:
        w['detail']='Новая версия без подтверждённых дополнительных серий в том же варианте'
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
                examine(p,st,t,w,{int(s):e for s,e in known.items()},old)
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
        item={k:v for k,v in w.items() if k not in ('hash','candidate','new_label')}
        item['revision']=revision(w)
        public.append(item)
    atomic(ROOT/'report.json',dict(items=public,checked=st['checked'],last=st.get('last',0)))

def revision(w):
    return digest(json.dumps([w['hash'],w.get('candidate'),w['ready'],w['choices'],w['identities']],sort_keys=True))[:32]

def apply(p,st,w,t):
    h=w['candidate']
    place=w['place']
    if place=='box':
        if not any(o['hash']==h for o in p.inventory('box')):
            p.box('createtorrent',form=dict(magnet='magnet:?xt=urn:btih:'+h,add_only_if_cached='true',as_queued='false',allow_zip='false',seed='1'))
    else:
        already_present=any(o['hash']==h for o in p.inventory(place))
        # The destination resolves the magnet before the persistent save.
        checked=episode_files(p.ts_files(place,h),w['season'])
        baseline={int(s):e for s,e in st['files'][t['hash']].items()}
        if not full_preserved(baseline,checked) or not all(str(n) in checked.get(w['season'],{}) for n in w['ready']):
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
            actual=episode_files(row['files'] or p.ts_files(place,h),w['season'])
            if not full_preserved({int(s):ep for s,ep in st['files'][t['hash']].items()},actual):
                raise Unavailable('Сохранённая версия не прошла повторную проверку файлов')
            if all(str(n) in actual.get(w['season'],{}) for n in w['ready']):
                return row
        time.sleep(2)
    raise Unavailable('Источник ещё не подтвердил сохранение. Старая версия доступна')

def action(p,st,cmd,key,rev,value):
    w=st['watches'].get(key)
    if not w or revision(w)!=rev:
        raise Unavailable('Список изменился. Откройте его заново')
    inventory=p.inventory(w['place'])
    t=next((a for a in inventory if a['hash']==w['hash']),None)
    if not t:
        raise Unavailable('Исходная раздача больше не сохранена')
    if cmd=='identity' and any(c['value']==value for c in w['identities']):
        st['identities'][w['hash']]=int(value)
    elif cmd=='bind' and any(c['value']==value for c in w['choices']):
        show=p.calendar(f"tv/{w['tmdb']}")
        rows=p.jac(t['title'] or show['name'],t['original'] or show.get('original_name'))
        matches={r['hash']:r for r in rows if r['page']==value}
        if len(matches)!=1: raise Unavailable('Страница изменилась. Повторите проверку')
        st['sources'][key]=next(iter(matches.values()))
    elif cmd=='apply' and w.get('candidate') and w['ready']:
        checked=next((r for r in item_rows(p,st,t,w['place'],force=True) if r['id']==key),{})
        if checked.get('candidate')!=w['candidate'] or checked.get('ready')!=w['ready']:
            raise Unavailable('Состав или доступность обновления изменились')
        row=apply(p,st,checked,t)
        # Keep the old provider object for existing players and Infuse links.
        st['replaced'][w['place']+':'+w['hash']]=row['hash']
        st['identities'][row['hash']]=w['tmdb']
        for prev in list(st['watches'].values()):
            if prev['place']==w['place'] and prev['hash']==w['hash']:
                oldbinding=st['sources'].get(prev['id'])
                if oldbinding: st['sources'][item_id(w['place'],row['hash'],prev['season'])]=oldbinding
                st['watches'].pop(prev['id'])
        t=row
        t.update(tmdb=w['tmdb'],tv=True,title=w['title'])
    else:
        raise Unavailable('Действие уже недоступно')
    for new in item_rows(p,st,t,w['place']):
        st['watches'][new['id']]=new
    save(st)

def main():
    os.umask(0o077)
    with (ROOT/'worker.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX if sys.argv[1:] == ['scan'] else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise Unavailable('Идёт проверка сезонов. Попробуйте немного позже')
        p,st=Providers(),state()
        if sys.argv[1:] == ['scan']:
            scan(p,st)
            save(st)
            print(json.dumps(dict(inventory=st['totals'],seasons=len(st['watches']),checked=st['checked'])))
        elif len(sys.argv)==5:
            action(p,st,*sys.argv[1:])
        else:
            raise Unavailable('Некорректная команда')
if __name__=='__main__':
    try: main()
    except ERRORS as exc:
        print(optional_label(exc))
        sys.exit(1)
