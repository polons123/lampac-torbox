import hashlib
import json
import re
import uuid
import sys
from pathlib import Path
from urllib import parse, request
from urllib.error import HTTPError

APP = Path('/opt/lampac')
SETTINGS = Path('/etc/season-watch/config.json')
NAMES = {'home': 'Домашний TorrServer', 'peer': 'TorrServer h2nexus-p', 'box': 'TorBox'}
VIDEO = re.compile(r'\.(?:mkv|mp4|avi|m4v|ts|m2ts|mov|webm|mpg|mpeg|wmv)$', re.I)
SE = re.compile(r'(?<![a-z0-9])s(\d{1,2})[ ._-]*e(\d{1,3})(?!\d)', re.I)
EX = re.compile(r'(?<!\d)(\d{1,2})x(\d{1,3})(?!\d)', re.I)
SEASON = re.compile(r'(?:\bseason|\bсезон|\bs)[ ._:-]*(\d{1,2})\b|\b(\d{1,2})[ ._-]*сезон', re.I)
EP = re.compile(r'(?:^|[^a-zа-я0-9])(?:ep?(?:isode)?|серия)[ ._-]*(\d{1,3})(?!\d)', re.I)
HASH = re.compile(r'^[0-9a-f]{40}$')
BAD = re.compile(r'(?:^|[/ ._\-])(sample|trailer|extras?|трейлер|образец)(?:[/ ._\-]|$)', re.I)

class Unavailable(Exception):
    pass

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None

opener = request.build_opener(NoRedirect())

def http(url, headers=None, body=None, timeout=18):
    headers = {'User-Agent': 'SeasonWatch/1.0', **(headers or {})}
    if isinstance(body, dict):
        headers['Content-Type'] = 'application/json'
        body = json.dumps(body).encode()
    try:
        with opener.open(request.Request(url, headers=headers, data=body), timeout=timeout) as response:
            data = response.read(24 * 1024 * 1024 + 1)
            if len(data) > 24 * 1024 * 1024:
                raise Unavailable('Слишком большой ответ')
            return json.loads(data) if data else None
    except HTTPError as exc:
        raise Unavailable('HTTP ' + str(exc.code)) from None
    except (OSError, ValueError):
        raise Unavailable('Ошибка соединения или формата ответа') from None

def checked(value, kind, operation):
    if not isinstance(value, kind):
        print(f'{operation}: unexpected {type(value).__name__}', file=sys.stderr)
        raise Unavailable('Некорректные данные источника · '+operation)
    return value

def records(value, operation):
    rows=checked(value,list,operation)
    for row in rows: checked(row,dict,operation)
    return rows

def file_list(value, operation):
    rows=records(value,operation)
    for row in rows:
        checked(row.get('path') or row.get('name'),str,operation)
        size=row.get('length') or row.get('size') or 0
        if not isinstance(size,(int,str)) or not str(size).isdecimal():
            raise Unavailable('Некорректный размер файла · '+operation)
    return rows

def valid_hash(value, operation):
    if not isinstance(value,str) or not HASH.fullmatch(value.lower()):
        raise Unavailable('Некорректный хеш · '+operation)
    return value.lower()

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

ORDINALS = ('первый','второй','третий','четвертый','пятый','шестой','седьмой','восьмой','девятый','десятый')
WORD_SEASON = re.compile(r'\b('+'|'.join(ORDINALS)+r')[ ._-]+сезон\b',re.I)

def season_hint(name):
    for part in reversed(re.split(r'[/\\]',name)):
        m = SEASON.search(part)
        if m: return int(m.group(1) or m.group(2))
        m = WORD_SEASON.search(part.lower().replace('ё','е'))
        if m: return ORDINALS.index(m.group(1))+1
    return None

def file_pair(leaf):
    explicit = SE.search(leaf)
    if explicit: return explicit
    for match in EX.finditer(leaf):
        if match[0].lower()=='2x2' and re.search('[а-яё]',leaf,re.I): continue
        return match
    return None

