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
        if (path==='torbox/infuse/links') send({'X-TorBox-Mode':'file'});
        else if (path==='torbox/play' && window.lampac_torbox_headers) window.lampac_torbox_headers(address,send);
        else send(body?{'X-Season-Watch':'1'}:{});
    }
    function message(txt) { Lampa.Noty.show(safe(txt)); }
    function select(label,rows,back) {
        Lampa.Select.show({title:safe(label),items:rows,onSelect:function (row) {
            Lampa.Select.hide(); row.run();
        },onBack:function () { Lampa.Select.hide(); back(); }});
    }
    function number(s,n) { return 'S'+('0'+s).slice(-Math.max(2,String(s).length))+'E'+('0'+n).slice(-Math.max(2,String(n).length)); }
    function pairLabel(f) {
        return f.episodes && f.episodes.length===2 ? f.season+' сезон · серии '+f.episodes.join('–') : '';
    }
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
    var nextCatalog=null;
    function library(object) {
        Lampa.InteractionCategory.call(this,object);
        var self=this,alive=true,destroy=this.destroy,owner=account(),removing={},timers={};
        function valid() { return alive && owner===account(); }
        function current() { var a=Lampa.Activity.active();return valid() && a && a.component==='all_releases' && (!a.activity || !a.activity.component || a.activity.component===self); }
        function back() { if (current()) Lampa.Controller.toggle('content'); }
        function error(s) { if(current()) {message(s);back();} }
        function edit(c,season,choice,confirmation,keepBoth) {
            if (!current()) return;
            message('Проверяю раздачу');
            request('season-watch/update',{place:c.place,hash:c.hash,season:season||0,choice:choice||'',confirmation:confirmation||'',keep_both:!!keepBoth},function (r) {
                if (!current()) return;
                if (r.seasons) return select('Выберите сезон',r.seasons.map(function (s) {
                    return {title:'Сезон '+s,run:function () {edit(c,s,'');}};
                }),back);
                if (r.choices) return select('Выберите раздачу для обновления',r.choices.map(function (item) {
                    return {title:safe(item.label),run:function () {edit(c,season,item.value);}};
                }),back);
                if (r.offer) {
                    var offer=r.offer;
                    var episodes=offer.ready.map(function (n) {return number(offer.season,n);}).join(', ');
                    var versions=offer.versions.map(function (v) {return safe(v.label)+' ['+safe(v.hash.slice(0,8))+']';}).join('<br>');
                    function preview() {
                        var menu=[{title:'Отмена',run:function () {actions(c);}}];
                        var delta=offer.files;
                        if (delta) {
                            [['added','Добавятся'],['removed','Исчезнут'],['changed','Изменится размер']].forEach(function (field) {
                                var paths=delta[field[0]]||[];
                                if (paths.length) menu.push({title:field[1]+' · '+paths.length,run:function () {
                                    select(field[1],paths.map(function (path) {
                                        return {title:safe(path),run:preview};
                                    }),preview);
                                }});
                            });
                        }
                        var unknown=(offer.unknown_files||[]).map(safe);
                        if (r.keep_confirmation) menu.push({title:'Сохранить обе версии',
                            subtitle:safe(offer.label),run:function () {edit(c,offer.season,choice,r.keep_confirmation,true);}});
                        menu.push({title:offer.retry?'Повторить очистку':'Подтвердить замену',
                            subtitle:safe(offer.label)+(episodes?'<br>'+episodes:'')+'<br>Будут удалены:<br>'+versions+
                                (unknown.length?'<br>Список файлов недоступен: '+unknown.join(', '):''),
                            run:function () {edit(c,offer.season,choice,r.confirmation,false);}});
                        select('Подтвердить обновление · '+names[c.place],menu,function () {actions(c);});
                    }
                    return preview();
                }
                message(r.message);
                if (r.hash) {
                    refreshBadge(); Lampa.Activity.replace({component:'all_releases',title:title,page:1});
                } else back();
            },error);
        }
        function withMedia(c,ready) {
            request('season-watch/media?place='+encodeURIComponent(c.place)+'&hash='+c.hash,null,function (r) {
                if (!current()) return;
                var files=(r.files||[]).sort(function (a,b) {return a.path.localeCompare(b.path,undefined,{numeric:true});});
                if (!files.length) return error('В раздаче нет видеофайлов');
                ready(files,r);
            },error);
        }
        function play(c) {
            var metrics=window.lampacPlaybackMetrics;
            withMedia(c,function (files,r) {
                var offered=metrics && metrics.choosing();
                function launch(f,n) {
                    var attempt=metrics && metrics.begin(offered);
                    function open(address) {
                        if (!current()) return;
                        var cell={url:address,title:n?number(f.season,n)+' · '+f.name:f.name,torrent_hash:c.hash,
                            timeline:c.place==='box' && window.lampac_torbox_file_timeline ? window.lampac_torbox_file_timeline(c.hash,f.id) : false};
                        if (attempt) metrics.resolved(attempt,cell);
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
                var rows=[];
                files.forEach(function (f) {
                    var pair=pairLabel(f);
                    if (pair) {
                        f.episodes.forEach(function (n) {
                            rows.push({title:number(f.season,n),subtitle:pair+' · '+safe(f.name),run:function () {launch(f,n);}});
                        });
                    } else rows.push({title:safe(f.name),subtitle:safe([f.episode_note,f.path!==f.name?f.path.slice(0,-f.name.length):''].filter(Boolean).join(' · ')),run:function () {launch(f);}});
                });
                if (rows.length===1 && !files[0].episode_note) return launch(files[0]);
                select('Выберите файл',rows,back);
            });
        }
        var infuseVisit=0;
        function infuse(c) {
            var visit=++infuseVisit;
            function active() {return visit===infuseVisit && current();}
            function close() {++infuseVisit;if (current()) actions(c);}
            withMedia(c,function (files,r) {
                if (!active()) return;
                if (c.place==='box' && (r.box_id==null || c.box_id!==r.box_id)) return error('Карточка TorBox изменилась. Обновите список раздач');
                function resolve(wanted,done) {
                    if (!active()) return;
                    if (c.place!=='box') return done();
                    message('Подготавливаю ссылки TorBox для Infuse');
                    function batch(offset) {
                        if (!active()) return;
                        var chunk=wanted.slice(offset,offset+8);
                        if (!chunk.length) return done();
                        request('torbox/infuse/links',{id:r.box_id,files:chunk.map(function (f) {return f.id;})},function (reply) {
                            if (!active()) return;
                            var links=Array.isArray(reply.data)?reply.data:[];
                            var complete=chunk.every(function (f) {
                                var match=links.find(function (l) {return l.id===f.id;});
                                if (!match || !/^https:\/\//i.test(match.url||'')) return false;
                                f.url=match.url;
                                if (match.episode>0 && match.season!=null) {f.season=match.season;f.episode=match.episode;}
                                return true;
                            });
                            if (!complete) return error(reply.detail||'TorBox не вернул ссылки для всех выбранных файлов');
                            batch(offset+chunk.length);
                        },function (s) {if (active()) error(s);});
                    }
                    batch(0);
                }
                function chooseFile() {
                    if (!active()) return;
                    var saver=window.lampac_infuse_saver;
                    if (!saver || !saver.menu) return error('Не удалось загрузить InfuseSaver');
                    function modes(file,all) {
                        if (!active()) return;
                        var menu=saver.menu({element:file,items:files,params:{movie:r.movie},resolve:resolve});
                        if (all) menu=menu.filter(function (item) {return item.infuse_scope!=='single';});
                        select('Infuse · '+(all?c.title:file.name),menu.map(function (item) {
                            return {title:item.title,subtitle:safe((all?c.release_summary:file.release_summary)||''),run:function () {if (active()) {back();item.onSelect();}}};
                        }),files.length===1?close:chooseFile);
                    }
                    if (files.length===1 && !files[0].episode_note) return modes(files[0],false);
                    var rows=[{title:'Все видеофайлы',run:function () {modes(files[0],true);}}];
                    files.forEach(function (f) {rows.push({title:safe(f.name),subtitle:safe([pairLabel(f),f.episode_note,f.path!==f.name?f.path:'',f.release_summary].filter(Boolean).join(' · ')),run:function () {modes(f,false);}});});
                    select('Infuse · '+c.title,rows,close);
                }
                if (window.lampac_infuse_saver && window.lampac_infuse_saver.menu) chooseFile();
                else Lampa.Utils.putScriptAsync([url('infuseSave.js')],chooseFile,function () {if(active()) error('Не удалось загрузить InfuseSaver');});
            });
        }
        function reload() {
            if (current()) Lampa.Activity.replace({component:'all_releases',title:title,page:1});
        }
        function followRemoval(c,due,attempt) {
            var k=c.place+':'+c.hash;
            attempt=attempt||0;
            removing[k]=true;
            clearTimeout(timers[k]);
            if (attempt>=12) {if (current()) message('Проверка удаления продолжится в фоне');return;}
            timers[k]=setTimeout(function () {
                if (!valid()) return;
                request('season-watch/remove/check',{place:c.place,hash:c.hash},function (r) {
                    if (!valid()) return;
                    if (r.pending) return followRemoval(c,r.due,attempt+1);
                    delete removing[k];
                    refreshBadge();
                    if (current()) {message(r.message);reload();}
                },function () {
                    if (current()) followRemoval(c,Date.now()/1000+30,attempt+1);
                });
            }, Math.max(1000,(due||0)*1000-Date.now()));
        }
        function confirmRemoval(c) {
            var k=c.place+':'+c.hash;
            if (!current()) return;
            if (removing[k]) return message('Удаление уже проверяется');
            var source=c.place==='box'?'Общий аккаунт TorBox':names[c.place];
            select('Удалить все версии «'+c.title+'»? · '+source,[
                {title:'Отмена',run:function () {if (current()) actions(c);}},
                {title:'Удалить',subtitle:safe(c.label+' · '+names[c.place]+' · текущая и предыдущие версии этой раздачи'),run:function () {
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
        function saveFilm(c,type,id) {
            request('season-watch/identity',{place:c.place,hash:c.hash,identity:id,media_type:type},function (r) {
                if (current()) {message(r.message);refreshBadge();reload();}
            },error);
        }
        function clarify(c) {
            function choose() {
                if (!current()) return;
                if (!window.lampac_choose_film) return error('Обновите плагины Lampa и повторите поиск');
                window.lampac_choose_film({name:c.label,film:{title:c.title,media_type:c.tv?'tv':c.movie?'movie':''}},
                    reload,function () {if (current()) actions(c);},current,function (film) {
                        saveFilm(c,film.media_type,film.tmdb_id);
                    });
            }
            if (window.lampac_choose_film) choose();
            else Lampa.Utils.putScriptAsync([url('torbox.js')],choose,function () {if (current()) error('Поиск TMDB недоступен');});
        }
        function actions(c) {
            ++infuseVisit;
            var read=Lampa.Storage.get(seenKey(),{})||{};
            (c.seasons||[]).forEach(function (s) {s.ready.forEach(function (n) {
                read[eventKey({tmdb:s.tmdb,id:s.id,season:s.n},n)]=true;
            });}); Lampa.Storage.set(seenKey(),read);drawBadge();
            var rows=[{title:'Смотреть',subtitle:safe(c.release_summary || c.label),run:function () {play(c);}},
                {title:'Сохранить в Infuse',subtitle:safe(c.release_summary || names[c.place]),run:function () {infuse(c);}},
                {title:'Обновить',subtitle:names[c.place]+' · просмотр файлов и выбор версий для сохранения',run:function () {edit(c,0,'');}}];
            rows.push({title:'Уточнить фильм или сериал',run:function () {clarify(c);}});
            if (c.identities && c.identities.length) rows.push({title:'Выбрать сериал в TMDB',run:function () {
                select('Выберите сериал в TMDB',c.identities.map(function (candidate) {
                    return {title:safe(candidate.label),run:function () {
                        saveFilm(c,'tv',Number(candidate.value));
                    }};
                }),function () {actions(c);});
            }});
            if(c.place==='box') rows.push({title:'Управление TorBox',run:function () {
                Lampa.Activity.push({component:'my_torbox',title:'Мой TorBox',page:1});
            }});
            rows.push({title:'Удалить',subtitle:names[c.place],run:function () {confirmRemoval(c);}});
            select(c.title,rows,back);
        }
        function catalogKey(d) {
            return JSON.stringify([d.cards,d.errors,d.refreshing]);
        }
        function refreshSources(d) {
            if (!(d.refreshing||[]).length || !valid()) return;
            timers.catalog=setTimeout(function () {
                request('season-watch/catalog',null,function (fresh) {
                    if (!valid()) return;
                    if (catalogKey(d)===catalogKey(fresh)) return refreshSources(fresh);
                    function show() {
                        if (!valid()) return;
                        if (!current() || Lampa.Controller.enabled().name!=='content') {
                            timers.catalog=setTimeout(show,750);
                            return;
                        }
                        var focused=self.render().find('.all-releases-card.focus').attr('data-release-key');
                        nextCatalog={owner:owner,data:fresh,focus:focused};
                        reload();
                    }
                    show();
                },function () { if (valid()) timers.catalog=setTimeout(function () {refreshSources(d);},2500); });
            },1200);
        }
        this.create=function () {
            self.activity.loader(true);
            var saved=nextCatalog;
            nextCatalog=null;
            function draw(d) {
                if (!valid()) return;
                (d.pending||[]).forEach(function (r) {followRemoval(r,r.due);});
                var cards=(d.cards||[]).slice();
                function score(c) {return c.seasons.some(function (s){return s.ready.length;})?0:c.seasons.some(function (s){return s.waiting.length;})?1:2;}
                cards.sort(function (a,b) {return score(a)-score(b)||a.title.localeCompare(b.title)||a.place.localeCompare(b.place)||a.label.localeCompare(b.label);});
                var results=cards.map(function (c) {return {id:c.place+'-'+c.hash,title:safe(c.title),original_title:safe(c.title),poster_path:/^https?:/.test(c.poster)?'':c.poster,img:/^https?:/.test(c.poster)?c.poster:undefined,release_date:c.year?c.year+'-01-01':'',item:c};});
                if (!results.length) results.push({id:'empty',title:(d.refreshing||[]).length?'Обновляются библиотеки…':'Раздач пока нет'});
                self.build({results:results,total_pages:1});
                if (saved && saved.focus && current()) {
                    var node=self.render().find('.all-releases-card').filter(function () {
                        return this.getAttribute('data-release-key')===saved.focus;
                    }).first();
                    if (node.length) Lampa.Controller.collectionFocus(node,self.render());
                }
                Object.keys(d.errors||{}).forEach(function (k) {message(names[k]+': '+d.errors[k]);});
                refreshSources(d);
            }
            if (saved && saved.owner===owner) draw(saved.data);
            else request('season-watch/catalog',null,draw,function (s) {if(valid()) {self.build({results:[{id:'retry',title:'Повторить загрузку'}],total_pages:1});message(s);}});
        };
        this.next=function () {};
        this.cardRender=function (params,data,card) {
            var node=card.render(),c=data.item;
            node.addClass('all-releases-card');
            if (c) {
                var q=/(?:2160|1080|720|480)[pр]|4K/i.exec(c.label);
                node.attr('data-release-key',c.place+'-'+c.hash);
                var info=[names[c.place],c.release_summary || (q && q[0])].filter(Boolean);
                if (c.inventory_asof!==undefined) info.push('Сохранённый список'+(c.inventory_asof?' от '+new Date(c.inventory_asof*1000).toLocaleString():''));
                (c.seasons||[]).forEach(function (s) {
                    if (s.stale) info.push('Последние данные'+(s.asof?' от '+new Date(s.asof*1000).toLocaleString():''));
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
        $('<style>').text('.all-releases-source{font-size:.85em;opacity:.85;margin-top:.25em}.all-releases-caption{position:absolute;left:.7em;right:.7em;top:45%;text-align:center;overflow-wrap:anywhere}').appendTo('head');
        entry=$('<li class="menu__item selector" data-action="all_releases"><div class="menu__ico">▤</div><div class="menu__text"></div></li>');
        entry.find('.menu__text').text(title);
        badge=$('<span style="margin-left:.5em"></span>').hide().appendTo(entry);
        entry.on('hover:enter',grid);$('.menu .menu__list').eq(0).append(entry);
        refreshBadge();window.setInterval(function () {if (!document.hidden) refreshBadge();},600000);
        document.addEventListener('visibilitychange',function () {if(!document.hidden) refreshBadge();});
    }
    if (window.appready) start();else Lampa.Listener.follow('app',function (e) {if(e.type==='ready') start();});
})();
