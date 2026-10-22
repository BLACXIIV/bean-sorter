/* Operate screen: 500 ms status poll, calibrate + run controls, line drawing. */
(function () {
  'use strict';
  window.__op = 1;                        // tells admin.js not to double-poll the chip
  var ENGINE = 'http://' + location.hostname + ':5000';
  var $ = function (id) { return document.getElementById(id); };

  var st = null;              // last /api/status
  var grades = null;          // last /api/grades.php
  var belt = null;            // last /api/belt.php
  var drawing = false;
  var linePts = [];

  $('cam').src = ENGINE + '/stream.mjpg';
  addEventListener('pagehide', function () { $('cam').src = ''; });   // free the stream
  var seg = document.createElement('span');     // detection-source chip in top bar
  seg.className = 'chip';
  seg.id = 'seg-chip';
  $('engine-chip').parentNode.insertBefore(seg, $('engine-chip').nextSibling);

  function timedFetch(url, opts, ms) {          // AbortController timeout
    var ac = new AbortController();
    var t = setTimeout(function () { ac.abort(); }, ms);
    return fetch(url, Object.assign({ signal: ac.signal }, opts || {}))
      .finally(function () { clearTimeout(t); });
  }

  function post(path, obj, cb, label) {
    var t0 = performance.now();
    timedFetch(ENGINE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(obj || {})
    }, 6000).then(function (r) {
      return r.text().then(function (txt) {
        var j = null;
        try { j = JSON.parse(txt); } catch (e) {}
        if (!r.ok) {
          console.warn('post ' + path + ' HTTP ' + r.status + ' ' +
            Math.round(performance.now() - t0) + 'ms body=' + txt.slice(0, 120));
          toast((label || path) + ' failed: HTTP ' + r.status);
          return;
        }
        if (!j) {
          console.warn('post ' + path + ' bad JSON ' +
            Math.round(performance.now() - t0) + 'ms');
          toast((label || path) + ' failed: bad reply');
          return;
        }
        if (cb) cb(j);
        if (!j.ok && j.error) toast(j.error);
      });
    }).catch(function (e) {
      console.warn('post ' + path + ' ' + e.name + ' ' + e.message + ' ' +
        Math.round(performance.now() - t0) + 'ms');
      toast((label || path) + ' failed: ' + e.message);
    });
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

  /* ---------------- accordion (conveyor + grades) ---------------- */
  var GC = { 1: 'var(--g1)', 2: 'var(--g2)', 3: 'var(--g3)', 4: 'var(--g4)' };
  var openKey = 1;            // 'belt' | 1..4, one open at a time
  function renderGrades() {
    if (!grades) return;
    var h = '';
    if (belt) {
      var open = openKey === 'belt';
      h += '<div class="acc' + (open ? ' open' : '') + '">' +
        '<div class="acc-h" data-k="belt"><span class="dot" style="background:#343434"></span>' +
        '<b>Conveyor</b><span class="cnt">' + belt.colors.length + ' colours</span></div>' +
        '<div class="acc-b"><table><tr><th></th><th>#HEX</th><th>L a b</th><th></th></tr>';
      belt.colors.forEach(function (c) {
        h += '<tr><td><span class="swatch" style="background:' + c.hex + '"></span></td>' +
          '<td class="mono">' + c.hex + '</td>' +
          '<td>' + c.L.toFixed(0) + ' ' + c.a.toFixed(0) + ' ' + c.b.toFixed(0) + '</td>' +
          '<td><button class="belt-del danger" data-id="' + c.id + '" ' +
          'style="min-height:36px;min-width:44px">X</button></td></tr>';
      });
      h += '</table>' +
        (belt.colors.length ? '<button id="belt-clear" class="danger" style="margin:6px">Clear all</button>' : '') +
        '</div></div>';
    }
    [1, 2, 3, 4].forEach(function (g) {
      var d = grades[g], open = g === openKey;
      h += '<div class="acc' + (open ? ' open' : '') + '">' +
        '<div class="acc-h" data-k="' + g + '"><span class="dot" style="background:' + GC[g] + '"></span>' +
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
        var k = el.dataset.k === 'belt' ? 'belt' : +el.dataset.k;
        openKey = (openKey === k) ? 0 : k;
        renderGrades();
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('.belt-del'), function (el) {
      el.onclick = function () {
        beltPost({ action: 'delete', id: +el.dataset.id });
      };
    });
    var bc = $('belt-clear');
    if (bc) bc.onclick = function () {
      confirmModal('Delete ALL conveyor colours?').then(function (y) {
        if (y) beltPost({ action: 'clear_all' });
      });
    };
  }
  function refreshGrades() {
    fetch('/api/grades.php').then(function (r) { return r.json(); })
      .then(function (j) { grades = j.grades; renderGrades(); updateStart(); })
      .catch(function () {});
  }
  function refreshBelt() {
    fetch('/api/belt.php').then(function (r) { return r.json(); })
      .then(function (j) {
        belt = j;
        var strip = '';
        j.colors.slice(0, 8).forEach(function (c) {
          strip += '<i style="background:' + c.hex + '"></i>';
        });
        $('belt-strip').innerHTML = strip;
        renderGrades();
      }).catch(function () {});
  }
  function beltPost(obj) {
    fetch('/api/belt.php', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(obj)
    }).then(function (r) { return r.json(); })
      .then(function () { refreshBelt(); })
      .catch(function () { toast('belt update failed'); });
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
  var HINTS = { belt: 'Tap 3-5 empty belt spots (bright, dark and shadowed areas)' };
  function hintFor(t) {
    return t === 'belt' ? HINTS.belt
      : 'Tap a bean on the camera to save its colours as Grade ' + t;
  }
  document.querySelectorAll('.gbtn').forEach(function (b) {
    b.onclick = function () {
      var t = b.dataset.t === 'belt' ? 'belt' : +b.dataset.t;
      post('/api/calibrate/select', { target: t }, function (j) {
        if (!j.ok) return;
        document.querySelectorAll('.gbtn').forEach(function (x) { x.classList.remove('on'); });
        b.classList.add('on');
        $('cal-hint').textContent = hintFor(t);
      });
    };
  });
  $('btn-bg').onclick = function () {
    confirmModal('Capture the background now?<br><br>The tray must be <b>completely EMPTY</b>.')
      .then(function (y) { if (y) post('/api/calibrate/background', {}, function (j) { if (j.ok) toast('Background saved'); }); });
  };
  $('btn-undo').onclick = function () {
    post('/api/calibrate/undo', {}, function () { refreshGrades(); refreshBelt(); });
  };
  $('btn-clear').onclick = function () {
    var t = st ? st.selected_target : 1;
    var q = t === 'belt' ? 'Delete ALL conveyor colours?'
      : 'Delete ALL samples of Grade ' + t + '?';
    confirmModal(q).then(function (y) {
      if (y) post('/api/calibrate/clear', { target: t },
        function () { refreshGrades(); refreshBelt(); });
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
        if (j.ok) {
          if (j.note) toast(j.note);
          else if (j.id) toast('Belt colour saved', [j.hex]);
          else toast('Saved G' + j.grade, j.hex);
          refreshGrades(); refreshBelt();
        }
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
    if (st.segmentation === 'none')
      reason = 'no detection source - tap BELT colours or capture a background in CALIBRATE';
    else if (!grades || !(grades[4].samples > 0 && (grades[1].samples + grades[2].samples + grades[3].samples) > 0))
      reason = 'need samples: grade 4 + at least one of 1-3';
    else if (!st.line) reason = 'no sorting line - tap "Draw line"';
    $('start-reason').textContent = running ? '' : reason;
    $('btn-start').disabled = !!reason;
  }

  /* ---------------- status poll (chained, timeout, 3-strike banner) -------- */
  var fails = 0;
  function poll() {
    var t0 = performance.now();
    timedFetch(ENGINE + '/api/status', null, 3500)
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (j) {
        fails = 0;
        st = j;
        $('engine-down').classList.add('hidden');
        $('engine-chip').className = 'chip ok';
        $('engine-chip').innerHTML = j.mode + ' &middot; ' +
          (j.armed ? '<b style="color:var(--g4)">ARMED</b>' : 'DISARMED') +
          ' &middot; ' + j.fps + 'fps &middot; ' +
          '<span class="' + (j.camera_source !== 'pi' || j.valve !== 'real' ? 'sim' : '') + '">' +
          'CAM: ' + (j.camera_source || '?').toUpperCase() +
          ' &middot; VALVE: ' + (j.valve || '?').toUpperCase() + '</span>';
        $('seg-chip').textContent = 'Detection: ' +
          (j.segmentation === 'belt_color' ? 'Conveyor color'
            : j.segmentation === 'background' ? 'Background' : 'None');
        var w = $('belt-warn');
        if (j.warnings && j.warnings.length) {
          w.innerHTML = j.warnings.join('<br>');
          w.classList.remove('hidden');
        } else {
          w.classList.add('hidden');
        }
        document.querySelectorAll('.gbtn').forEach(function (x) {
          var t = x.dataset.t === 'belt' ? 'belt' : +x.dataset.t;
          x.classList.toggle('on', t === j.selected_target);
        });
        if (j.mode === 'calibrate') setPanel('cal');
        if (j.mode === 'run') setPanel('run');
        ['g1', 'g2', 'g3', 'g4'].forEach(function (k, i) { $('c' + (i + 1)).textContent = j.counts[k]; });
        $('cb').textContent = j.counts.blown;
        $('btn-arm').textContent = j.armed ? 'DISARM' : 'ARM';
        $('show-detected').checked = !!j.show_detected;
        updateStart();
      })
      .catch(function (e) {
        console.warn('poll /api/status ' + e.name + ' ' + e.message + ' ' +
          Math.round(performance.now() - t0) + 'ms');
        st = null;
        fails++;
        if (fails >= 3) {                     // banner only on 3 strikes
          $('engine-down').classList.remove('hidden');
          $('engine-chip').className = 'chip bad';
          $('engine-chip').textContent = 'engine offline';
        }
      })
      .finally(function () { setTimeout(poll, 500); });   // never overlap
  }
  poll();
  setInterval(function () {
    if (st && st.mode === 'calibrate') { refreshGrades(); refreshBelt(); }
  }, 3000);
  refreshGrades(); refreshBelt();
})();