# An explicit endpoint can repeat the season (S02E03-S02E04, 2x03-2x04).
PAIR_END = re.compile(r'(?:[ ._+-]*(?:s(\d{1,2})[ ._-]*)?e|[ ._+-]+(\d{1,2})x|[+-]\s*)(\d{1,3})(?=$|[. _+()\[\]-]|e\d)',re.I)

def explicit_episodes(leaf,match):
    season,first=map(int,match.groups())
    tail=leaf[match.end():]
    end=PAIR_END.match(tail)
    if not end:return season,[first]
    last=int(end[3])
    if int(end[1] or end[2] or season)!=season or last!=first+1 or PAIR_END.match(tail[end.end():]):
        return season,[]
    return season,[first,last]

def episode_files(files, hint=None):
    seasons = {}
    for f in files:
        name = str(f.get('path') or f.get('name') or '')
        size = int(f.get('length') or f.get('size') or 0)
        if not VIDEO.search(name) or BAD.search(name) or size <= 0:
            continue
        leaf = re.split(r'[/\\]',name)[-1]
        m = file_pair(leaf)
        if m:
            season,episodes=explicit_episodes(leaf,m)
        else:
            epmatch = EP.search(leaf) or re.match(r'^(\d{1,3})[ ._-]+', leaf)
            season = season_hint(name) or hint
            if not season or not epmatch:
                continue
            episodes=[int(epmatch.group(1))]
        if season>0 and episodes and min(episodes)>0:
            for ep in episodes:
                seasons.setdefault(season, {}).setdefault(str(ep), []).append([leaf, size])
    return seasons

def measured_tags(streams):
    video = next((s for s in streams if s.get('codec_type')=='video' and s.get('height') and (s.get('disposition') or {}).get('attached_pic') not in (1,'1')), {})
    audio = [s for s in streams if s.get('codec_type')=='audio']
    channels = {1:'1.0',2:'2.0',6:'5.1',8:'7.1'}
    tags = {}
    if video.get('height'): tags['resolution']=str(video['height'])+'p'
    if video.get('codec_name'): tags['codec']=video['codec_name']
    formats = list(dict.fromkeys(s['codec_name'] for s in audio if s.get('codec_name')))
    layouts = list(dict.fromkeys(s.get('channel_layout') or channels.get(s.get('channels')) for s in audio))
    if formats: tags['audio']=formats
    if any(layouts): tags['channels']=[v for v in layouts if v]
    return tags

def release_summary(tags, label='', measured=None):
    data=dict(tags or {})
    for key,pattern in [('resolution',r'(?<!\d)(2160|1440|1080|720|576|480)[pр]'),('source',r'\b(WEB[- .]?DLRip|WEB[- .]?DL|WEBRip|BluRay|BDRemux|BDRip|DVDRip)\b'),('codec',r'\b(HEVC|AVC|AV1|[HX][ .-]?26[45])\b')]:
        if not data.get(key):
            match=re.search(pattern,label,re.I)
            if match:data[key]=match[1]+('p' if key=='resolution' else '')
    if not data.get('hdr'):
        formats=[('Dolby Vision',r'\b(?:DV|DoVi|Dolby[ ._-]*Vision)\b'),
                 ('HDR10+',r'\bHDR10(?:\+|Plus\b)'),('HDR10',r'\bHDR10\b(?!\+)'),
                 ('HDR',r'\bHDR\b'),('SDR',r'\bSDR\b')]
        data['hdr']=[name for name,pattern in formats if re.search(pattern,label,re.I)]
    data.update(measured or {})
    parts=[]
    aliases={'4k':'2160p','hevc':'HEVC','h265':'HEVC','x265':'HEVC','h264':'AVC','x264':'AVC','avc':'AVC','av1':'AV1','dv':'Dolby Vision','eac3':'DD+','ac3':'AC-3','aac':'AAC','stereo':'2.0','mono':'1.0','5.1(side)':'5.1','5.1(back)':'5.1'}
    for key in ('resolution','source','hdr','codec','audio','channels','editions'):
        values=data.get(key) or []
        for val in values if isinstance(values,list) else [values]:
            text=str(val)
            parts.append(aliases.get(text.casefold(),text))
    if data.get('dubbed') or data.get('dubLabel'):
        parts.append('дубляж'+(' ('+str(data['dubLabel'])+')' if data.get('dubLabel') else ''))
    for key,prefix in [('originalAudioLanguages','оригинал '),('subtitleLanguages','субтитры ')]:
        if data.get(key):parts.append(prefix+', '.join(str(v).upper() for v in data[key]))
    if data.get('subbed') and not data.get('subtitleLanguages'): parts.append('субтитры')
    return ' · '.join(dict.fromkeys(parts))

