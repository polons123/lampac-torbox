(function () {
    'use strict';
    if (window.seasonWatchInstalled) return;
    window.seasonWatchInstalled = true;
    var base = '{localhost}', label = 'Обновления сериалов', entry, badge, serial = 0;
    var script = (document.currentScript || {}).src || '';
    var token = /[?&]token=([^&#]+)/.exec(script);
    var places = {home: 'Домашний TorrServer', peer: 'TorrServer h2nexus-p', box: 'TorBox'};
    function storage(name) { return Lampa.Storage.get(name, '') || ''; }
    function account() { return base + '|' + storage('account_email') + '|' + storage('lampac_unic_id') + '|' + (token ? token[1] : ''); }
    function url(path) {
        var u = base + '/season-watch/' + path;
        [['account_email', storage('account_email')], ['uid', storage('lampac_unic_id')], ['token', token && decodeURIComponent(token[1])]].forEach(function (p) {
            if (p[1]) u = Lampa.Utils.addUrlComponent(u, p[0] + '=' + encodeURIComponent(p[1]));
        });
        return u;
    }
    function safe(value) { return $('<span>').text(String(value || '')).html(); }
    function ep(s, n) { return 'S' + (s < 10 ? '0' : '') + s + 'E' + (n < 10 ? '0' : '') + n; }
    function dates(t) { return new Date(t * 1000).toLocaleString(); }
    function key(w, n) { return (w.tmdb || w.id) + '/' + w.season + '/' + n; }
    function seenKey() { return 'season_watch_seen_' + Lampa.Utils.hash(account()); }
    function seen() { return Lampa.Storage.get(seenKey(), {}) || {}; }
    function unique(items) {
        var keys = {};
        items.forEach(function (w) { w.ready.forEach(function (n) { keys[key(w, n)] = true; }); });
        return keys;
    }
    function count(items) {
        var history = seen(), keys = unique(items);
        var n = Object.keys(keys).filter(function (k) { return !history[k]; }).length;
        badge.text(n ? String(n) : '').toggle(!!n);
    }
    function read(open) {
        var id = ++serial, who = account();
        $.ajax({url: url('list'), dataType: 'json', timeout: 15000}).done(function (d) {
            if (id !== serial || who !== account() || !Array.isArray(d.items)) return;
            count(d.items);
            if (open) library(d);
        }).fail(function () {
            if (id !== serial) return;
            badge.hide();
            if (open) Lampa.Noty.show('Список обновлений недоступен. Проверьте вход в Lampac');
        });
    }
    function menu(title, items, back) {
        Lampa.Select.show({title: title, items: items, onSelect: function (i) {
            Lampa.Select.hide();
            if (i.run) i.run(); else back();
        }, onBack: function () { Lampa.Select.hide(); back(); }});
    }
    function rootBack() { Lampa.Controller.toggle('menu'); }
    function library(d) {
        var items = [], rest = [], errors = [], again = function () { read(true); };
        function group(w) {
            if (w.ready.length) return 0;
            if (w.waiting.length) return 1;
            if (w.detail === 'Все серии из календаря уже есть') return 3;
            return 2;
        }
        d.items.slice().sort(function (a,b) { return group(a)-group(b) || a.title.localeCompare(b.title); }).forEach(function (w) {
            var subtitle = [], row;
            if (w.ready.length) subtitle.push(w.ready.map(function (n) { return ep(w.season,n); }).join(', ') + ' доступна в вашей раздаче');
            if (w.waiting.length) subtitle.push(w.waiting.map(function (n) { return ep(w.season,n); }).join(', ') + ' вышла, ждём вашу озвучку');
            if (w.detail) subtitle.push(w.detail);
            var quality = /(?:2160|1080|720|480)[pр]|4K/i.exec(w.label);
            row = {title: safe(w.title + (w.season ? ' · сезон ' + w.season : '')),
                subtitle: safe([places[w.place], quality && quality[0], subtitle.join(' · ')].filter(Boolean).join(' · ')),
                run: function () {
                    var history = seen();
                    w.ready.forEach(function (n) { history[key(w,n)] = true; });
                    Lampa.Storage.set(seenKey(),history); count(d.items); details(w,again);
                }};
            (group(w)===3 ? rest : items).push(row);
        });
        if (rest.length) items.push({title: 'Все серии уже есть (' + rest.length + ')', run: function () { menu(label,rest,again); }});
        Object.keys(d.checked || {}).forEach(function (k) { if (d.checked[k].error) errors.push(places[k] + ' · ' + d.checked[k].error); });
        if (!items.length) items.push({title:'Пока нет сезонов для отслеживания'});
        items.push({title:'Обновить список', subtitle:safe([d.last && ('Проверено ' + dates(d.last))].concat(errors).filter(Boolean).join(' · ')), run:again});
        menu(label,items,rootBack);
    }
    function details(w,back) {
        var rows = [{title:safe(places[w.place]),subtitle:safe(w.label),run:back}];
        if (w.owned.length) rows.push({title:'Серии в библиотеке',subtitle:safe(w.owned.join(', ')),run:back});
        if (w.detail) rows.push({title:safe(w.detail),run:back});
        if (w.ready.length) rows.push({title:'Обновить раздачу',subtitle:'Старая версия сохранится для действующих ссылок',run:function () { confirm('apply',''); }});
        function select(op,choices,title) {
            if (choices.length) rows.push({title:title,run:function () {
                menu(title,choices.map(function (c) { return {title:safe(c.label),run:function () { confirm(op,c.value,c.label); }}; }),function () { details(w,back); });
            }});
        }
        function confirm(op,value,name) {
            var text = op==='apply' ? 'Добавить проверенную версию в ' + places[w.place] + '?' : 'Сохранить выбор?';
            menu(text,[{title:'Отмена',run:function () { details(w,back); }},{title:op==='apply'?'Обновить':'Сохранить',subtitle:safe(name || w.title),run:function () {
                var who=account();
                Lampa.Noty.show('Проверяем выбранную раздачу');
                $.ajax({url:url('action'),method:'POST',headers:{'X-Season-Watch':'1'},contentType:'application/json',dataType:'json',timeout:245000,
                    data:JSON.stringify({op:op,id:w.id,revision:w.revision,value:value})}).done(function () {
                    if (who===account()) { Lampa.Noty.show(op==='apply'?'Новая версия сохранена':'Выбор сохранён'); back(); }
                }).fail(function (x) {
                    if (who===account()) { Lampa.Noty.show(safe((x.responseJSON || {}).detail || 'Не удалось выполнить действие')); back(); }
                });
            }}],function () { details(w,back); });
        }
        select('identity',w.identities,'Выбрать сериал в TMDB');
        select('bind',w.choices,'Выбрать страницу раздачи');
        menu(safe(w.title),rows,back);
    }
    function start() {
        if (entry) return;
        entry = $('<li class="menu__item selector" data-action="season_watch"><div class="menu__ico">▤</div><div class="menu__text"></div></li>');
        entry.find('.menu__text').text(label);
        badge = $('<span style="margin-left:.55em;font-weight:bold" aria-label="Новые серии"></span>').hide().appendTo(entry);
        entry.on('hover:enter',function () { read(true); });
        $('.menu .menu__list').eq(0).append(entry);
        read(false);
        window.setInterval(function () { if (!document.hidden) read(false); }, 10*60*1000);
        document.addEventListener('visibilitychange',function () { if (!document.hidden) read(false); });
    }
    if (window.appready) start();
    else Lampa.Listener.follow('app',function (e) { if (e.type==='ready') start(); });
})();
