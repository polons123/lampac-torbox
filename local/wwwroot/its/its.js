// Compatibility URL: the implementation lives only in infuseSave.js.
(function () {
    if (window.lampac_infuse_saver || window.lampac_infuse_alias_loading) return;
    window.lampac_infuse_alias_loading = true;
    var current = document.currentScript;
    var script = document.createElement('script');
    script.src = new URL('../infuseSave.js', current ? current.src : document.baseURI).href;
    script.onerror = function () { window.lampac_infuse_alias_loading = false; };
    document.head.appendChild(script);
})();
