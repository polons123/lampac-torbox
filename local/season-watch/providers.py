import hashlib
import json
import re
import uuid
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

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def season_hint(name):
    m = SEASON.search(name)
    return int(m.group(1) or m.group(2)) if m else None

def episode_files(files, hint=None):
    seasons = {}
    for f in files:
        name = str(f.get('path') or f.get('name') or '')
        size = int(f.get('length') or f.get('size') or 0)
        if not VIDEO.search(name) or BAD.search(name) or size <= 0:
            continue
        leaf = name.rsplit('/', 1)[-1]
        m = SE.search(leaf) or EX.search(leaf)
        if m:
            season, ep = map(int, m.groups())
            # Multiple episodes in one file require a manual decision.
            if re.match(r'\s*[-+]?\s*[Ee]\d+|-\d{1,2}(?=[._ -]|$)', leaf[m.end():]):
                continue
        else:
            epmatch = EP.search(leaf) or re.match(r'^(\d{1,3})[ ._-]+', leaf)
            season = season_hint(name) or hint
            if not season or not epmatch:
                continue
            ep = int(epmatch.group(1))
        if ep > 0 and season > 0:
            seasons.setdefault(season, {}).setdefault(str(ep), []).append([leaf, size])
    return seasons

def unpack_ts(item):
    try:
        data = json.loads(item.get('data') or '{}')
    except (ValueError, TypeError):
        data = {}
    m = data.get('movie') or {}
    tv = bool(m.get('first_air_date') or m.get('number_of_seasons') or m.get('media_type') == 'tv')
    movie = not tv and bool(m.get('release_date') or m.get('media_type') == 'movie')
    title = m.get('name') or m.get('title') or ''
    return {'hash': item['hash'].lower(), 'label': item.get('title') or title, 'tv': tv,
            'movie': movie, 'tmdb': m.get('id'), 'title': title, 'poster':m.get('poster_path') or item.get('poster') or '',
            'year':str(m.get('first_air_date') or m.get('release_date') or '')[:4],
            'original': m.get('original_name') or m.get('original_title') or title,
            'files': item.get('file_stats') or [], 'raw': item}

class Providers:
    def __init__(self):
        self.cfg = json.loads(SETTINGS.read_text())
        self.memo = {}

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
        if data.get('hash', '').lower() != h or not data.get('file_stats'):
            raise Unavailable('Список файлов пока недоступен')
        return data['file_stats']

    def box(self, path, params=None, form=None):
        cfg = json.loads((APP / 'torbox-private.json').read_text())
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
        if result.get('success') is not True:
            raise Unavailable('TorBox отклонил запрос')
        return result.get('data')

    def box_deletion(self, actor, body, check=False):
        # Reuse the Lampac module's account guard, item deletion and metadata cleanup.
        route='delete/check' if check else 'delete'
        url='http://127.0.0.1:19092/torbox/'+route+'?'+parse.urlencode({'account_email':actor})
        result=http(url,{'X-TorBox-Mode':'file'},body,timeout=45)
        if not isinstance(result,dict) or not result.get('success'):
            raise Unavailable('TorBox не подтвердил запрос')
        return result

    def inventory(self, place):
        if place != 'box':
            return [unpack_ts(t) for t in self.ts(place, 'list')]
        mappings = json.loads((APP / 'database/torbox-library.json').read_text()) if (APP / 'database/torbox-library.json').exists() else {}
        items = []
        offset = 0
        while True:
            batch = self.box('mylist', dict(limit=100, offset=offset, files='true', bypass_cache='true'))
            if not isinstance(batch, list):
                raise Unavailable('Некорректный список TorBox')
            for t in batch:
                h = (t.get('hash') or '').lower()
                metas = [entries['hash:'+h] for entries in mappings.values() if 'hash:'+h in entries]
                m = metas[0] if metas and len({(v.get('tmdb_id'),v.get('media_type')) for v in metas}) == 1 else {}
                items.append(dict(hash=h, label=t['name'], title=m.get('title') or '', original=m.get('original_title') or '',
                                  tmdb=m.get('tmdb_id'), tv=m.get('media_type')=='tv', movie=m.get('media_type')=='movie',
                                  files=t.get('files') or [], raw=t, poster=m.get('poster_path') or '', year=str(m.get('year') or '')))
            if len(batch) < 100:
                return items
            offset += len(batch)

    def calendar(self, path, **params):
        key = ('tmdb', path, tuple(params.items()))
        if key not in self.memo:
            self.memo[key] = http('https://api.themoviedb.org/3/'+path+'?'+parse.urlencode(
                dict(api_key=self.cfg['tmdb'], language='ru-RU', **params)))
        return self.memo[key]

    def jac(self, title, original, movie=False, year=0):
        key = ('jac',title,original,movie,year)
        if key in self.memo:
            return self.memo[key]
        c = self.cfg['jacred']
        data = http(c['base_url'].rstrip('/') + '/api/v2.0/indexers/all/results?' + parse.urlencode(
            dict(title=title,title_original=original or title,year=year,is_serial=1 if movie else 2,apikey=c['api_key'])))
        if not isinstance(data.get('Results'), list):
            raise Unavailable('Некорректный ответ JacRed')
        out = []
        for row in data['Results']:
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
                seasons=info.get('seasons') or [], height=height, title=info.get('name') or '', original=info.get('originalname') or ''))
        self.memo[key]=out
        return out

    def candidate_files(self, place, h):
        if place != 'box':
            from magnet_meta import files
            if ('meta',h) not in self.memo:
                self.memo['meta',h]=files(h,self.memo.get(('trackers',h),[]))
            return self.memo['meta',h]
        cache = self.box('checkcached', {'hash':h, 'format':'object','list_files':'true'})
        row = cache.get(h) if isinstance(cache,dict) else None
        if not row or not row.get('files'):
            raise Unavailable('Ожидает кеша TorBox')
        return row['files']
