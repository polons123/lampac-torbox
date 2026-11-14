#!/usr/bin/env python3
"""Run the real deployer offline with a fake installer and systemctl."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = Path(__file__).resolve().parent

SHIM = r'''#!/usr/bin/env python3
import json,os,pathlib,shutil,subprocess,sys,runpy
p=pathlib.Path; base=p(os.environ['FIXTURE']); name=p(sys.argv[0]).name; args=sys.argv[1:]
root=base/'app'; service=base/'service'; failure=os.environ.get('FAILURE','')
if name=='systemctl':
 with (base/'commands').open('a') as f:f.write(' '.join(args)+'\n')
 action=args[0]
 if action=='is-active':sys.exit(0 if service.read_text()=='active' else 3)
 if action=='stop':service.write_text('inactive')
 if action=='start':service.write_text('active')
 if action=='show':print('0000' if failure=='verify' else '0077')
elif name=='curl':
 shutil.copyfile(base/'assets'/args[-1].rsplit('/',1)[-1],args[args.index('-o')+1])
elif name=='rsync':
 if failure=='snapshot' and '--delete' not in args:sys.exit(75)
 source,target=map(p,[a for a in args if not a.startswith('-')][-2:])
 if '--delete' in args:
  for x in target.iterdir():
   if not (source/x.name).exists():
    if x.is_dir():shutil.rmtree(x)
    else:x.unlink()
 shutil.copytree(source,target,dirs_exist_ok=True,symlinks=True)
elif name=='install':
 if failure=='overlay' and args[-1].endswith('module/Online/plugin.js'):sys.exit(73)
 shutil.copy2(args[-2],args[-1])
elif name=='installer':
 (root/'Core.dll').write_text('new core');(root/'database/TimeCode.sql').write_text('new schema')
 (root/'new-file').write_text('added by installer')
 (root/'version.txt').write_text(args[args.index('--tag')+1]+'\n')
 subprocess.run(['systemctl','start','lampac'],check=True)
 if failure=='installer':sys.exit(42)
elif name=='python3':
 if failure=='write' and len(args)>1 and args[1]=='apply':
  m=runpy.run_path(args[0]);real=m['os'].replace;counter=[0]
  def fail(src,dst):
   counter[0]+=1
   if counter[0]==2:raise OSError('fixture: atomic write denied')
   return real(src,dst)
  m['os'].replace=fail;m['apply'](*map(p,args[2:]));sys.exit(0)
 os.execv(os.environ['REAL_PYTHON'],[os.environ['REAL_PYTHON']]+args)
'''


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        b = self.base
        self.root = b / 'app'
        self.root.mkdir()
        self.old = {'LampaWeb': {'customPlugins': [
            {'url': 'https://example.test/torbox.js?v=1'},
            {'url': 'https://example.test/online.js?label=torbox'},
            {'url': 'https://example.test/custom-torbox.js'}]},
            'TorBox': {'enable': True}, 'accsdb': {'enable': True}}
        self.put('init.conf', json.dumps(self.old))
        self.put('excludes.conf', '# torbox is mentioned in a comment\nmods/TorBox/**\nusers.json\ncustom-torbox.json\n')
        self.put('torbox-private.json', 'private fixture')
        self.put('mods/TorBox/manifest.json', '{}')
        self.put('database/torbox-library.json', '{}')
        self.put('database/torbox-series.json', '{}')
        self.put('database/TimeCode.sql', 'old schema')
        self.put('Core.dll', 'old core')
        self.put('version.txt', 'old')
        self.put('users.json', 'old tokens')
        self.put('module/Online/plugin.js', 'old script')
        self.put('cache/static/old.js', 'var torboxVoiceMenu = null; torbox_voice_options')
        self.put('cache/static/innocent.js', 'var name="torbox";')
        self.put('cache/media.js', 'var torboxVoiceMenu = null; torbox_voice_options')
        (b / 'state').mkdir()
        (b / 'state/old').write_text('old release state')
        (b / 'checker').write_text('old checker')
        (b / 'dropin').write_text('old dropin')
        (b / 'service').write_text('active')
        (b / 'commands').touch()
        (b / 'locks').mkdir()
        assets = b / 'assets'
        assets.mkdir()
        overlay = {'module/Online/plugin.js': b'new script',
                   'release-tools/lampac-postupdate-check.py': b'new checker'}
        with zipfile.ZipFile(assets / 'lampac-nextgen.zip', 'w') as z:
            z.writestr('Core.dll', 'new core')
            for name, content in overlay.items():
                z.writestr(name, content)
        manifest = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name, data in overlay.items())
        (assets / 'overlay.sha256').write_text(manifest)
        info = {'zip_sha256': hashlib.sha256((assets / 'lampac-nextgen.zip').read_bytes()).hexdigest(),
                'overlay_sha256': hashlib.sha256(manifest.encode()).hexdigest(), 'overlay_files': 2}
        (assets / 'build-info.txt').write_text(''.join(f'{k}={v}\n' for k, v in info.items()))
        tools = b / 'bin'
        tools.mkdir()
        for name in ['systemctl', 'curl', 'rsync', 'install', 'python3', 'installer']:
            f = tools / name
            f.write_text(SHIM.replace('#!/usr/bin/env python3', '#!' + sys.executable, 1))
            f.chmod(0o755)
        # The deployer calls the upstream installer through bash.
        (b / 'installer.sh').write_text('#!/bin/bash\nexec installer "$@"\n')
        self.env = dict(os.environ, FIXTURE=str(b), REAL_PYTHON=sys.executable,
                        PATH=str(tools) + ':' + os.environ['PATH'], LAMPAC_ROOT=str(self.root),
                        LAMPAC_RELEASE_STATE=str(b / 'state'), LAMPAC_CHECKER=str(b / 'checker'),
                        LAMPAC_SYSTEMD_DROPIN=str(b / 'dropin'), LAMPAC_BACKUP_ROOT=str(b / 'backups'),
                        LAMPAC_LOCK_DIR=str(b / 'locks'), LAMPAC_INSTALLER=str(b / 'installer.sh'))
        self.before = self.contents()

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def contents(self):
        return {str(p.relative_to(self.base)): p.read_bytes()
                for name in ['app', 'state', 'checker', 'dropin']
                for p in ([self.base / name] if (self.base / name).is_file() else (self.base / name).rglob('*'))
                if p.is_file()}

    def deploy(self, failure=''):
        return subprocess.run(['bash', str(HERE / 'deploy-host.sh'), 'custom-fixture'],
                              env=dict(self.env, FAILURE=failure), capture_output=True, text=True, timeout=30)

    def test_success_and_repeat(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads((self.root / 'init.conf').read_text())
        self.assertNotIn('TorBox', config)
        self.assertEqual(config['LampaWeb']['customPlugins'], self.old['LampaWeb']['customPlugins'][1:])
        self.assertEqual((self.root / 'excludes.conf').read_text(), '# torbox is mentioned in a comment\nusers.json\ncustom-torbox.json\n')
        self.assertFalse((self.root / 'mods/TorBox').exists())
        self.assertFalse((self.root / 'cache/static/old.js').exists())
        self.assertTrue((self.root / 'cache/static/innocent.js').exists())
        self.assertTrue((self.root / 'cache/media.js').exists())
        self.assertEqual((self.root / 'users.json').read_text(), 'old tokens')
        before = {n: (self.root / n).stat().st_mtime_ns for n in ['init.conf', 'excludes.conf']}
        # A repeated migration must not even read matching script cache entries.
        self.put('cache/static/later.js', 'var torboxVoiceMenu = null; torbox_voice_options')
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'cache/static/later.js').exists())
        self.assertEqual(before, {n: (self.root / n).stat().st_mtime_ns for n in before})
        self.assertEqual((self.base / 'service').read_text(), 'active')
        backups = list((self.base / 'backups').iterdir())
        self.assertEqual(len(backups), 2)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o700 for p in backups))
        self.assertTrue(any('TorBox' in json.loads((p / '0/init.conf').read_text()) for p in backups))

    def test_bad_configuration_before_stop(self):
        for text in ['{ /* unsupported comments */ }', '{"LampaWeb":{"customPlugins":{}}}']:
            self.put('init.conf', text)
            before = self.contents()
            result = self.deploy()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.contents(), before)
        self.assertNotIn('stop lampac', (self.base / 'commands').read_text())

    def test_failure_restores_files_and_service(self):
        for failure, status in [('installer', 42), ('overlay', 73), ('write', 1), ('verify', 1), ('snapshot', 1)]:
            with self.subTest(failure=failure):
                result = self.deploy(failure)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(self.contents(), self.before)
                self.assertEqual((self.base / 'service').read_text(), 'active')
                self.assertFalse((self.base / 'state/migrations/torbox-removal-v1.done').exists())

    def test_initially_inactive_failure(self):
        (self.base / 'service').write_text('inactive')
        result = self.deploy('installer')
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual((self.base / 'service').read_text(), 'inactive')
        self.assertEqual(self.contents(), self.before)


if __name__ == '__main__':
    unittest.main()
