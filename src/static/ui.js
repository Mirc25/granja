document.addEventListener('DOMContentLoaded', function() {
  function buildRepeated(text, repsStr) {
    var reps = parseInt(repsStr || '1', 10);
    if (isNaN(reps) || reps < 1) reps = 1;
    var lines = String(text || '').split('\n')
      .map(function(s){ return s.trim(); })
      .filter(function(s){ return s.length > 0; });
    var out = [];
    for (var i = 0; i < lines.length; i++) {
      for (var j = 0; j < reps; j++) out.push(lines[i]);
    }
    return out.join('\n');
  }

  function addHidden(form, name, value) {
    var input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    input.value = String(value == null ? '' : value);
    form.appendChild(input);
  }

  var runBtn = document.getElementById('run');
  if (runBtn) {
    runBtn.addEventListener('click', function() {
      var urls = document.getElementById('urls').value || '';
      var reps = document.getElementById('reps').value || '1';
      var screenshot = document.getElementById('screenshot').checked;
      var show = document.getElementById('show').checked;
      var respect = document.getElementById('respect').checked;
      var min = document.getElementById('min').value || '5000';
      var maxd = document.getElementById('maxd').value || '15000';
      var play = document.getElementById('play').checked;
      var loginGmailEl = document.getElementById('login_gmail');
      var useSavedEl = document.getElementById('use_saved_accounts');
      var gmailEmailEl = document.getElementById('gmail_email');
      var gmailPasswordEl = document.getElementById('gmail_password');
      var loginGmail = loginGmailEl ? loginGmailEl.checked : false;
      var useSaved = useSavedEl ? useSavedEl.checked : false;
      var gmailEmail = gmailEmailEl ? (gmailEmailEl.value || '') : '';
      var gmailPassword = gmailPasswordEl ? (gmailPasswordEl.value || '') : '';
      var rotateVpn = document.getElementById('rotate_vpn').checked;
      var vpnProvider = document.getElementById('vpn_provider').value || '';
      var vpnCountry = document.getElementById('vpn_country').value || '';
      var vpnWait = document.getElementById('vpn_wait').value || '5000';

      var urlsRepeated = buildRepeated(urls.trim(), reps);
      var form = document.createElement('form');
      form.method = 'POST';
      form.action = '/run_browser_form';

      addHidden(form, 'urls_text', urlsRepeated);
      addHidden(form, 'max_pages_per_proxy', '3');
      if (screenshot) addHidden(form, 'screenshot', 'on');
      if (show) addHidden(form, 'show_browser', 'on');
      if (respect) addHidden(form, 'respect_robots', 'on');
      addHidden(form, 'min_dwell_ms', min);
      addHidden(form, 'max_dwell_ms', maxd);
      if (play) addHidden(form, 'play_video', 'on');
      if (loginGmail) addHidden(form, 'gmail_login', 'on');
      if (useSaved) addHidden(form, 'use_saved_accounts', 'on');
      if (gmailEmail) addHidden(form, 'gmail_email', gmailEmail);
      if (gmailPassword) addHidden(form, 'gmail_password', gmailPassword);
      var forceIncognitoEl = document.getElementById('force_incognito');
      if (forceIncognitoEl && forceIncognitoEl.checked) addHidden(form, 'force_incognito', 'on');
      if (rotateVpn) addHidden(form, 'rotate_vpn_per_url', 'on');
      if (vpnProvider) addHidden(form, 'vpn_provider', vpnProvider);
      if (vpnCountry) addHidden(form, 'vpn_country', vpnCountry);
      addHidden(form, 'vpn_wait_ms', vpnWait);

      document.body.appendChild(form);
      form.submit();
    });
  }

  var silentBtn = document.getElementById('run_silent');
  if (silentBtn) {
    silentBtn.addEventListener('click', function() {
      var urls = document.getElementById('urls').value || '';
      var reps = document.getElementById('reps').value || '1';
      var screenshot = document.getElementById('screenshot').checked;
      var show = document.getElementById('show').checked;
      var forceIncognitoEl = document.getElementById('force_incognito');
      var respect = document.getElementById('respect').checked;
      var min = document.getElementById('min').value || '5000';
      var maxd = document.getElementById('maxd').value || '15000';
      var play = document.getElementById('play').checked;
      var fwmEl = document.getElementById('force_watch_mode');
      var loginGmailEl = document.getElementById('login_gmail');
      var useSavedEl = document.getElementById('use_saved_accounts');
      var gmailEmailEl = document.getElementById('gmail_email');
      var gmailPasswordEl = document.getElementById('gmail_password');
      var loginGmail = loginGmailEl ? loginGmailEl.checked : false;
      var useSaved = useSavedEl ? useSavedEl.checked : false;
      var gmailEmail = gmailEmailEl ? (gmailEmailEl.value || '') : '';
      var gmailPassword = gmailPasswordEl ? (gmailPasswordEl.value || '') : '';
      var rotateVpn = document.getElementById('rotate_vpn').checked;
      var vpnProvider = document.getElementById('vpn_provider').value || '';
      var vpnCountry = document.getElementById('vpn_country').value || '';
      var vpnWait = document.getElementById('vpn_wait').value || '5000';
      var instancesEl = document.getElementById('instances');
      var instCount = instancesEl ? parseInt(instancesEl.value || '1', 10) : 1;

      var urlsRepeated = buildRepeated(urls.trim(), reps);
      var params = new URLSearchParams();
      params.set('urls_text', urlsRepeated);
      params.set('max_pages_per_proxy', '3');
      if (screenshot) params.set('screenshot', 'on');
      if (show) params.set('show_browser', 'on');
      if (forceIncognitoEl && forceIncognitoEl.checked) params.set('force_incognito', 'on');
      if (respect) params.set('respect_robots', 'on');
      params.set('min_dwell_ms', min);
      params.set('max_dwell_ms', maxd);
      if (play) params.set('play_video', 'on');
      if (fwmEl && fwmEl.checked) params.set('force_watch_mode', 'on');
      if (loginGmail) params.set('gmail_login', 'on');
      if (useSaved) params.set('use_saved_accounts', 'on');
      if (gmailEmail) params.set('gmail_email', gmailEmail);
      if (gmailPassword) params.set('gmail_password', gmailPassword);
      if (rotateVpn) params.set('rotate_vpn_per_url', 'on');
      if (vpnProvider) params.set('vpn_provider', vpnProvider);
      if (vpnCountry) params.set('vpn_country', vpnCountry);
      params.set('vpn_wait_ms', vpnWait);
      if (!isNaN(instCount)) params.set('instances', String(Math.min(100, Math.max(1, instCount))));

      silentBtn.disabled = true;
      var sr = document.getElementById('silent_result');
      if (sr) sr.textContent = 'Ejecutando… (silencioso)';

      var endpoint;
      if (show) {
        endpoint = '/run_browser_single_tabs_live';
      } else if (instCount > 1) {
        endpoint = '/run_browser_silent_batch';
      } else {
        endpoint = '/run_browser_silent_inline';
      }
      fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: params.toString(),
      }).then(function(resp){ return resp.text(); })
        .then(function(html){
          var sr = document.getElementById('silent_result');
          if (sr) sr.innerHTML = html;
          try {
            var tmp = document.createElement('div');
            tmp.innerHTML = html;
            var grid = document.getElementById('live_minis');
            var minis = Array.prototype.slice.call(tmp.querySelectorAll('.mini-grid .mini, .mini'));
            var counterId = (minis[0] && minis[0].getAttribute('data-counter-id')) || ('batch-' + Math.random().toString(36).slice(2));
            var visibleLimit = 25;
            if (grid && minis.length) {
              var visible = minis.slice(0, visibleLimit);
              var hidden = minis.slice(visibleLimit);
              visible.forEach(function(m){ grid.prepend(m); });
              ensureLivePreviewTimer();
              ensureLiveStatusTimer();
              if (hidden.length) {
                var moreWrap = document.createElement('div');
                moreWrap.className = 'mini-toolbar';
                moreWrap.style.display = 'flex';
                moreWrap.style.alignItems = 'center';
                moreWrap.style.gap = '10px';
                moreWrap.style.marginTop = '6px';

                var info = document.createElement('span');
                info.className = 'muted';
                info.textContent = 'Mostrando ' + Math.min(minis.length, visibleLimit) + ' de ' + minis.length;
                moreWrap.appendChild(info);

                var moreBtn = document.createElement('button');
                moreBtn.className = 'more-btn';
                moreBtn.textContent = 'Más +';
                moreBtn.style.marginTop = '8px';
                moreBtn.style.padding = '6px 10px';
                moreBtn.style.border = '1px solid #4caf50';
                moreBtn.style.color = '#fff';
                moreBtn.style.background = '#2e7d32';
                moreBtn.style.borderRadius = '6px';
                moreBtn.style.cursor = 'pointer';
                moreWrap.appendChild(moreBtn);

                var hiddenGrid = document.createElement('div');
                hiddenGrid.id = 'hidden-' + counterId;
                hiddenGrid.className = 'mini-grid';
                hiddenGrid.style.display = 'none';
                hiddenGrid.style.flexWrap = 'wrap';
                hiddenGrid.style.gap = '8px';
                hiddenGrid.style.marginTop = '10px';
                hidden.forEach(function(m){ hiddenGrid.appendChild(m); });

                // Insert after the live grid
                grid.parentNode.insertBefore(moreWrap, grid.nextSibling);
                grid.parentNode.insertBefore(hiddenGrid, moreWrap.nextSibling);

                moreBtn.addEventListener('click', function(){
                  try {
                    hiddenGrid.style.display = 'flex';
                    // Mover los ocultos al grid visible para que se gestionen igual
                    var toMove = Array.prototype.slice.call(hiddenGrid.querySelectorAll('.mini'));
                    toMove.forEach(function(m){ grid.appendChild(m); });
                    hiddenGrid.parentNode.removeChild(hiddenGrid);
                    moreBtn.disabled = true;
                    moreBtn.textContent = 'Mostrando todo';
                    ensureLivePreviewTimer();
                    ensureLiveStatusTimer();
                    ensureYouTubeAPI().then(function(){ registerYTPlayers(); });
                  } catch(e){}
                });
              }
            }
            ensureYouTubeAPI().then(function(){
              registerYTPlayers();
            }).catch(function(){
              setTimeout(function(){
                try {
                  minis.forEach(function(m){ if (m && m.parentNode) m.parentNode.removeChild(m); });
                  updateCountersFromMinis(minis);
                } catch(e){}
              }, 90000);
            });
            ensureLiveStatusTimer();
          } catch(e) {}
        })
        .catch(function(err){
          var msg = 'Error: ' + String(err && err.message ? err.message : err) + ' — endpoint: ' + String(endpoint);
          var out = document.getElementById('out');
          if (out) out.textContent = msg;
          var sr = document.getElementById('silent_result');
          if (sr) sr.textContent = msg;
        })
        .finally(function(){ silentBtn.disabled = false; });
    });
  }
});

