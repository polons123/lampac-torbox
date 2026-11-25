"""Resolve BEP 9 metadata with piece requests disabled."""
import tempfile
import time
from urllib import parse
from pathlib import Path
import libtorrent as lt
from providers import HASH, Unavailable

def files(h, trackers=()):
    if not HASH.fullmatch(h):
        raise Unavailable('Недопустимый хеш')
    settings=dict(listen_interfaces='0.0.0.0:0',enable_incoming_tcp=False,enable_incoming_utp=False,
                  enable_lsd=False,enable_upnp=False,enable_natpmp=False,connections_limit=24,
                  active_downloads=0,active_seeds=0, alert_mask=0)
    with tempfile.TemporaryDirectory(prefix='season-metadata-') as scratch:
        session=lt.session(settings)
        params=lt.parse_magnet_uri('magnet:?xt=urn:btih:'+h)
        params.save_path=scratch
        flags=lt.torrent_flags
        params.flags=(params.flags | flags.upload_mode | flags.stop_when_ready) & ~flags.auto_managed & ~flags.paused
        # Stable public trackers, no tracker credentials or web seeds forwarded.
        params.trackers=list(trackers) or ['udp://tracker.opentrackr.org:1337/announce','udp://open.stealth.si:80/announce']
        handle=session.add_torrent(params)
        handle.resume()
        try:
            until=time.monotonic()+36
            while time.monotonic()<until:
                if handle.status().has_metadata:
                    info=handle.torrent_file()
                    if str(info.info_hashes().v1)!=h:
                        raise Unavailable('Хеш метаданных не совпал')
                    storage=info.files()
                    return [dict(path=storage.file_path(n),length=storage.file_size(n)) for n in range(storage.num_files())]
                time.sleep(.25)
            raise Unavailable('Список файлов новой версии пока недоступен')
        finally:
            session.remove_torrent(handle)
            session.pause()
