<?php
declare(strict_types=1);
require __DIR__ . '/inc/layout.php';
page_head('Operate', 'operate', false);
?>
<div id="engine-down" class="banner hidden">Engine not running</div>
<div class="op-grid">
  <div class="op-left">
    <div class="cam-wrap"><img id="cam" alt="camera stream" draggable="false">
      <div class="cam-stats">
        <div><b class="n" id="c1" style="color:var(--g1)">0</b><span>G1</span></div>
        <div><b class="n" id="c2" style="color:var(--g2)">0</b><span>G2</span></div>
        <div><b class="n" id="c3" style="color:var(--g3)">0</b><span>G3</span></div>
        <div><b class="n" id="c4" style="color:var(--g4)">0</b><span>G4</span></div>
        <div><b class="n" id="cb">0</b><span>Blown</span></div>
        <div class="c-trkline" id="track-stats"></div>
      </div>
      <div id="belt-warn" class="warnstrip hidden"></div>
    </div>
    <div class="controls">
      <div class="seg">
        <button id="m-cal" class="on">CALIBRATE</button>
        <button id="m-run">OPERATE</button>
      </div>

      <div id="panel-cal">
        <div class="btnrow">
          <button class="gbtn" data-t="1">G1</button>
          <button class="gbtn" data-t="2">G2</button>
          <button class="gbtn" data-t="3">G3</button>
          <button class="gbtn" data-t="4">G4 DEFECT</button>
          <button class="gbtn belt" data-t="belt">BELT<span id="belt-strip"></span></button>
        </div>
        <div class="btnrow">
          <button id="btn-bg" class="secondary">Background (optional)</button>
          <button id="btn-undo">Undo</button>
          <button id="btn-clear" class="danger">Clear</button>
          <label class="toggle" style="margin-left:auto">
            <input type="checkbox" id="show-detected">Show detected</label>
        </div>
        <p class="hint" id="cal-hint">Tap a bean on the camera to save its colours as Grade 1</p>
      </div>

      <div id="panel-run" class="hidden">
        <div class="btnrow">
          <button id="btn-line">Draw line</button>
          <button id="btn-clearline" class="danger">Clear line</button>
          <button id="btn-arm">ARM</button>
          <button id="btn-valve">Test valve</button>
          <button id="btn-tracks">Tracks</button>
        </div>
        <div class="btnrow">
          <input type="text" id="batch" placeholder="Batch name (optional)" style="flex:1">
          <button id="btn-start" class="primary big" style="flex:1">START</button>
          <button id="btn-stop" class="danger big hidden" style="flex:1">STOP</button>
        </div>
        <p class="reason" id="start-reason"></p>
      </div>
    </div>
  </div>
  <aside class="op-right"><h2>GRADE LIST</h2><div id="grades"></div></aside>
</div>
<div id="toast"></div>
<div id="modal-root"></div>
<script src="/assets/js/operate.js"></script>
<?php page_foot(); ?>