def unpack_ts(item):
    try:
        data = json.loads(item.get('data') or '{}')
    except (ValueError, TypeError):
        data = {}
    m = checked(checked(data,dict,'TorrServer metadata').get('movie') or {},dict,'TorrServer movie')
    tv = bool(m.get('first_air_date') or m.get('number_of_seasons') or m.get('media_type') == 'tv')
    movie = not tv and bool(m.get('release_date') or m.get('media_type') == 'movie')
    title = checked(m.get('name') or m.get('title') or '',str,'TorrServer movie title')
    return {'hash': valid_hash(item.get('hash'),'TorrServer list'), 'label': checked(item.get('title') or title,str,'TorrServer title'), 'tv': tv,
            'movie': movie, 'tmdb': m.get('id'), 'title': title, 'poster':checked(m.get('poster_path') or item.get('poster') or '',str,'TorrServer poster'),
            'year':str(m.get('first_air_date') or m.get('release_date') or '')[:4],
            'original': m.get('original_name') or m.get('original_title') or title,
            'files': file_list(item['file_stats'] if item.get('file_stats') is not None else [],'TorrServer files'), 'raw': item}

class Providers:
    def __init__(self):
        self.cfg = json.loads(SETTINGS.read_text())
        self.memo = {}

    def json_file(self,path):
        stamp=path.stat().st_mtime_ns
        cached=self.memo.get(path)
        if not cached or cached[0]!=stamp:
            self.memo[path]=(stamp,checked(json.loads(path.read_text()),dict,path.name))
        return self.memo[path][1]

    def ts(self, place, action, **fields):
        c = self.cfg['ts'][place]
        return http(c['url'] + '/torrents', {'Authorization': c['auth']}, {'action': action, **fields})

    def ts_files(self, place, h, save=False):
        if not HASH.fullmatch(h):
            raise Unavailable('Недопустимый хеш')
        c = self.cfg['ts'][place]
        # stat requests metadata only, without play, preload or save_to_db.
        params={'stat':'', 'link':'magnet:?xt=urn:btih:'+h}
        if save:
            params['save']=''
        data = http(c['url'] + '/stream?' + parse.urlencode(params),
                    {'Authorization': c['auth']}, timeout=24)
        if valid_hash(checked(data,dict,'TorrServer stat').get('hash'),'TorrServer stat') != h or not data.get('file_stats'):
            raise Unavailable('Список файлов пока недоступен')
        return file_list(data['file_stats'],'TorrServer stat')

    def box(self, path, params=None, form=None):
        cfg = self.json_file(APP / 'torbox-private.json')
        if cfg.get('allow_client_credentials') or not cfg.get('api_key'):
            raise Unavailable('Серверный аккаунт TorBox не настроен')
        url = 'https://api.torbox.app/v1/api/torrents/' + path
        if params:
            url += '?' + parse.urlencode(params)
        headers = {'Authorization':'Bearer ' + cfg['api_key']}
        body = None
        if form:
            boundary='season-'+uuid.uuid4().hex
            headers['Content-Type'] = 'multipart/form-data; boundary='+boundary
            body=''.join('--'+boundary+'\r\nContent-Disposition: form-data; name="'+name+'"\r\n\r\n'+val+'\r\n'
                         for name,val in form.items()).encode()+('--'+boundary+'--\r\n').encode()
        result = http(url, headers, body)
        if checked(result,dict,'TorBox '+path).get('success') is not True:
            raise Unavailable('TorBox отклонил запрос')
        return result.get('data')

    def box_deletion(self, actor, body, check=False):
        # Reuse the Lampac module's account guard, item deletion and metadata cleanup.
        route='delete/check' if check else 'delete'
        return self.box_local(actor,route,body)

    def box_local(self,actor,route,body):
        port=self.cfg.get('lampac_port')
        if port is None:
            port=self.json_file(APP/'init.conf')['listen']['port']
        if not 1 <= int(port) <= 65535: raise Unavailable('Некорректный порт Lampac')
        url=f'http://127.0.0.1:{int(port)}/torbox/'+route+'?'+parse.urlencode({'account_email':actor})
        result=http(url,{'X-TorBox-Mode':'file'},body,timeout=45)
        if not isinstance(result,dict) or not result.get('success'):
            raise Unavailable('TorBox не подтвердил запрос')
        return result

    def write_film(self,place,item,film,actor,manual):
        if place=='box':
            saved=self.box_local(actor,'metadata',dict(id=item['raw']['id'],film=film,automatic=not manual))['film']
            if saved.get('tmdb_id')!=film['tmdb_id'] or saved.get('media_type')!=film['media_type'] or (film['poster_path'] and not saved.get('poster_path')):
                raise Unavailable('Метаданные TorBox изменились. Уточните карточку')
            return
        raw=item['raw']
        data=json.loads(raw.get('data') or '{}')
        movie=data.get('movie') or {}
        tv=film['media_type']=='tv'
        if movie.get('id')!=film['tmdb_id'] or item['tv']!=tv: movie={}
        date='first_air_date' if tv else 'release_date'
        year=str(film['year']) if film['year'] else ''
        if not year or not str(movie.get(date) or '').startswith(year):
            movie[date]=year+'-01-01' if year else ''
        movie.update(id=film['tmdb_id'],media_type=film['media_type'],poster_path=film['poster_path'])
        movie['name' if tv else 'title']=film['title']
        movie['original_name' if tv else 'original_title']=film['original_title']
        data['movie']=movie
        poster=film['poster_path']
        if poster.startswith('/'): poster='https://image.tmdb.org/t/p/w500'+poster
        self.ts(place,'add',link='magnet:?xt=urn:btih:'+item['hash'],title=raw.get('title') or item['label'],
                category=raw.get('category') or '',poster=poster,data=json.dumps(data,ensure_ascii=False),save_to_db=True)
        current=next((t for t in self.inventory(place) if t['hash']==item['hash']),{})
        if current.get('tmdb')!=film['tmdb_id'] or current.get('poster')!=film['poster_path'] or current.get('tv')!=tv:
            raise Unavailable('TorrServer не подтвердил сохранение карточки')

    def inventory(self, place):
        if place != 'box':
            return [unpack_ts(t) for t in records(self.ts(place, 'list'),'TorrServer list')]
        mappings = self.json_file(APP / 'database/torbox-library.json') if (APP / 'database/torbox-library.json').exists() else {}
        items = []
        offset = 0
        while True:
            batch = self.box('mylist', dict(limit=100, offset=offset, files='true', bypass_cache='true'))
            if not isinstance(batch, list):
                raise Unavailable('Некорректный список TorBox')
            for t in records(batch,'TorBox mylist'):
                h = valid_hash(t.get('hash'),'TorBox mylist')
                if (type(t.get('id')) is not int or t['id']<0 or type(t.get('name')) is not str
                        or ('download_present' in t and type(t['download_present']) is not bool)):
                    raise Unavailable('Некорректная запись TorBox')
                metas = [entries['hash:'+h] for entries in mappings.values() if 'hash:'+h in entries]
                m = metas[0] if metas and len({(v.get('tmdb_id'),v.get('media_type')) for v in metas}) == 1 else {}
                items.append(dict(hash=h, label=t['name'], title=m.get('title') or '', original=m.get('original_title') or '',
                                  tmdb=m.get('tmdb_id'), tv=m.get('media_type')=='tv', movie=m.get('media_type')=='movie',
                                  files=file_list(t['files'] if t.get('files') is not None else [],'TorBox files'), raw=t, poster=m.get('poster_path') or '', year=str(m.get('year') or '')))
            if len(batch) < 100:
                return items
            offset += len(batch)

    def calendar(self, path, **params):
        key = ('tmdb', path, tuple(params.items()))
        if key not in self.memo:
            self.memo[key] = http('https://api.themoviedb.org/3/'+path+'?'+parse.urlencode(
                dict(api_key=self.cfg['tmdb'], language='ru-RU', **params)))
        result=checked(self.memo[key],dict,'TMDB')
        if path.startswith('search/'): records(result.get('results'),'TMDB search')
        if '/season/' in path: records(result.get('episodes'),'TMDB episodes')
        return result

    def jac(self, title, original, movie=False, year=0, *, timeout=18):
        key = ('jac',title,original,movie,year)
        if key in self.memo:
            return self.memo[key]
        c = self.cfg['jacred']
        data = http(c['base_url'].rstrip('/') + '/api/v2.0/indexers/all/results?' + parse.urlencode(
            dict(title=title,title_original=original or title,year=year,is_serial=1 if movie else 2,apikey=c['api_key'])),timeout=timeout)
        rows=records(checked(data,dict,'JacRed search').get('Results'),'JacRed results')
        out = []
        for row in rows:
            m = re.search(r'btih:([\da-f]{40})(?![\da-f])', parse.unquote(row.get('MagnetUri') or ''), re.I)
            u = parse.urlsplit(row.get('Details') or '')
            if not m or u.scheme not in ('https','http') or not u.hostname:
                continue
            h = m.group(1).lower()
            allowed = {'opentor.net','tracker.openbittorrent.com','tracker.dler.com','open.stealth.si','exodus.desync.com','tracker.opentrackr.org','tracker.torrent.eu.org','open.demonii.com','bt.t-ru.org'}
            magnet = parse.parse_qs(parse.urlsplit(row.get('MagnetUri') or '').query)
            self.memo['trackers',h] = [tr for tr in magnet.get('tr',[]) if parse.urlsplit(tr).scheme in ('udp','https','http') and parse.urlsplit(tr).hostname in allowed and not parse.urlsplit(tr).username][:12]
            page = digest(u.hostname.lower()+u.path+('?' + u.query if u.query else ''))
            info = row.get('info') or {}
            streams = row.get('ffprobe') or []
            height = max([int(s.get('height') or 0) for s in streams if s.get('codec_type')=='video'] or [0])
            out.append(dict(hash=m.group(1).lower(), page=page, tracker=u.hostname, label=row.get('Title') or '',
                tags=info.get('releaseTags') or {}, measured=measured_tags(streams),
                seasons=(info.get('releaseTags') or {}).get('seasons') or info.get('seasons') or [], height=height, title=info.get('name') or '', original=info.get('originalname') or ''))
        self.memo[key]=out
        return out

    def candidate_files(self, place, h):
        if place != 'box':
            from magnet_meta import files
            if ('meta',h) not in self.memo:
                self.memo['meta',h]=files(h,self.memo.get(('trackers',h),[]))
            return self.memo['meta',h]
        key=('box-files',h)
        if key in self.memo: return self.memo[key]
        cache = checked(self.box('checkcached', {'hash':h, 'format':'object','list_files':'true'}),dict,'TorBox checkcached')
        row = cache.get(h)
        if row is not None: checked(row,dict,'TorBox cached torrent')
        if not row or not row.get('files'):
            raise Unavailable('Ожидает кеша TorBox')
        self.memo[key]=file_list(row['files'],'TorBox checkcached')
        return self.memo[key]
