(function () {
    'use strict';
    if (window.lampac_player_metrics) return;
    window.lampac_player_metrics = true;

    function install() {
        var L = window.Lampa;
        if (!L || !window.Lampa.PlayerVideo || !window.Lampa.PlayerInfo || !window.Lampa.Player) return false;
        var active = false, timer = null, state = null, row = null;
        var pending = new WeakMap(), session = null, lastSummary = '', ready = false;
        var observed = null, frameId = null;

        function now() { return window.performance && window.performance.now ? window.performance.now() : Date.now(); }
        window.lampacPlaybackMetrics = {
            choosing: function () { return {shown: now()}; },
            begin: function (offer) {
                var clicked = now();
                return {clicked: clicked, choice: offer ? clicked - offer.shown : null};
            },
            resolved: function (token, cell) {
                if (token && cell) pending.set(cell, {choice: token.choice, link: now() - token.clicked});
            }
        };
        // Native TorrServer can start synchronously or wait for preload before Player.create.
        // Only the latter exposes distinct click and stream-ready times.
        var torrentPicks = new WeakMap(), listOffer = null;
        if (L.Listener) L.Listener.follow('torrent_file', function (e) {
            if (e.type === 'list_open' || e.type === 'list_close') {
                torrentPicks = new WeakMap();
                listOffer = e.type === 'list_open' ? window.lampacPlaybackMetrics.choosing() : null;
            }
            if (e.type !== 'render' || !listOffer || !e.element || !e.item) return;
            var pick = {offer: listOffer};
            torrentPicks.set(e.element, pick);
            e.item.on('hover:enter', function () {
                if (torrentPicks.get(e.element) === pick) pick.click = window.lampacPlaybackMetrics.begin(pick.offer);
            });
        });
        L.Player.listener.follow('create', function (e) {
            var cell = e.data, pick = cell && torrentPicks.get(cell);
            if (!pick) return;
            torrentPicks.delete(cell);
            if (pick.click) window.lampacPlaybackMetrics.resolved(pick.click, cell);
            else pending.set(cell, {choice: now() - pick.offer.shown, link: null});
        });
        function take(cell) {
            var measured = cell && pending.get(cell);
            if (cell) pending.delete(cell);
            return measured || {choice: null, link: null};
        }
        function duration(ms) { return ms == null ? '—' : (ms / 1000).toFixed(2).replace('.', ',') + ' с'; }
        function summary(s) {
            var phases = 'Выбор файла ' + duration(s.choice) + ' · Ссылка ' + duration(s.link);
            if (s.external) return phases + ' · Внешний плеер: данные о кадрах и буферизации недоступны';
            var stalls = s.stallMs + (s.waitFrom == null ? 0 : now() - s.waitFrom);
            return phases + (s.estimate ? ' · Старт (playing) ' : ' · До первого кадра ') + duration(s.frame) +
                ' · Буферизация ' + (s.frame == null ? '—' : s.stalls + ' / ' + duration(stalls));
        }
        function finishWait() {
            if (session && session.waitFrom != null) {
                session.stallMs += now() - session.waitFrom;
                session.waitFrom = null;
            }
        }
        function waitingForData() {
            if (session && session.frame != null && session.waitFrom == null && !observed.paused && !observed.ended && !observed.seeking) {
                session.waitFrom = now();
                session.stalls++;
            }
        }
        function playing() {
            finishWait();
            if (session && session.frame == null && typeof observed.requestVideoFrameCallback !== 'function') {
                session.frame = now() - session.start;
                session.estimate = true;
            }
        }
        function unbindVideo() {
            finishWait();
            if (!observed) return;
            if (frameId != null && observed.cancelVideoFrameCallback) observed.cancelVideoFrameCallback(frameId);
            observed.removeEventListener('waiting', waitingForData);
            observed.removeEventListener('playing', playing);
            ['pause', 'seeking', 'ended'].forEach(function (event) { observed.removeEventListener(event, finishWait); });
            observed = null;
            frameId = null;
        }
        function bindVideo(video) {
            if (!ready || !session || observed === video) return;
            unbindVideo();
            if (!video || !video.addEventListener || !video.removeEventListener) return;
            observed = video;
            observed.addEventListener('waiting', waitingForData);
            observed.addEventListener('playing', playing);
            ['pause', 'seeking', 'ended'].forEach(function (event) { video.addEventListener(event, finishWait); });
            if (session.frame == null && typeof video.requestVideoFrameCallback === 'function') {
                var current = session;
                frameId = video.requestVideoFrameCallback(function (time) {
                    if (current !== session || video !== observed) return;
                    current.frame = Math.max(0, time - current.start);
                    frameId = null;
                });
            }
        }
        if (L.SettingsApi && L.SettingsApi.addParam) L.SettingsApi.addParam({
            component: 'player', param: {type: 'button'}, field: {name: 'Статистика последнего запуска'},
            onChange: function () { L.Noty.show(session ? summary(session) : lastSummary || 'В этом сеансе плеер ещё не запускался'); }
        });
        var nativeSet = L.PlayerInfo.set;
        // Suppress the old HLS statistic only while our replacement is active.
        L.PlayerInfo.set = function (name, value) {
            if (active && name === 'bitrate') return;
            return nativeSet.apply(this, arguments);
        };
        var style = document.createElement('style');
        style.textContent = '.player-info__values > .lampac-player-metrics{flex-basis:100%;margin-right:0;line-height:1.6}.lampac-player-metrics span{white-space:normal;overflow-wrap:break-word}@media(max-width:600px){.player-info__values > .lampac-player-metrics span{font-size:1em}}';
        document.head.appendChild(style);

        function number(value) { return typeof value === 'number' && isFinite(value) && value > 0; }
        function buffer(video) {
            try {
                var ranges = video.buffered, at = video.currentTime;
                if (!ranges || typeof at !== 'number' || !isFinite(at)) return null;
                for (var i = 0; i < ranges.length; i++) {
                    if (ranges.start(i) <= at && at <= ranges.end(i)) return Math.max(0, ranges.end(i) - at);
                }
                return 0;
            } catch (e) { return null; }
        }
        function rate(value) { return number(value) ? value.toFixed(2).replace('.', ',') + ' Мбит/с' : '—'; }
        function update() {
            if (!active) return;
            hookHls();
            var video = L.PlayerVideo.video();
            bindVideo(video);
            var info = L.PlayerInfo.render();
            if (!info || !info.find) return;
            var values = info.find('.player-info__values');
            if (!values.length) return;
            if (!row || !row.length || row.parent()[0] !== values[0]) {
                if (row) row.remove();
                row = $('<div class="lampac-player-metrics"><span></span></div>');
                values.append(row);
            }
            var current = state && state.video === video ? state : null;
            var bits = null, speed = null;
            if (current && current.hls.media === video) {
                var frag = current.hls.streamController && current.hls.streamController.fragPlaying;
                if (frag && number(frag.duration) && frag.stats && number(frag.stats.total)) bits = frag.stats.total * 8 / frag.duration / 1000000;
                if (!number(bits)) {
                    var level = current.hls.levels && current.hls.levels[current.hls.currentLevel];
                    if (level && number(level.bitrate)) bits = level.bitrate / 1000000;
                }
                // A completed segment is a sample, not continuous channel capacity.
                if (Date.now() - current.sampleAt < 15000) speed = current.speed;
            }
            var seconds = video ? buffer(video) : null;
            row.find('span').text('Поток ' + (number(bits) ? '≈' : '') + rate(bits) + ' · Загрузка ' + (number(speed) ? '≈' : '') + rate(speed) + ' · Буфер ' + (seconds === null ? '—' : seconds.toFixed(1).replace('.', ',') + ' с') + (session ? ' · ' + summary(session) : ''));
        }
        function hookHls() {
            var H = window.Hls;
            if (!H || !H.prototype || !H.prototype.attachMedia || H.prototype.attachMedia.lampacMetrics) return;
            var original = H.prototype.attachMedia;
            function attach(media) {
                var hls = this;
                var record = {hls:hls,video:media,speed:null,sampleAt:0};
                hls.lampacMetricsRecord = record;
                if (media === L.PlayerVideo.video()) state = record;
                if (!hls.lampacMetricsBound && H.Events && H.Events.FRAG_LOADED) {
                    hls.lampacMetricsBound = true;
                    hls.on(H.Events.FRAG_LOADED, function (_, data) {
                        var record = hls.lampacMetricsRecord;
                        if (!active || state !== record || hls.media !== L.PlayerVideo.video()) return;
                        if (!data || !data.frag || data.frag.type !== 'main') return;
                        var stats = data.stats || data.frag.stats;
                        var loading = stats && stats.loading;
                        var begin = loading ? loading.start : stats && stats.trequest;
                        var end = loading ? loading.end : stats && stats.tload;
                        if (stats && number(stats.total) && typeof begin === 'number' && typeof end === 'number' && end > begin) {
                            record.speed = stats.total * 8 / (end - begin) / 1000;
                            record.sampleAt = Date.now();
                        } else { record.speed = null; record.sampleAt = 0; }
                        update();
                    });
                }
                return original.apply(this, arguments);
            }
            attach.lampacMetrics = true;
            H.prototype.attachMedia = attach;
        }
        function stop() {
            unbindVideo();
            if (session) lastSummary = summary(session);
            session = null; ready = false;
            active = false;
            clearInterval(timer); timer = null; state = null;
            if (row) row.remove();
            row = null;
        }
        L.Player.listener.follow('external', function (cell) {
            stop();
            var external = take(cell);
            external.external = true;
            lastSummary = summary(external);
        });
        L.Player.listener.follow('start', function (cell) {
            stop();
            session = take(cell);
            session.start = now();
            session.frame = null;
            session.waitFrom = null;
            session.stalls = 0;
            session.stallMs = 0;
            active = true; hookHls();
            timer = setInterval(update, 1000);
            update();
        });
        L.Player.listener.follow('ready', function () { ready = true; update(); });
        L.Player.listener.follow('destroy', stop);
        L.PlayerVideo.listener.follow('destroy', function () { state = null; unbindVideo(); });
        L.PlayerVideo.listener.follow('timeupdate', update);
        L.PlayerVideo.listener.follow('progress', update);
        hookHls();
        return true;
    }
    if (!install()) {
        var waiting = setInterval(function () { if (install()) clearInterval(waiting); }, 500);
    }
})();
