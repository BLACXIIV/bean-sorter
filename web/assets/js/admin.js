/* Shared: clock, engine chip, confirm modals on forms with data-confirm. */
(function () {
  'use strict';
  var clock = document.getElementById('clock');
  if (clock) {
    var tick = function () { clock.textContent = new Date().toLocaleTimeString('en-GB'); };
    tick(); setInterval(tick, 1000);
  }

  if (!window.__op) {                     // operate.js polls faster on its page
    var chip = document.getElementById('engine-chip');
    var card = document.getElementById('engine-card');  // admin/index.php
    var sysTbl = document.getElementById('engine-sys'); // admin/system.php
    if (chip || card || sysTbl) {
      var fill = function (j) {
        if (chip) {
          chip.className = 'chip ok';
          chip.innerHTML = j.mode + ' &middot; ' + (j.armed ? 'ARMED' : 'DISARMED') +
            ' &middot; ' + j.fps + 'fps &middot; ' +
            '<span class="' + (j.camera_source !== 'pi' || j.valve !== 'real' ? 'sim' : '') + '">' +
            'CAM: ' + (j.camera_source || '?').toUpperCase() +
            ' &middot; VALVE: ' + (j.valve || '?').toUpperCase() + '</span>';
        }
        if (card) {
          card.innerHTML = '<p>mode <b>' + j.mode + '</b> &middot; ' +
            (j.armed ? '<b class="err">ARMED</b>' : 'disarmed') + ' &middot; ' +
            j.fps + ' fps' + (j.sim ? ' &middot; <b class="warn">SIM</b>' : '') +
            '</p><p class="muted">' + (j.message || '') + '</p>';
        }
        if (sysTbl) {
          sysTbl.innerHTML = '<table>' +
            '<tr><td>Mode</td><td>' + j.mode + '</td></tr>' +
            '<tr><td>Armed</td><td>' + (j.armed ? '<b class="err">ARMED</b>' : 'no') + '</td></tr>' +
            '<tr><td>FPS</td><td>' + j.fps + '</td></tr>' +
            '<tr><td>Camera</td><td>' + (j.camera_source || '?') + '</td></tr>' +
            '<tr><td>Valve</td><td>' + (j.valve || '?') + '</td></tr>' +
            '<tr><td>Session</td><td>' + (j.session_id || '-') + '</td></tr></table>';
        }
      };
      var poll = function () {
        fetch('http://' + location.hostname + ':5000/api/status')
          .then(function (r) { return r.json(); })
          .then(fill)
          .catch(function () {
            if (chip) { chip.className = 'chip bad'; chip.textContent = 'engine offline'; }
            if (card) { card.innerHTML = '<p class="err">Engine not running</p>'; }
            if (sysTbl) { sysTbl.innerHTML = '<p class="err">Engine not running</p>'; }
          });
      };
      poll(); setInterval(poll, 3000);
    }
  }

  /* forms tagged data-confirm="question" get a modal OK/Cancel */
  document.querySelectorAll('form[data-confirm]').forEach(function (f) {
    f.addEventListener('submit', function (ev) {
      if (f.dataset.ok === '1') return;
      ev.preventDefault();
      var bg = document.createElement('div');
      bg.className = 'modal-bg';
      bg.innerHTML = '<div class="modal"><p>' + f.dataset.confirm + '</p>' +
        '<div class="btnrow"><button type="button" id="cf-no">Cancel</button>' +
        '<button type="button" id="cf-yes" class="primary">Confirm</button></div></div>';
      document.body.appendChild(bg);
      bg.querySelector('#cf-yes').onclick = function () {
        f.dataset.ok = '1'; bg.remove(); f.submit();
      };
      bg.querySelector('#cf-no').onclick = function () { bg.remove(); };
    });
  });
})();
