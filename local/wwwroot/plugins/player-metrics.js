(function () {
    'use strict';
    if (window.lampac_player_metrics) return;
    window.lampac_player_metrics = true;

    function install() {
        var L = window.Lampa;
        if (!L || !window.Lampa.PlayerVideo || !window.Lampa.PlayerInfo || !window.Lampa.Player) return false;
        var active = false, timer = null, state = null, row = null;
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
            row.find('span').text('Поток ' + (number(bits) ? '≈' : '') + rate(bits) + ' · Загрузка ' + (number(speed) ? '≈' : '') + rate(speed) + ' · Буфер ' + (seconds === null ? '—' : seconds.toFixed(1).replace('.', ',') + ' с'));
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
            active = false;
            clearInterval(timer); timer = null; state = null;
            if (row) row.remove();
            row = null;
        }
        L.Player.listener.follow('start', function () {
            stop(); active = true; hookHls();
            timer = setInterval(update, 1000);
            update();
        });
        L.Player.listener.follow('destroy', stop);
        L.PlayerVideo.listener.follow('destroy', function () { state = null; });
        L.PlayerVideo.listener.follow('timeupdate', update);
        L.PlayerVideo.listener.follow('progress', update);
        hookHls();
        return true;
    }
    if (!install()) {
        var waiting = setInterval(function () { if (install()) clearInterval(waiting); }, 500);
    }
})();