// Actualiza las miniaturas en vivo (imagenes provenientes de artifacts/) cada segundo
function ensureLivePreviewTimer(){
  if (window.__miniLiveTimer) return;
  window.__miniLiveTimer = setInterval(function(){
    try {
      var imgs = document.querySelectorAll('img.mini-live');
      var ts = Date.now();
      imgs.forEach(function(img){
        var base = img.getAttribute('data-src-base') || (img.src ? img.src.split('?')[0] : '');
        if (base) {
          img.setAttribute('data-src-base', base);
          img.src = base + '?ts=' + ts;
        }
      });
    } catch(e){}
  }, 1000);
}

// Estado en vivo: marca cada mini en verde/rojo leyendo /artifacts/<id>.done
function ensureLiveStatusTimer(){
  if (window.__miniStatusTimer) return;
  window.__miniStatusTimer = setInterval(function(){
    try {
      var minis = document.querySelectorAll('.mini-grid .mini[data-counter-id][data-index]');
      minis.forEach(function(m){
        var cid = m.getAttribute('data-counter-id');
        var idx = m.getAttribute('data-index');
        if (!cid || idx == null) return;
        var url = '/artifacts/' + cid + '_' + idx + '.done?ts=' + Date.now();
        fetch(url, { method: 'GET', cache: 'no-store' }).then(function(resp){
          if (!resp.ok) return;
          return resp.text();
        }).then(function(text){
          if (!text) return;
          var t = (text || '').trim().toLowerCase();
          if (t === 'ok') {
            m.className = 'mini ok';
            var b = m.querySelector('.badge'); if (b) b.textContent = 'OK';
          } else if (t === 'err') {
            m.className = 'mini err';
            var b2 = m.querySelector('.badge'); if (b2) b2.textContent = 'ERROR';
          }
        }).catch(function(){ /* nada */ });
      });
    } catch(e){}
  }, 1000);
}

