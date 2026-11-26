const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const delay = ms => new Promise(r => setTimeout(r,ms));
(async function () {
    const dir='/opt/lampac/wwwroot/lampa-main';
    let actions = 0, previews = 0, identities = 0;
    const selections=[];
    const deletions=[],gone=new Set();
    let checks=0;
    let infuseMedia=false, incomplete=false;
    const saves=[],batches=[];
    const fixture={items:[],checked:{peer:{time:1}},last:1};
    const example={id:'a'.repeat(32),revision:'b'.repeat(32),label:'Fixture 1080p',title:'Fixture <b>Show</b>',season:2,owned:[1,2],identities:[],choices:[],detail:'',tmdb:3};
    fixture.items.push({...example,place:'peer',ready:[3],waiting:[]},{...example,place:'box',ready:[3],waiting:[]},{...example,tmdb:5,title:'Waiting show',place:'home',ready:[],waiting:[4]});
    const server=http.createServer((q,r)=>{
        const route=new URL(q.url,'http://localhost').pathname;
        if(route==='/season-watch/list') {r.setHeader('Content-Type','application/json');return r.end(JSON.stringify(fixture));}
        if(route==='/season-watch/catalog') {r.setHeader('Content-Type','application/json');return r.end(JSON.stringify({cards:['peer','home','box'].map((place,i)=>({place,hash:String(i).repeat(40),box_id:place==='box'?77:null,identities:place==='peer' && !identities?[{value:'22',label:'Show (2024)'}]:[],title:'Test '+place,label:'1080p',release_summary:'1080p · WEB-DLRip',poster:'',year:'2026',movie:i===1,seasons:i===1?[]:[{n:2,ready:[3],waiting:[],tmdb:3}]})).filter(c=>!gone.has(c.place)),errors:{}}));}
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
        if(route==='/infuseSave.js') {r.setHeader('Content-Type','application/javascript');return fs.createReadStream('/opt/lampac/wwwroot/infuseSave.js').pipe(r);}
        if(route==='/infuse/named' || route==='/torbox/infuse/links') {
            let raw='';q.on('data',c=>raw+=c);q.on('end',()=>{
                const body=JSON.parse(raw);r.setHeader('Content-Type','application/json');
                if(route==='/infuse/named') {
                    saves.push(body.links);
                    return r.end(JSON.stringify({success:true,links:body.links}));
                }
                assert.equal(body.id,77);assert.equal(q.headers['x-torbox-mode'],'file');
                batches.push(body.files);
                const ids=incomplete?body.files.slice(1):body.files;
                r.end(JSON.stringify({success:true,data:ids.slice().reverse().map(id=>({id,url:'https://box.invalid/'+id}))}));
            });return;
        }
        if(route==='/season-watch/media') {
            r.setHeader('Content-Type','application/json');
            if(!infuseMedia) return r.end(JSON.stringify({files:[{id:1,name:'episode.mkv',path:'episode.mkv',url:'https://fixture.invalid/video'}],box_id:77}));
            const place=new URL(q.url,'http://localhost').searchParams.get('place');
            const movie=place==='home'?{title:'Film',id:55,release_date:'2024-01-01'}:{media_type:'tv',name:'Шоу',original_name:'Show',first_air_date:'2025-01-01'};
            const files=Array.from({length:place==='home'?1:9},(_,i)=>({id:i,name:'File '+(i+1)+'.mkv',path:'pack/File '+(i+1)+'.mkv',release_summary:'720p · AVC',season:2,episode:i+1,url:place==='box'?undefined:'https://'+place+'.invalid/'+i}));
            return r.end(JSON.stringify({movie,files,box_id:place==='box'?77:null}));
        }
        if(route==='/torbox/play') {r.setHeader('Content-Type','application/json');return r.end('{"success":true,"url":"https://fixture.invalid/box"}');}
        if(route==='/season-watch/update' || route==='/season-watch/identity') {
            let raw='';q.on('data',c=>raw+=c);q.on('end',()=>{
                const body=JSON.parse(raw);r.setHeader('Content-Type','application/json');
                if(route.endsWith('/identity')){
                    selections.push(body);identities++;return r.end(JSON.stringify({hash:body.hash,message:'TMDB'}));
                }
                if(!body.confirmation){
                    previews++;return r.end(JSON.stringify({offer:{label:'New 1080p',season:2,ready:[3],versions:[{hash:body.hash,label:'Old 1080p'}]},confirmation:'c'.repeat(64)}));
                }
                assert.equal(body.confirmation,'c'.repeat(64));actions++;r.end(JSON.stringify({message:'Раздача обновлена',hash:'abcd'}));
            });return;
        }
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
        await ev(fs.readFileSync(path.join(__dirname,'../../Modules/TorBox/plugin.js'),'utf8').replace('{localhost}',origin));
        await ev(fs.readFileSync(path.join(__dirname,'SeasonWatch/plugin.js'),'utf8').replace('{localhost}',origin));
        await until("document.querySelector('[data-action=all_releases] span')?.textContent==='1'");
        assert(await ev(`['my_torbox','all_releases'].every(action => {
            const item = document.querySelector('.menu [data-action=' + action + ']');
            return item && getComputedStyle(item).display !== 'none';
        })`), 'My TorBox and All Releases must both be visible in the sidebar');
        await ev("$('[data-action=my_torbox]').trigger('hover:enter')");
        await until("Lampa.Activity.active()?.component==='my_torbox'");
        await ev("window.playCalls=[];Lampa.Player.play=function(c){playCalls.push(c.url)}; Lampa.Player.playlist=function(){}");
        await ev("$('[data-action=all_releases]').trigger('hover:enter')");
        await until("$('.all-releases-card').length===3");
        assert(await ev("$('.all-releases-source').text().includes('Домашний TorrServer') && $('.all-releases-source').text().includes('h2nexus-p')"));
        const cardEvent = place => "$('.all-releases-card').filter(function(){return $(this).find('.all-releases-source').text().includes('"+place+"');})[0].dispatchEvent(new Event('hover:enter'))";
        async function openCard(place) {
            await until("$('.all-releases-card .all-releases-source').toArray().some(el=>el.textContent.includes("+JSON.stringify(place)+"))");
            await ev(cardEvent(place));
        }
        await openCard('h2nexus-p');
        await until("testMenus.at(-1)?.items.some(x=>x.title==='Обновить')");
        assert.equal(actions,0);
        await ev("testMenus.at(-1).onSelect(testMenus.at(-1).items.find(x=>x.title==='Смотреть'))");
        await until("playCalls.includes('https://fixture.invalid/video')");
        await openCard('TorBox');
        await ev("testMenus.at(-1).onSelect(testMenus.at(-1).items.find(x=>x.title==='Смотреть'))");
        await until("playCalls.includes('https://fixture.invalid/box')");
        await openCard('h2nexus-p');
        await ev("testMenus.at(-1).onSelect(testMenus.at(-1).items.find(x=>x.title==='Обновить'))");
        const choose=label=>ev("testMenus.at(-1).onSelect(testMenus.at(-1).items.find(x=>x.title==="+JSON.stringify(label)+"))");
        await until("testMenus.at(-1).items.some(x=>x.title==='Подтвердить замену')");
        assert.equal(actions,0);
        assert(await ev("testMenus.at(-1).items[1].subtitle.includes('Old 1080p') && testMenus.at(-1).items[1].subtitle.includes('S02E03')"));
        await choose('Отмена');assert.equal(actions,0);
        await choose('Выбрать сериал в TMDB');await choose('Show (2024)');
        for(let i=0;i<30&&!identities;i++)await delay(100);
        assert.equal(identities,1);await delay(300);
        assert.equal(selections[0].identity,22);
        await ev(fs.readFileSync(path.join(__dirname,'../../Modules/TorBox/plugin.js'),'utf8').replaceAll('{localhost}',origin));
        await ev("window.oldEdit=Lampa.Input.edit;window.oldGet=Lampa.Api.sources.tmdb.get;Lampa.Input.edit=(o,done)=>done('Fixture');Lampa.Api.sources.tmdb.get=(path,params,done)=>done({results:path==='search/movie'?[{id:43,title:'Fixture',original_title:'Fixture',release_date:'2024-01-01',poster_path:'/cover.jpg'}]:[]});");
        await openCard('Домашний');await choose('Уточнить фильм или сериал');
        await until("testMenus.at(-1).items.some(x=>x.title==='Fixture (2024)')");
        await choose('Fixture (2024)');
        for(let i=0;i<30&&selections.length<2;i++)await delay(100);
        assert.deepEqual(selections[1],{place:'home',hash:'1'.repeat(40),identity:43,media_type:'movie'});
        await ev("Lampa.Input.edit=oldEdit;Lampa.Api.sources.tmdb.get=oldGet");
        await delay(300);
        await openCard('h2nexus-p');await choose('Обновить');
        await until("testMenus.at(-1).items.some(x=>x.title==='Подтвердить замену')");
        await choose('Подтвердить замену');assert.equal(previews,2);
        await until("$('.all-releases-card').length===3");
        for(let i=0;i<30&&!actions;i++)await delay(100);
        if(!actions) console.log(await ev("({notes,active:Lampa.Activity.active()?.component,menus:testMenus.map(x=>x.title),urls:performance.getEntriesByType('resource').filter(x=>x.name.includes('season-watch')).map(x=>new URL(x.name).pathname)})"));
        assert.equal(actions,1);
        const click=label=>ev("testMenus.at(-1).onSelect(testMenus.at(-1).items.find(x=>x.title==="+JSON.stringify(label)+"))");
        const sites=[['h2nexus-p','peer'],['TorBox','box'],['Домашний','home']];
        infuseMedia=true;
        const saveMode=label=>ev("testMenus.at(-1).onSelect(testMenus.at(-1).items.find(x=>$(x.title).text().trim()==="+JSON.stringify(label)+"))");
        for(const [name,place] of sites) {
            await openCard(name);await click('Сохранить в Infuse');
            if(place!=='home'){
                await until("testMenus.at(-1).items.some(x=>x.title==='Все видеофайлы')");
                await click(place==='box'?'Все видеофайлы':'File 9.mkv');
            }
            await until("testMenus.at(-1).items.some(x=>x.title.includes('infuseSaver'))");
            const labels=await ev("testMenus.at(-1).items.map(x=>$(x.title).text().trim())");
            assert(labels.includes('Редактировать и сохранить'),JSON.stringify(labels));
            const metadata=await ev("testMenus.at(-1).items[0].subtitle");
            assert.equal(metadata,place==='box'?'1080p · WEB-DLRip':'720p · AVC');
            const count=saves.length;
            await saveMode(place==='box'?'Сохранить все':'Сохранить');
            for(let n=0;n<100 && saves.length===count;n++)await delay(50);
            assert.equal(saves.length,count+1);
            const saved=saves[count];
            assert.equal(saved.length,place==='box'?9:1);
            saved.forEach((row,i)=>{
                const id=place==='peer'?8:i;
                assert.equal(row.url,'https://'+place+'.invalid/'+id);
                assert.equal(row.filename,place==='home'?'Film (2024) {tmdb-55}.mkv':'Show (2025) S02E0'+(id+1)+'.mkv');
            });
        }
        assert.deepEqual(batches.map(b=>b.length),[8,1]);
        incomplete=true;await openCard('TorBox');await click('Сохранить в Infuse');
        await until("testMenus.at(-1).items.some(x=>x.title==='Все видеофайлы')");
        await click('Все видеофайлы');await saveMode('Сохранить все');
        await until("notes.some(s=>s.includes('не вернул ссылки'))");
        assert.equal(saves.length,3);
        await openCard('h2nexus-p');await click('Сохранить в Infuse');
        await until("testMenus.at(-1).items.some(x=>x.title==='Все видеофайлы')");
        await click('Все видеофайлы');await saveMode('Редактировать и сохранить');
        await until("$('.its-links-layer [data-action=toggle]').length===9");
        await ev("$('.its-links-layer [data-action=toggle]').first().trigger('hover:enter')");
        await ev("$('.its-links-layer [data-action=save]').first().trigger('hover:enter')");
        for(let n=0;n<100 && saves.length===3;n++)await delay(50);
        assert.equal(saves.length,4);assert.equal(saves[3].length,8);
        assert(!saves[3].some(f=>f.url==='https://peer.invalid/0'));
        for (let i=0;i<sites.length;i++){
            await until("$('.all-releases-card .all-releases-source').length==="+(3-i));
            await openCard(sites[i][0]);await click('Удалить');
            assert(await ev("testMenus.at(-1).title.includes('Удалить все версии') && testMenus.at(-1).title.includes("+JSON.stringify(sites[i][0])+")"));
            assert.equal(await ev("testMenus.at(-1).items[0].title"),'Отмена');
            await click('Отмена');assert.equal(deletions.length,i);
            await click('Удалить');await click('Удалить');
            await until("$('.all-releases-card .all-releases-source').length==="+(2-i));
            const req=deletions[i];assert.equal(req.place,sites[i][1]);assert.equal(req.confirmed,true);
            assert.equal(req.box_id,req.place==='box'?77:null);
        }
        assert.equal(deletions.length,3);assert.equal(checks,1);
        console.log('PASS My TorBox and All Releases sidebar entries, InfuseSaver, playback, updates and deletion');
    } finally {
        if(ws)ws.close();chrome.kill();server.close();await delay(500);fs.rmSync(profile,{recursive:true,force:true,maxRetries:3});
    }
})().catch(e=>{console.error(e.message);process.exitCode=1;});
