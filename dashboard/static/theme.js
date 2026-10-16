// Shared by the console, playables and phones pages. Load it in <head> (not deferred) so the
// saved theme applies before first paint. Theme: "auto" (follow the system), "light" or "dark",
// saved per browser. Pages listen for the "themechange" event to recolour canvases.
(function () {
  var KEY = 'openyam-theme';
  var root = document.documentElement;
  function saved() { try { return localStorage.getItem(KEY) || 'auto'; } catch (e) { return 'auto'; } }
  function apply(mode) {
    if (mode === 'light' || mode === 'dark') root.setAttribute('data-theme', mode);
    else root.removeAttribute('data-theme');
  }
  apply(saved());
  function label(mode) { return mode === 'light' ? '☀ light' : mode === 'dark' ? '☾ dark' : '◐ auto'; }
  function fire() { window.dispatchEvent(new Event('themechange')); }
  window.openyamTheme = {
    // Read a CSS custom property as a string, e.g. cssVar('--sunken').
    cssVar: function (name) { return getComputedStyle(root).getPropertyValue(name).trim(); },
  };
  if (window.matchMedia) {
    var mq = window.matchMedia('(prefers-color-scheme: light)');
    if (mq.addEventListener) mq.addEventListener('change', fire);
  }
  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('themeBtn');
    if (btn) {
      btn.textContent = label(saved());
      btn.title = 'Theme: auto follows the system setting';
      btn.addEventListener('click', function () {
        var order = ['auto', 'light', 'dark'];
        var next = order[(order.indexOf(saved()) + 1) % order.length];
        try { localStorage.setItem(KEY, next); } catch (e) { /* private mode: not saved */ }
        apply(next);
        btn.textContent = label(next);
        fire();
      });
    }
    // The nav works on the console (:8090). When a page is served by the standalone preview
    // server (:8092), point the nav at the console instead.
    if (location.port === '8092') {
      document.querySelectorAll('a[data-console]').forEach(function (a) {
        a.href = 'http://127.0.0.1:8090' + a.getAttribute('href');
      });
    }
  });
})();