// YouTube Iframe API helpers
var ytApiPromise;
function ensureYouTubeAPI(){
  // Si está activo el modo watch forzado, no cargamos la API de YouTube
  var fwmEl = document.getElementById('force_watch_mode');
  var forceWatch = !!(fwmEl && fwmEl.checked);
  if (forceWatch) return Promise.resolve();
  if (window.YT && window.YT.Player) return Promise.resolve();
  if (ytApiPromise) return ytApiPromise;
  ytApiPromise = new Promise(function(resolve){
    var s = document.createElement('script');
    s.id = 'yt-iframe-api';
    s.src = 'https://www.youtube.com/iframe_api';
    document.head.appendChild(s);
    window.onYouTubeIframeAPIReady = function(){ resolve(); };
  });
  return ytApiPromise;
}

function registerYTPlayers(container){
  // Preferir contenedores vacíos generados por el batch
  var containers = (container || document).querySelectorAll('.mini-player');
  containers.forEach(function(el){
    try {
      var fwmEl = document.getElementById('force_watch_mode');
      var forceWatch = !!(fwmEl && fwmEl.checked);
      var mini = el.closest('.mini');
      var cid = mini ? mini.getAttribute('data-counter-id') : null;
      var vid = (mini ? mini.getAttribute('data-video-id') : null) || el.getAttribute('data-video-id');
      // Si el elemento actual ya es un iframe (modo inline silencioso), también registrar
      var isIframe = el.tagName.toLowerCase() === 'iframe';
      var target = isIframe ? el : el.id || undefined;
          var hasYT = !!(window.YT && window.YT.Player);

      // Modo watch forzado: mostrar miniatura y enlace a la página de watch
      if (forceWatch) {
        if (!isIframe && vid) {
          var thumb = 'https://i.ytimg.com/vi/' + vid + '/hqdefault.jpg';
          var href = 'https://www.youtube.com/watch?v=' + vid;
          el.innerHTML = '<a href="' + href + '" target="_blank" rel="noopener noreferrer" title="Abrir en YouTube" style="display:block; width:100%; height:100%; position:relative;">\
            <img src="' + thumb + '" alt="preview" style="width:100%; height:100%; object-fit:cover; display:block;"/>\
            <span class="badge" style="position:absolute; bottom:2px; right:2px; font-size:10px; padding:2px 4px; border-radius:4px; background:rgba(255,255,255,0.85); color:#000;">Abrir</span>\
          </a>';
        }
        return; // no seguimos con embeds
      }
          if (hasYT && !isIframe) {
            var config = {
              host: 'https://www.youtube-nocookie.com',
              videoId: vid,
              playerVars: { autoplay: 1, mute: 1, controls: 0, rel: 0, modestbranding: 1, origin: window.location.origin, playsinline: 1, enablejsapi: 1 },
              events: {
                onReady: function(ev){ try { ev.target.mute(); ev.target.playVideo(); } catch(e){} },
                onStateChange: function(ev){
                  if (ev && ev.data === YT.PlayerState.ENDED) {
                    try {
                      if (mini && mini.parentNode) mini.parentNode.removeChild(mini);
                      if (cid) incrementCounter(cid);
                    } catch(e){}
                  }
                }
              }
            };
        // Crear player y fallback si no arranca
        var plr;
        try {
          plr = new YT.Player(target, config);
          setTimeout(function(){
            try {
              var st = plr && typeof plr.getPlayerState === 'function' ? plr.getPlayerState() : undefined;
              if (st == null || st === YT.PlayerState.UNSTARTED) {
                if (vid) {
                  el.innerHTML = '<iframe src="https://www.youtube-nocookie.com/embed/' + vid + '?autoplay=1&mute=1&playsinline=1" title="YouTube mini" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share; autoplay" allowfullscreen></iframe>';
                }
              }
            } catch(e){}
          }, 2000);
        } catch(err) {
          if (vid) {
            el.innerHTML = '<iframe src="https://www.youtube-nocookie.com/embed/' + vid + '?autoplay=1&mute=1&playsinline=1" title="YouTube mini" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share; autoplay" allowfullscreen></iframe>';
          }
        }
        // Registrar también fallback en onError para errores como 150/153
        try {
          if (plr && typeof plr.addEventListener === 'function') {
            plr.addEventListener('onError', function(){
              try {
                if (vid) {
                  el.innerHTML = '<iframe src="https://www.youtube-nocookie.com/embed/' + vid + '?autoplay=1&mute=1&playsinline=1" title="YouTube mini" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share; autoplay" allowfullscreen></iframe>';
                }
              } catch(e){}
            });
          } else if (plr && plr.addEventListener == null) {
            // API v3: usar events.onError directamente
            try {
              plr.addEventListener = function(name, fn){ /* noop */ };
            } catch(e){}
          }
        } catch(e){}
      } else {
        // Sin API de YouTube o ya es iframe: inyectar iframe directo si hace falta
        if (!isIframe && vid) {
          el.innerHTML = '<iframe src="https://www.youtube-nocookie.com/embed/' + vid + '?autoplay=1&mute=1&playsinline=1" title="YouTube mini" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share; autoplay" allowfullscreen></iframe>';
        }
      }
    } catch(e){}
  });
}

function incrementCounter(counterId){
  try {
    var line = document.getElementById('counter-' + counterId);
    if (!line) return;
    var countEl = line.querySelector('.count');
    var n = parseInt((countEl && countEl.textContent) || '0', 10);
    if (isNaN(n)) n = 0;
    if (countEl) countEl.textContent = String(n + 1);
    var sum = document.getElementById('players_summary');
    if (sum) {
      var total = 0;
      document.querySelectorAll('.repro-line .count').forEach(function(c){
        var v = parseInt(c.textContent||'0',10); if(!isNaN(v)) total += v;
      });
      sum.textContent = 'Se reprodujo ' + String(total) + ' veces';
    }
  } catch(e){}
}

function updateCountersFromMinis(minis){
  (minis || []).forEach(function(m){
    var cid = m.getAttribute('data-counter-id');
    if (cid) incrementCounter(cid);
  });
}