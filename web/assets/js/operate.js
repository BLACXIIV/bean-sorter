/* Operate screen: 500 ms status poll, calibrate + run controls, line drawing. */
(function () {
  'use strict';
  window.__op = 1;                        // tells admin.js not to double-poll the chip
  var ENGINE = 'http://' + location.hostname + ':5000';
  var $ = function (id) { return document.getElementById(id); };

  var st = null;              // last /api/status
  var grades = null;          // last /api/grades.php
  var drawing = false;
  var linePts = [];

  $('cam').src = ENGINE + '/stream.mjpg';

  function post(path, obj, cb) {
    fetch(ENGINE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(obj || {})
    }).then(function (r) { return r.json(); })
      .then(function (j) { if (cb) cb(j); if (!j.ok && j.error) toast(j.error); })
      .catch(function () { toast('engine unreachable'); });
  }

  /* ---------------- toast + confirm modal ---------------- */
  var toastT = null;
  function toast(msg, hexes) {
    var t = $('toast'), h = '<div>' + msg + '</div>';
    if (hexes && hexes.length) {
      h += '<div class="hexes">';
      hexes.forEach(function (x) {
        h += '<span><i style="background:' + x + '"></i>' + x + '</span>';
      });
      h += '</div>';
    }
    t.innerHTML = h; t.style.display = 'block';
    clearTimeout(toastT);
    toastT = setTimeout(function () { t.style.display = 'none'; }, 3000);
  }

  function confirmModal(text) {
    return new Promise(function (resolve) {
      var root = $('modal-root');
      root.innerHTML =
        '<div class="modal-bg"><div class="modal"><p>' + text + '</p>' +
        '<div class="btnrow"><button id="m-no">Cancel</button>' +
        '<button id="m-yes" class="primary">Confirm</button></div></div></div>';
      $('m-yes').onclick = function () { root.innerHTML = ''; resolve(true); };
      $('m-no').onclick = function () { root.innerHTML = ''; resolve(false); };
    });
  }

  /* ---------------- grade accordion ---------------- */
  var GC = { 1: 'var(--g1)', 2: 'var(--g2)', 3: 'var(--g3)', 4: 'var(--g4)' };
  var openGrade = 1;
  function renderGrades() {
    if (!grades) return;
    var h = '';
    [1, 2, 3, 4].forEach(function (g) {
      var d = grades[g], open = g === openGrade;
      h += '<div class="acc' + (open ? ' open' : '') + '">' +
        '<div class="acc-h" data-g="' + g + '"><span class="dot" style="background:' + GC[g] + '"></span>' +
        '<b>Grade ' + g + (g === 4 ? ' (DEFECT)' : '') + '</b>' +
        '<span class="cnt">' + d.samples + ' samples &middot; ' + d.colors.length + ' colours</span></div>' +
        '<div class="acc-b"><table><tr><th></th><th>#HEX</th><th>wt%</th><th>sample</th></tr>';
      d.colors.forEach(function (c) {
        h += '<tr><td><span class="swatch" style="background:' + c.hex + '"></span></td>' +
          '<td class="mono">' + c.hex + '</td>' +
          '<td>' + Math.round(c.weight * 100) + '%</td>' +
          '<td>#' + c.sample_id + '</td></tr>';
      });
      h += '</table></div></div>';
    });
    $('grades').innerHTML = h;
    Array.prototype.forEach.call(document.querySelectorAll('.acc-h'), function (el) {
      el.onclick = function () {
        var g = +el.dataset.g;
        openGrade = (openGrade === g) ? 0 : g;
        renderGrades();
      };
    });
  }
  function refreshGrades() {
    fetch('/api/grades.php').then(function (r) { return r.json(); })
      .then(function (j) { grades = j.grades; renderGrades(); updateStart(); })
      .catch(function () {});
  }

  /* ---------------- mode / panels ---------------- */
  function setPanel(which) {
    $('m-cal').classList.toggle('on', which === 'cal');
    $('m-run').classList.toggle('on', which === 'run');
    $('panel-cal').classList.toggle('hidden', which !== 'cal');
    $('panel-run').classList.toggle('hidden', which !== 'run');
  }
  $('m-cal').onclick = function () { setPanel('cal'); post('/api/mode', { mode: 'calibrate' }); };
  $('m-run').onclick = function () { setPanel('run'); if (!st || st.mode !== 'run') post('/api/mode', { mode: 'idle' }); };

  /* ---------------- calibrate controls ---------------- */
  document.querySelectorAll('.gbtn').forEach(function (b) {
    b.onclick = function () {
      var g = +b.dataset.g;
      post('/api/calibrate/select', { grade: g }, function () {
        document.querySelectorAll('.gbtn').forEach(function (x) { x.classList.remove('on'); });
        b.classList.add('on');
        $('cal-hint').textContent = 'Tap a bean on the camera to save its colours as Grade ' + g;
      });
    };
  });
  $('btn-bg').onclick = function () {
    confirmModal('Capture the background now?<br><br>The tray must be <b>completely EMPTY</b>.')
      .then(function (y) { if (y) post('/api/calibrate/background', {}, function (j) { if (j.ok) toast('Background saved'); }); });
  };
  $('btn-undo').onclick = function () { post('/api/calibrate/undo', {}, refreshGrades); };
  $('btn-clear').onclick = function () {
    confirmModal('Delete ALL samples of Grade ' + (st ? st.selected_grade : '?') + '?')
      .then(function (y) {
        if (y) post('/api/calibrate/clear', { grade: st ? st.selected_grade : 1 }, refreshGrades);
      });
  };
  $('show-detected').onchange = function () {
    post('/api/calibrate/show-detected', { on: $('show-detected').checked });
  };

  /* ---------------- camera click ---------------- */
  $('cam').addEventListener('pointerdown', function (ev) {
    var r = ev.target.getBoundingClientRect();
    var nx = (ev.clientX - r.left) / r.width;
    var ny = (ev.clientY - r.top) / r.height;
    if (drawing) {
      linePts.push([nx, ny]);
      var d = document.createElement('div');
      d.className = 'cam-dot';
      d.style.left = (nx * 100) + '%';
      d.style.top = (ny * 100) + '%';
      ev.target.parentNode.appendChild(d);
      if (linePts.length === 2) {
        post('/api/line', { p1: linePts[0], p2: linePts[1] }, function (j) {
          if (j.ok) toast('Line saved');
        });
        drawing = false; linePts = [];
        clearDots();
        $('btn-line').textContent = 'Draw line';
      }
      return;
    }
    if (st && st.mode === 'calibrate') {
      post('/api/calibrate/click', { nx: nx, ny: ny }, function (j) {
        if (j.ok) { toast('Saved G' + j.grade, j.hex); refreshGrades(); }
      });
    }
  });
  function clearDots() {
    document.querySelectorAll('.cam-dot').forEach(function (d) { d.remove(); });
  }

  /* ---------------- operate controls ---------------- */
  $('btn-line').onclick = function () {
    drawing = !drawing; linePts = []; clearDots();
    $('btn-line').textContent = drawing ? 'Tap 2 points...' : 'Draw line';
  };
  $('btn-clearline').onclick = function () {
    confirmModal('Clear the sorting line? (a running session will stop)')
      .then(function (y) { if (y) post('/api/line', { clear: true }); });
  };
  $('btn-arm').onclick = function () {
    post('/api/arm', { armed: !(st && st.armed) });
  };
  $('btn-valve').onclick = function () { post('/api/valve/test', {}); };
  $('btn-start').onclick = function () {
    post('/api/mode', { mode: 'run', batch_name: $('batch').value || null }, function (j) {
      if (j.ok) toast('Run started');
    });
  };
  $('btn-stop').onclick = function () {
    confirmModal('Stop the current run?').then(function (y) {
      if (y) post('/api/mode', { mode: 'idle' });
    });
  };

  function updateStart() {
    if (!st) return;
    var running = st.mode === 'run';
    $('btn-start').classList.toggle('hidden', running);
    $('btn-stop').classList.toggle('hidden', !running);
    var reason = '';
    if (!st.has_background) reason = 'no background - capture it in CALIBRATE';
    else if (!grades || !(grades[4].samples > 0 && (grades[1].samples + grades[2].samples + grades[3].samples) > 0))
      reason = 'need samples: grade 4 + at least one of 1-3';
    else if (!st.line) reason = 'no sorting line - tap "Draw line"';
    $('start-reason').textContent = running ? '' : reason;
    $('btn-start').disabled = !!reason;
  }

  /* ---------------- status poll ---------------- */
  function poll() {
    fetch(ENGINE + '/api/status').then(function (r) { return r.json(); })
      .then(function (j) {
        st = j;
        $('engine-down').classList.add('hidden');
        $('engine-chip').className = 'chip ok';
        $('engine-chip').innerHTML = j.mode + ' &middot; ' +
          (j.armed ? '<b style="color:var(--g4)">ARMED</b>' : 'DISARMED') +
          ' &middot; ' + j.fps + 'fps' + (j.sim ? ' &middot; <span class="sim">SIM</span>' : '');
        if (j.mode === 'calibrate') setPanel('cal');
        if (j.mode === 'run') setPanel('run');
        ['g1', 'g2', 'g3', 'g4'].forEach(function (k, i) { $('c' + (i + 1)).textContent = j.counts[k]; });
        $('cb').textContent = j.counts.blown;
        $('btn-arm').textContent = j.armed ? 'DISARM' : 'ARM';
        $('show-detected').checked = !!j.show_detected;
        updateStart();
      })
      .catch(function () {
        st = null;
        $('engine-down').classList.remove('hidden');
        $('engine-chip').className = 'chip bad';
        $('engine-chip').textContent = 'engine offline';
      });
  }
  setInterval(poll, 500); poll();
  setInterval(function () { if (st && st.mode === 'calibrate') refreshGrades(); }, 3000);
  refreshGrades();
})();
