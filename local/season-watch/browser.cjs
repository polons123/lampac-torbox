const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const delay = ms => new Promise(r => setTimeout(r,ms));
(async function () {
    const dir='/opt/lampac/wwwroot/lampa-main';
    let actions = 0;
    const fixture={items:[],checked:{peer:{time:1}},last:1};
    const example={id:'a'.repeat(32),revision:'b'.repeat(32),label:'Fixture 1080p',title:'Fixture <b>Show</b>',season:2,owned:[1,2],identities:[],choices:[],detail:'',tmdb:3};
    fixture.items.push({...example,place:'peer',ready:[3],waiting:[]},{...example,place:'box',ready:[3],waiting:[]},{...example,tmdb:5,title:'Waiting show',place:'home',ready:[],waiting:[4]});
    const server=http.createServer((q,r)=>{
        const route=new URL(q.url,'http://localhost').pathname;
        if(route==='/season-watch/list') {r.setHeader('Content-Type','application/json');return r.end(JSON.stringify(fixture));}
        if(route==='/season-watch/action') {actions++;r.setHeader('Content-Type','application/json');return r.end('{"success":true}');}
        if(route==='/lampainit.js') {r.setHeader('Content-Type','application/javascript');return r.end('');}
        const p=path.join(dir,route);
        if(!p.startsWith(dir+'/') || !fs.existsSync(p)||!fs.statSync(p).isFile()){r.statusCode=404;return r.end();}
        const ext=path.extname(p);r.setHeader('Content-Type',({'.js':'application/javascript','.css':'text/css','.html':'text/html','.json':'application/json'})[ext]||'application/octet-stream');
        fs.createReadStream(p).pipe(r);
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const origin='http://127.0.0.1:'+server.address().port;
    const profile=fs.mkdtempSync(path.join(os.tmpdir(),'season-browser-'));
    const chrome=spawn('chromium',['--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--disable-background-networking','--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{stdio:'ignore'});
    let ws;
    try {
        const portfile=path.join(profile,'DevToolsActivePort');
        for(let i=0;i<100&&!fs.existsSync(portfile);i++)await delay(100);
        const port=fs.readFileSync(portfile,'utf8').split('\n')[0];
        const tab=(await (await fetch('http://127.0.0.1:'+port+'/json/list')).json()).find(t=>t.type==='page');
        ws=new WebSocket(tab.webSocketDebuggerUrl);
        await new Promise(r=>ws.addEventListener('open',r,{once:true}));
        let seq=0;const pending=new Map();
        ws.onmessage=e=>{const m=JSON.parse(e.data);if(pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id);}};
        const call=(method,params)=>new Promise(resolve=>{let id=++seq;pending.set(id,resolve);ws.send(JSON.stringify({id,method,params}));});
        const ev=async code=>{const r=(await call('Runtime.evaluate',{expression:code,returnByValue:true})).result;if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result?.value;};
        const until=async code=>{for(let i=0;i<140;i++){if(await ev(code))return;await delay(150);}throw Error('timeout '+code);};
        await call('Page.addScriptToEvaluateOnNewDocument',{source:"localStorage.setItem('language',JSON.stringify('ru'));localStorage.setItem('account_email',JSON.stringify('fixture@example.invalid')); "});
        await call('Page.navigate',{url:origin+'/index.html'});
        await until("window.Lampa && (window.appready || ($('.selector').filter(function(){return $(this).text().trim()==='Русский'}).trigger('hover:enter'),false))");
        await ev("window.testMenus=[];var actualSelect=Lampa.Select.show;Lampa.Select.show=function(o){testMenus.push(o);return actualSelect(o);};");
        await ev(fs.readFileSync('/opt/lampac/mods/SeasonWatch/plugin.js','utf8').replace('{localhost}',origin));
        await until("document.querySelector('[data-action=season_watch] span')?.textContent==='1'");
        await ev("$('[data-action=season_watch]').trigger('hover:enter')");
        await until('testMenus.length===1');
        assert.equal(await ev('testMenus[0].title'),'Обновления сериалов');
        assert(await ev("testMenus[0].items.some(x=>x.subtitle?.includes('S02E04 вышла'))"));
        assert(await ev("testMenus[0].items[0].subtitle.includes('S02E03 доступна')"));
        assert(await ev("testMenus[0].items[0].title.includes('&lt;b&gt;')"));
        await ev('testMenus[0].onSelect(testMenus[0].items[0])');
        assert.equal(await ev("document.querySelector('[data-action=season_watch] span').textContent"),'');
        await ev("testMenus.at(-1).onSelect(testMenus.at(-1).items.find(x=>x.title==='Обновить раздачу'))");
        assert.equal(actions,0);
        await ev("testMenus.at(-1).onSelect(testMenus.at(-1).items.find(x=>x.title==='Отмена'))");
        assert.equal(actions,0);
        await ev("testMenus.at(-1).onSelect(testMenus.at(-1).items.find(x=>x.title==='Обновить раздачу')); testMenus.at(-1).onSelect(testMenus.at(-1).items.find(x=>x.title==='Обновить'))");
        await until("testMenus.at(-1).title==='Обновления сериалов'");
        assert.equal(actions,1);
        console.log('PASS actual Lampa menu, ready/wait statuses, deduplicated count, read badge, HTML escaping, explicit confirmation and cancellation');
    } finally {
        if(ws)ws.close();chrome.kill();server.close();await delay(500);fs.rmSync(profile,{recursive:true,force:true,maxRetries:3});
    }
})().catch(e=>{console.error(e.message);process.exitCode=1;});
