(function () {
    'use strict';
    if (window.allReleasesInstalled) return;
    window.allReleasesInstalled = true;
    var base = '{localhost}', title = 'Все раздачи', entry, badge;
    var script = (document.currentScript || {}).src || '';
    var token = /[?&]token=([^&#]+)/.exec(script);
    var names = {home:'Домашний TorrServer',peer:'TorrServer h2nexus-p',box:'TorBox'};
    function store(k) { return Lampa.Storage.get(k,'') || ''; }
    function account() { return [base,store('account_email'),store('lampac_unic_id'),token && token[1]].join('|'); }
    function url(path) {
        var u=base+'/'+path;
        [['account_email',store('account_email')],['uid',store('lampac_unic_id')],['token',token && decodeURIComponent(token[1])]].forEach(function (kv) {
            if (kv[1]) u=Lampa.Utils.addUrlComponent(u,kv[0]+'='+encodeURIComponent(kv[1]));
        });
        return u;
    }
    function safe(s) { return $('<span>').text(String(s||'')).html(); }
    function request(path,body,ok,fail) {
        var who=account(), address=url(path);
        function send(headers) {
            $.ajax({url:address,method:body?'POST':'GET',dataType:'json',contentType:body?'application/json':undefined,
                timeout:245000,headers:headers,data:body?JSON.stringify(body):undefined}).done(function (result) {
                if (who===account()) ok(result);
            }).fail(function (xhr) {
                if (who!==account()) return;
                if (fail) fail((xhr.responseJSON||{}).detail || 'Источник временно недоступен');
            });
        }
        if (path==='torbox/play' && window.lampac_torbox_headers) window.lampac_torbox_headers(address,send);
        else send(body?{'X-Season-Watch':'1'}:{});
    }
    function message(txt) { Lampa.Noty.show(safe(txt)); }
    function select(label,rows,back) {
        Lampa.Select.show({title:safe(label),items:rows,onSelect:function (row) {
            Lampa.Select.hide(); row.run();
        },onBack:function () { Lampa.Select.hide(); back(); }});
    }
    function number(s,n) { return 'S'+('0'+s).slice(-Math.max(2,String(s).length))+'E'+('0'+n).slice(-Math.max(2,String(n).length)); }
    function seenKey() { return 'season_watch_seen_'+Lampa.Utils.hash(account()); }
    function eventKey(w,n) {return (w.tmdb||w.id)+'/'+w.season+'/'+n;}
    var notices=[];
    function drawBadge() {
        var read=Lampa.Storage.get(seenKey(),{})||{}, keys={};
        notices.forEach(function (w) { w.ready.forEach(function (n) { keys[eventKey(w,n)]=true; }); });
        var n=Object.keys(keys).filter(function (k) {return !read[k];}).length;
        badge.text(n||'').toggle(!!n);
    }
    function refreshBadge() { request('season-watch/list',null,function (d) {notices=d.items||[];drawBadge();},function () {badge.hide();}); }
    function grid() { Lampa.Activity.push({component:'all_releases',title:title,page:1}); }
    function library(object) {
        Lampa.InteractionCategory.call(this,object);
        var self=this,alive=true,destroy=this.destroy,owner=account(),removing={},timers={};
        function valid() { return alive && owner===account(); }
        function current() { var a=Lampa.Activity.active();return valid() && a && a.component==='all_releases'; }
        function back() { if (current()) Lampa.Controller.toggle('content'); }
        function error(s) { if(current()) {message(s);back();} }
        function edit(c,season,choice) {
            if (!current()) return;
            message('Проверяю раздачу');
            request('season-watch/update',{place:c.place,hash:c.hash,season:season||0,choice:choice||''},function (r) {
                if (!current()) return;
                if (r.seasons) return select('Выберите сезон',r.seasons.map(function (s) {
                    return {title:'Сезон '+s,run:function () {edit(c,s,'');}};
                }),back);
                if (r.choices) return select('Выберите раздачу для обновления',r.choices.map(function (item) {
                    return {title:safe(item.label),run:function () {edit(c,season,item.value);}};
                }),back);
                message(r.message);
                if (r.hash) {
                    refreshBadge(); Lampa.Activity.replace({component:'all_releases',title:title,page:1});
                } else back();
            },error);
        }
        function play(c) {
            request('season-watch/media?place='+encodeURIComponent(c.place)+'&hash='+c.hash,null,function (r) {
                if (!current()) return;
                var files=(r.files||[]).sort(function (a,b) {return a.path.localeCompare(b.path,undefined,{numeric:true});});
                if (!files.length) return error('В раздаче нет видеофайлов');
                function launch(f) {
                    function open(address) {
                        if (!current()) return;
                        var cell={url:address,title:f.name,torrent_hash:c.hash,timeline:false};
                        Lampa.Player.play(cell);
                        Lampa.Player.playlist(c.place==='box'?[cell]:files.map(function (x) {
                            return x===f?cell:{url:x.url,title:x.name,torrent_hash:c.hash,timeline:false};
                        }));
                    }
                    if (c.place==='box') request('torbox/play',{id:r.box_id,file:f.id},function (v) {
                        if (v.url) open(v.url);else error(v.detail||'TorBox не вернул ссылку');
                    },error);
                    else open(f.url);
                }
                if (files.length===1) return launch(files[0]);
                select('Выберите файл',files.map(function (f) {return {title:safe(f.name),subtitle:safe(f.path!==f.name?f.path.slice(0,-f.name.length):''),run:function () {launch(f);}};}),back);
            },error);
        }
        function reload() {
            if (current()) Lampa.Activity.replace({component:'all_releases',title:title,page:1});
        }
        function followRemoval(c,due) {
            var k=c.place+':'+c.hash;
            removing[k]=true;
            clearTimeout(timers[k]);
            timers[k]=setTimeout(function () {
                if (!valid()) return;
                request('season-watch/remove/check',{place:c.place,hash:c.hash},function (r) {
                    if (!valid()) return;
                    if (r.pending) return followRemoval(c,r.due);
                    delete removing[k];
                    refreshBadge();
                    if (current()) {message(r.message);reload();}
                },function () {
                    if (current()) followRemoval(c,Date.now()/1000+30);
                });
            }, Math.max(1000,(due||0)*1000-Date.now()));
        }
        function confirmRemoval(c) {
            var k=c.place+':'+c.hash;
            if (!current()) return;
            if (removing[k]) return message('Удаление уже проверяется');
            var source=c.place==='box'?'Общий аккаунт TorBox':names[c.place];
            select('Удалить «'+c.title+'»? · '+source,[
                {title:'Отмена',run:function () {if (current()) actions(c);}},
                {title:'Удалить',subtitle:safe(c.label+' · '+names[c.place]+' · вся раздача со всеми файлами'),run:function () {
                    if (!current() || removing[k]) return;
                    removing[k]=true;
                    request('season-watch/remove',{place:c.place,hash:c.hash,box_id:c.place==='box'?c.box_id:null,confirmed:true},function (r) {
                        if (!valid()) return;
                        if (current()) message(r.message);
                        if (r.pending) { followRemoval(c,r.due); back(); }
                        else { delete removing[k];refreshBadge();reload(); }
                    },function (s) {delete removing[k];error(s);});
                }}
            ],function () {if (current()) actions(c);});
        }
        function actions(c) {
            var read=Lampa.Storage.get(seenKey(),{})||{};
            (c.seasons||[]).forEach(function (s) {s.ready.forEach(function (n) {
                read[eventKey({tmdb:s.tmdb,id:s.id,season:s.n},n)]=true;
            });}); Lampa.Storage.set(seenKey(),read);drawBadge();
            var rows=[{title:'Смотреть',subtitle:safe(c.label),run:function () {play(c);}},
                {title:'Обновить',subtitle:names[c.place],run:function () {edit(c,0,'');}}];
            if(c.place==='box') rows.push({title:'Управление TorBox',run:function () {
                Lampa.Activity.push({component:'my_torbox',title:'Мой TorBox',page:1});
            }});
            rows.push({title:'Удалить',subtitle:names[c.place],run:function () {confirmRemoval(c);}});
            select(c.title,rows,back);
        }
        this.create=function () {
            self.activity.loader(true);
            request('season-watch/catalog',null,function (d) {
                if (!valid()) return;
                (d.pending||[]).forEach(function (r) {followRemoval(r,r.due);});
                var cards=d.cards||[];
                function score(c) {return c.seasons.some(function (s){return s.ready.length;})?0:c.seasons.some(function (s){return s.waiting.length;})?1:2;}
                cards.sort(function (a,b) {return score(a)-score(b)||a.title.localeCompare(b.title)||a.place.localeCompare(b.place)||a.label.localeCompare(b.label);});
                var results=cards.map(function (c) {return {id:c.place+'-'+c.hash,title:safe(c.title),original_title:safe(c.title),poster_path:c.poster,release_date:c.year?c.year+'-01-01':'',item:c};});
                if (!results.length) results.push({id:'empty',title:'Раздач пока нет'});
                self.build({results:results,total_pages:1});
                Object.keys(d.errors||{}).forEach(function (k) {message(names[k]+': '+d.errors[k]);});
            },function (s) {if(valid()) {self.build({results:[{id:'retry',title:'Повторить загрузку'}],total_pages:1});message(s);}});
        };
        this.next=function () {};
        this.cardRender=function (params,data,card) {
            var node=card.render(),c=data.item;
            node.addClass('all-releases-card');
            if (c) {
                var q=/(?:2160|1080|720|480)[pр]|4K/i.exec(c.label);
                var info=[names[c.place],q && q[0]].filter(Boolean);
                (c.seasons||[]).forEach(function (s) {
                    if (s.ready.length) info.push('Доступна '+s.ready.map(function (n) {return number(s.n,n);}).join(', '));
                    else if(s.waiting.length) info.push('Вышла '+s.waiting.map(function (n) {return number(s.n,n);}).join(', '));
                });
                $('<div class="all-releases-source">').text(info.join(' · ')).appendTo(node);
                if (!c.poster) $('<div class="all-releases-caption">').text(c.title).appendTo(node.find('.card__view'));
            }
            card.onEnter=card.onMenu=function () {if (c) actions(c);else grid();};
        };
        this.destroy=function () {alive=false;Object.keys(timers).forEach(function (k) {clearTimeout(timers[k]);});destroy.call(self);};
    }
    function start() {
        if (entry) return;
        Lampa.Component.add('all_releases',library);
        $('<style>').text('.menu [data-action="my_torbox"]{display:none!important}.all-releases-source{font-size:.85em;opacity:.85;margin-top:.25em}.all-releases-caption{position:absolute;left:.7em;right:.7em;top:45%;text-align:center;overflow-wrap:anywhere}').appendTo('head');
        entry=$('<li class="menu__item selector" data-action="all_releases"><div class="menu__ico">▤</div><div class="menu__text"></div></li>');
        entry.find('.menu__text').text(title);
        badge=$('<span style="margin-left:.5em"></span>').hide().appendTo(entry);
        entry.on('hover:enter',grid);$('.menu .menu__list').eq(0).append(entry);
        refreshBadge();window.setInterval(function () {if (!document.hidden) refreshBadge();},600000);
        document.addEventListener('visibilitychange',function () {if(!document.hidden) refreshBadge();});
    }
    if (window.appready) start();else Lampa.Listener.follow('app',function (e) {if(e.type==='ready') start();});
})();
