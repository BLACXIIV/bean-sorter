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
    if (chip) {
      var poll = function () {
        fetch('http://' + location.hostname + ':5000/api/status')
          .then(function (r) { return r.json(); })
          .then(function (j) {
            chip.className = 'chip ok';
            chip.innerHTML = j.mode + ' &middot; ' + (j.armed ? 'ARMED' : 'DISARMED') +
              ' &middot; ' + j.fps + 'fps' + (j.sim ? ' &middot; <span class="sim">SIM</span>' : '');
          })
          .catch(function () { chip.className = 'chip bad'; chip.textContent = 'engine offline'; });
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
