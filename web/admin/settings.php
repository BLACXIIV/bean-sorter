<?php
declare(strict_types=1);
require dirname(__DIR__) . '/inc/layout.php';
require_login();

// name => [min, max, step, default]   (defaults mirror config.py)
$TUNABLES = [
    'DEFECT_BIAS'        => [0.5, 1.5, 0.05, 0.85],
    'UNKNOWN_DE'         => [10, 60, 1, 30.0],
    'VALVE_DELAY_S'      => [0, 2, 0.05, 0.0],
    'VALVE_PULSE_S'      => [0.01, 0.5, 0.01, 0.05],   // capped by VALVE_MAX_PULSE_S
    'DIFF_THRESHOLD'     => [10, 80, 1, 30],
    'MIN_BEAN_AREA'      => [50, 5000, 10, 400],
    'MAX_BEAN_AREA'      => [5000, 100000, 500, 30000],
    'DEFECT_VOTE_RATIO'  => [0.1, 0.9, 0.05, 0.3],
    'BELT_TOLERANCE'     => [5, 50, 0.5, 18.0],
    'BELT_L_WEIGHT'      => [0.1, 1.0, 0.05, 0.5],
    'TRACK_MAX_COAST_S'  => [0.1, 3.0, 0.05, 0.6],
    'TRACK_REID_DE'      => [3, 40, 0.5, 12.0],
    'DEDUPE_RADIUS_PX'   => [5, 100, 1, 25],
    'DEDUPE_WINDOW_S'    => [0.02, 1.0, 0.01, 0.15],
];

$msg = ''; $err = '';
$saved = setting_get('tunables', []) ?: [];
$current = [];
foreach ($TUNABLES as $k => $t) {
    $current[$k] = isset($saved[$k]) && is_numeric($saved[$k]) ? (float)$saved[$k] : $t[3];
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    csrf_check();
    if (isset($_POST['reset'])) {
        foreach ($TUNABLES as $k => $t) $current[$k] = $t[3];
        setting_set('tunables', $current);
        $msg = 'Reset to defaults.';
    } else {
        $bad = [];
        foreach ($TUNABLES as $k => $t) {
            $v = $_POST[$k] ?? '';
            if (!is_numeric($v) || (float)$v < $t[0] || (float)$v > $t[1]) {
                $bad[] = "$k must be {$t[0]}-{$t[1]}";
            } else {
                $current[$k] = (float)$v;
            }
        }
        if ($bad) {
            $err = implode(' ', $bad);
        } else {
            setting_set('tunables', $current);
            $msg = 'Settings saved.';
        }
    }
    if (!$err) {
        $r = engine_post('/api/settings/reload');
        $msg .= ($r === null || empty($r['ok']))
            ? ' Engine not reachable - values apply on next engine start.'
            : ' Engine reloaded.';
    }
}

page_head('Settings', 'admin');
echo '<div class="wrap"><h2>Settings</h2>';
if ($msg) echo '<p class="warn">' . e($msg) . '</p>';
if ($err) echo '<p class="err">' . e($err) . '</p>';
echo '<form method="post" class="card"><table><tr><th>Setting</th><th>Value</th><th>Range</th><th>Default</th></tr>';
foreach ($TUNABLES as $k => $t) {
    echo '<tr><td class="mono">' . e($k) . '</td>',
         '<td><input type="number" name="' . e($k) . '" value="' . e($current[$k]) . '"',
         ' min="' . $t[0] . '" max="' . $t[1] . '" step="' . $t[2] . '" required style="width:130px"></td>',
         '<td class="muted">' . $t[0] . ' - ' . $t[1] . '</td>',
         '<td class="muted">' . $t[3] . '</td></tr>';
}
echo '</table><div class="btnrow" style="margin-top:10px">',
     '<input type="hidden" name="csrf" value="' . e(csrf_token()) . '">',
     '<button class="primary">Save &amp; apply</button></form>',
     '<form method="post" data-confirm="Reset all settings to defaults?">',
     '<input type="hidden" name="csrf" value="' . e(csrf_token()) . '">',
     '<input type="hidden" name="reset" value="1"><button>Reset to defaults</button></form></div></div>';
page_foot();
