"""Compile actual media modules in an isolated runtime, using synthetic services."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[2]
HASH = 'a' * 40
calls = []


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def send(self, obj):
        content=json.dumps(obj,separators=(',',':')).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(content)
    def do_GET(self):
        if self.path.startswith('/stream'):
            return self.send({'file_stats':[{'id':2,'path':'Sample.S01E01.mp4'}]})
        rows=[]
        for n,(resolution,tracker) in enumerate([('1440p','fixture+one'),('480p','second'),('1080p','selezen')]):
            rows.append({'Title':f'Sample 2026 {resolution} HDR10+', 'Tracker':tracker,'Size':100000000,'Seeders':20,'Peers':4,
                         'MagnetUri':'magnet:?xt=urn:btih:'+str(n+1)*40,'PublishDate':'2026-01-01',
                         'info':{'voices':["Studio 1+1, 'Team'"],'sizeName':'100 MB','seasons':[1]}})
        self.send({'Results':rows})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        calls.append((self.path,body,self.headers.get('Authorization')))
        if body.get('action')=='get':return self.send({'connected_seeders':3,'active_peers':7,'total_peers':12,'download_speed':1500})
        if body.get('action')=='add':return self.send({'hash':HASH})
        self.send({})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,required=True);parser.add_argument('--dotnet',default=shutil.which('dotnet'));args=parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder)
        for name in ['Core.dll','Shared.dll','Core.deps.json','Core.runtimeconfig.json']:shutil.copy2(args.runtime/name,root/name)
        (root/'runtimes').symlink_to(args.runtime.resolve()/'runtimes',target_is_directory=True)
        for name in ['Tracks','PidTor']:shutil.copytree(REPO/'Modules'/name,root/'module'/name)
        server=ThreadingHTTPServer(('127.0.0.1',0),Mock);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        endpoint='http://127.0.0.1:'+str(server.server_port)
        config={'listen':{'ip':'127.0.0.1','port':port,'localhost':'127.0.0.1','scheme':'http'},'BaseModule':{'LoadModules':['.*'],'SkipModules':[]},
                'accsdb':{'enable':False},'WAF':{'enable':False},'openstat':{'enable':False},'chromium':{'enable':False},'serilog':False,
                'Tracks':{'tsstat':endpoint,'tsauth':'fixture:secret','ffprobe_timeout_seconds':2},
                'PidTor':{'enable':True,'min_sid':0,'redapi':endpoint,'torrs':[endpoint]}}
        for name,content in [('init.conf',config),('base.conf',{}),('appsettings.Production.json',{})]:(root/name).write_text(json.dumps(content))
        (root/'data').mkdir();fake=root/'data/ffprobe'
        fake.write_text('''#!/usr/bin/python3
import sys,os,json,time,subprocess
mode=sys.argv[-1]
with open('probe-starts','a') as f:f.write(mode+'\\n')
if '/hang' in mode:
 child=subprocess.Popen(['/usr/bin/python3','-c','import time;time.sleep(120)'])
 with open('probe-pids','w') as f:f.write(str(os.getpid())+' '+str(child.pid))
 time.sleep(120)
if '/empty' in mode:sys.exit(0)
if '/stderr' in mode:sys.stderr.write('x'*2000000);sys.stderr.flush()
time.sleep(.2)
print(json.dumps({'streams':[{'codec_type':'video','codec_name':'h264'}],'format':{'bit_rate':'1200000'}}))
''');fake.chmod(0o755)
        def get(path):
            try:
                with urllib.request.urlopen('http://127.0.0.1:'+str(port)+path,timeout=15) as q:code,raw=q.status,q.read().decode()
            except urllib.error.HTTPError as e:code,raw=e.code,e.read().decode()
            try:return code,json.loads(raw)
            except ValueError:return code,raw
        log=(root/'server.log').open('w');proc=subprocess.Popen([args.dotnet,'Core.dll'],cwd=root,stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(160):
                try:
                    if get('/ffprobe')[0]==200:break
                except OSError:pass
                if proc.poll() is not None:raise AssertionError((root/'server.log').read_text()[-8000:])
                time.sleep(.25)
            else:raise AssertionError('isolated runtime startup timeout')
            assert get('/ffprobe')[0]==200,(root/'server.log').read_text()[-6000:]
            def probe(mode):return get('/ffprobe?'+urllib.parse.urlencode({'media':'http://example.test/'+mode}))
            with ThreadPoolExecutor(max_workers=8) as pool:
                result=list(pool.map(lambda _:probe('ok'),range(8)))
            assert all(code==200 and data.get('streams') for code,data in result),result
            assert sum('/ok' in line for line in (root/'probe-starts').read_text().splitlines())==1
            print('PASS concurrent identical probes execute once',flush=True)
            assert probe('stderr')[1].get('streams');print('PASS large stderr drains concurrently',flush=True)
            started=time.monotonic()
            with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(lambda _:probe('hang'),range(2)))
            assert all(code==504 for code,_ in result),result
            assert time.monotonic()-started<5
            for pid in (root/'probe-pids').read_text().split():
                p=Path('/proc')/pid/'stat'
                assert not p.exists() or p.read_text().split()[2]=='Z','probe process still alive'
            assert probe('after-timeout')[1].get('streams');print('PASS deadline kills process tree and releases gates',flush=True)
            assert probe('empty')[0]==200
            assert sum('/empty' in line for line in (root/'probe-starts').read_text().splitlines())==3
            print('PASS only quick empty failures retry',flush=True)
            for media in ['http://example.test/stream?link='+HASH+'&index=2','http://example.test/lite/pidtor/serial/'+HASH,'http://example.test/lite/pidtor/s'+HASH+'?tsid=9']:
                code,data=get('/tracks/stat?'+urllib.parse.urlencode({'media':media}));assert code==200 and data['total']==12,(code,data)
            assert len(calls)==1 and calls[0][0]=='/torrents' and calls[0][1]=={'action':'get','hash':HASH}
            assert calls[0][2]=='Basic Zml4dHVyZTpzZWNyZXQ='
            assert get('/tracks/stat?hash='+HASH+'0')[1]=={}
            print('PASS film/serial/stream swarm status uses authenticated read-only get and shared cache',flush=True)
            query='/lite/pidtor?title=Sample&original_title=Sample&year=2026&serial=0'
            code,data=get(query+'&rjson=true');assert code==200 and data.get('data'),(code,data,(root/'server.log').read_text()[-5000:])
            rows=data['data'];assert len(rows)==2,rows
            assert {row['pidtor']['res'] for row in rows}=={'480p','1440p'}
            assert all(row['pidtor']['voices']==["Studio 1+1, 'Team'"] and row['pidtor']['hdr']=='HDR10+' for row in rows)
            assert any(row['pidtor']['trackers']==['fixture+one'] for row in rows)
            html=get(query)[1];assert isinstance(html,str) and 'pidtor' in html
            for payload in re.findall("data-json='([^']*)'",html):assert json.loads(payload)['pidtor']['voices']==["Studio 1+1, 'Team'"]
            print('PASS native and HTML structured filters retain 480p/1440p, literal punctuation and skip selezen',flush=True)
            serial=get('/lite/pidtor?title=Sample&original_title=Sample&year=2026&serial=1&s=1&rjson=true')[1]
            assert len(serial['data'])==2 and all('pidtor' in row for row in serial['data']),serial
            episode=get(serial['data'][0]['url'].replace('http://127.0.0.1:'+str(port),''))[1]
            assert episode['data'][0]['pidtor']['index']==2,episode
            print('PASS serial release and episode metadata include selected torrent and file',flush=True)
        finally:
            proc.terminate();proc.wait(timeout=15);log.close();server.shutdown();server.server_close()


if __name__=='__main__':main()
