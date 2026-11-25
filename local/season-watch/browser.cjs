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
    const deletions=[],gone=new Set();
    let checks=0;
    const fixture={items:[],checked:{peer:{time:1}},last:1};
    const example={id:'a'.repeat(32),revision:'b'.repeat(32),label:'Fixture 1080p',title:'Fixture <b>Show</b>',season:2,owned:[1,2],identities:[],choices:[],detail:'',tmdb:3};
    fixture.items.push({...example,place:'peer',ready:[3],waiting:[]},{...example,place:'box',ready:[3],waiting:[]},{...example,tmdb:5,title:'Waiting show',place:'home',ready:[],waiting:[4]});
    const server=http.createServer((q,r)=>{
        const route=new URL(q.url,'http://localhost').pathname;
        if(route==='/season-watch/list') {r.setHeader('Content-Type','application/json');return r.end(JSON.stringify(fixture));}
        if(route==='/season-watch/catalog') {r.setHeader('Content-Type','application/json');return r.end(JSON.stringify({cards:['peer','home','box'].map((place,i)=>({place,hash:String(i).repeat(40),box_id:place==='box'?77:null,title:'Test '+place,label:'1080p',poster:'',year:'2026',movie:i===1,seasons:i===1?[]:[{n:2,ready:[3],waiting:[],tmdb:3}]})).filter(c=>!gone.has(c.place)),errors:{}}));}
        if(route==='/season-watch/remove' || route==='/season-watch/remove/check') {
            let data='';q.on('data',c=>data+=c);q.on('end',()=>{
                const body=JSON.parse(data);r.setHeader('Content-Type','application/json');
                if(route==='/season-watch/remove'){
                    deletions.push(body);
                    if(body.place==='box')return r.end(JSON.stringify({pending:true,due:Math.floor(Date.now()/1000),message:'Ожидается подтверждение удаления'}));
                }else checks++;
                gone.add(body.place);r.end(JSON.stringify({pending:false,confirmed:true,message:'Раздача удалена'}));
            });return;
        }
        if(route==='/season-watch/media') {r.setHeader('Content-Type','application/json');return r.end(JSON.stringify({files:[{id:1,name:'episode.mkv',path:'episode.mkv',url:'https://fixture.invalid/video'}],box_id:5}));}
        if(route==='/torbox/play') {r.setHeader('Content-Type','application/json');return r.end('{"success":true,"url":"https://fixture.invalid/box"}');}
        if(route==='/season-watch/update') {actions++;r.setHeader('Content-Type','application/json');return r.end('{"message":"Раздача обновлена","hash":"abcd"}');}
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
        await until("Lampa.Activity.active()?.component==='main'");
        await delay(1800);
        await ev("window.testMenus=[];window.notes=[];var origNoty=Lampa.Noty.show;Lampa.Noty.show=function(m){notes.push(m);return origNoty(m);};var actualSelect=Lampa.Select.show;Lampa.Select.show=function(o){testMenus.push(o);return actualSelect(o);};");
        await ev(fs.readFileSync(path.join(__dirname,'SeasonWatch/plugin.js'),'utf8').replace('{localhost}',origin));
        await until("document.querySelector('[data-action=all_releases] span')?.textContent==='1'");
        await ev("window.playCalls=[];Lampa.Player.play=function(c){playCalls.push(c.url)}; Lampa.Player.playlist=function(){}");
        await ev("$('[data-action=all_releases]').trigger('hover:enter')");
        await until("$('.all-releases-card').length===3");
        assert(await ev("$('.all-releases-source').text().includes('Домашний TorrServer') && $('.all-releases-source').text().includes('h2nexus-p')"));
        const card = place => "$('.all-releases-card').filter(function(){return $(this).find('.all-releases-source').text().includes('"+place+"');})[0].dispatchEvent(new Event('hover:enter'))";
        await ev(card('h2nexus-p'));
        await until("testMenus.at(-1)?.items.some(x=>x.title==='Обновить')");
        assert.equal(actions,0);
        await ev("testMenus.at(-1).onSelect(testMenus.at(-1).items.find(x=>x.title==='Смотреть'))");
        await until("playCalls.includes('https://fixture.invalid/video')");
        await ev(card('TorBox'));
        await ev("testMenus.at(-1).onSelect(testMenus.at(-1).items.find(x=>x.title==='Смотреть'))");
        await until("playCalls.includes('https://fixture.invalid/box')");
        await ev(card('h2nexus-p'));
        await ev("testMenus.at(-1).onSelect(testMenus.at(-1).items.find(x=>x.title==='Обновить'))");
        await until("$('.all-releases-card').length===3");
        for(let i=0;i<30&&!actions;i++)await delay(100);
        if(!actions) console.log(await ev("({notes,active:Lampa.Activity.active()?.component,menus:testMenus.map(x=>x.title),urls:performance.getEntriesByType('resource').filter(x=>x.name.includes('season-watch')).map(x=>new URL(x.name).pathname)})"));
        assert.equal(actions,1);
        const click=label=>ev("testMenus.at(-1).onSelect(testMenus.at(-1).items.find(x=>x.title==="+JSON.stringify(label)+"))");
        const sites=[['h2nexus-p','peer'],['TorBox','box'],['Домашний','home']];
        for (let i=0;i<sites.length;i++){
            await until("$('.all-releases-card .all-releases-source').length==="+(3-i));
            await ev(card(sites[i][0]));await click('Удалить');
            assert(await ev("testMenus.at(-1).title.includes("+JSON.stringify(sites[i][0])+")"));
            assert.equal(await ev("testMenus.at(-1).items[0].title"),'Отмена');
            await click('Отмена');assert.equal(deletions.length,i);
            await click('Удалить');await click('Удалить');
            await until("$('.all-releases-card .all-releases-source').length==="+(2-i));
            const req=deletions[i];assert.equal(req.place,sites[i][1]);assert.equal(req.confirmed,true);
            assert.equal(req.box_id,req.place==='box'?77:null);
        }
        assert.equal(deletions.length,3);assert.equal(checks,1);
        console.log('PASS native Lampa card playback/update, cancel and confirmed deletion from each source, deferred TorBox verification');
    } finally {
        if(ws)ws.close();chrome.kill();server.close();await delay(500);fs.rmSync(profile,{recursive:true,force:true,maxRetries:3});
    }
})().catch(e=>{console.error(e.message);process.exitCode=1;});
