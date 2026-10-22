<?php
declare(strict_types=1);
require dirname(__DIR__) . '/inc/layout.php';
require dirname(__DIR__) . '/inc/reports.php';
require_login();

$db = bean_db();
$tab = in_array($_GET['tab'] ?? '', ['runs', 'daily', 'log'], true) ? $_GET['tab'] : 'runs';
[, , $f] = report_filters('started_at');
[$from, $to, $run] = [$f['from'], $f['to'], $f['run']];
$qs = 'from=' . urlencode((string)$from) . '&to=' . urlencode((string)$to) . '&run=' . urlencode((string)$run);

page_head('Reports', 'admin');
echo '<div class="wrap"><h2>Reports</h2>';

echo '<form class="filters" method="get"><input type="hidden" name="tab" value="' . e($tab) . '">',
     '<div><label>From</label><input type="date" name="from" value="' . e($from) . '"></div>',
     '<div><label>To</label><input type="date" name="to" value="' . e($to) . '"></div>',
     '<div><label>Run #</label><input type="number" name="run" min="1" value="' . e($run) . '" placeholder="all"></div>',
     '<button class="primary">Filter</button></form>';

echo '<div class="tabs">',
     '<a href="?tab=runs&' . $qs . '"' . ($tab === 'runs' ? ' class="on"' : '') . '>Runs</a>',
     '<a href="?tab=daily&' . $qs . '"' . ($tab === 'daily' ? ' class="on"' : '') . '>Daily summary</a>',
     '<a href="?tab=log&' . $qs . '"' . ($tab === 'log' ? ' class="on"' : '') . '>Sort log</a></div>';

echo '<div class="btnrow">',
     '<a class="btn" href="export.php?view=current&tab=' . e($tab) . '&' . $qs . '">Export Excel (this view)</a>',
     '<a class="btn" href="export.php?view=all">Export ALL</a></div>';

if ($tab === 'runs') {
    $rows = run_rows($db, $from, $to, $run);
    echo '<div class="card"><table><tr><th>Run #</th><th>Batch</th><th>Start</th><th>End</th>',
         '<th>Duration</th><th>Total</th><th>G1</th><th>G2</th><th>G3</th><th>G4</th><th>Defect %</th><th>Blown</th></tr>';
    foreach ($rows as $r) {
        $t = $r['g1'] + $r['g2'] + $r['g3'] + $r['g4'];
        echo '<tr><td>#' . (int)$r['id'] . '</td><td>' . e($r['batch_name']) . '</td>',
             '<td>' . e(local_dt($r['started_at'])) . '</td><td>' . e(local_dt($r['ended_at'])) . '</td>',
             '<td>' . e(duration_s($r['started_at'], $r['ended_at'])) . '</td><td>' . $t . '</td>',
             '<td>' . (int)$r['g1'] . '</td><td>' . (int)$r['g2'] . '</td><td>' . (int)$r['g3'] . '</td>',
             '<td>' . (int)$r['g4'] . '</td><td>' . e(defect_pct((int)$r['g4'], $t)) . '</td>',
             '<td>' . (int)$r['blown'] . '</td></tr>';
    }
    echo '</table></div>';
} elseif ($tab === 'daily') {
    $days = [];
    foreach (run_rows($db, $from, $to, $run) as $r) {
        $d = local_day($r['started_at']);
        $days[$d] = $days[$d] ?? ['runs' => 0, 'total' => 0, 'g1' => 0, 'g2' => 0, 'g3' => 0, 'g4' => 0, 'blown' => 0];
        $days[$d]['runs']++;
        $days[$d]['total'] += $r['g1'] + $r['g2'] + $r['g3'] + $r['g4'];
        foreach ([1, 2, 3, 4] as $g) $days[$d]["g$g"] += $r["g$g"];
        $days[$d]['blown'] += $r['blown'];
    }
    krsort($days);
    echo '<div class="card"><table><tr><th>Date</th><th>Runs</th><th>Total</th>',
         '<th>G1</th><th>G2</th><th>G3</th><th>G4</th><th>Defect %</th><th>Blown</th></tr>';
    foreach ($days as $d => $r) {
        echo '<tr><td>' . e($d) . '</td><td>' . $r['runs'] . '</td><td>' . $r['total'] . '</td>',
             '<td>' . $r['g1'] . '</td><td>' . $r['g2'] . '</td><td>' . $r['g3'] . '</td><td>' . $r['g4'] . '</td>',
             '<td>' . e(defect_pct($r['g4'], $r['total'])) . '</td><td>' . $r['blown'] . '</td></tr>';
    }
    echo '</table></div>';
} else {
    $page = max(1, (int)($_GET['page'] ?? 1));
    $w = []; $a = [];
    if ($from) { $w[] = 'ts >= ?'; $a[] = utc_dt($from . ' 00:00:00'); }
    if ($to)   { $w[] = 'ts < ?';  $a[] = utc_dt(date('Y-m-d', strtotime($to . ' +1 day')) . ' 00:00:00'); }
    if ($run)  { $w[] = 'session_id = ?'; $a[] = $run; }
    $where = $w ? 'WHERE ' . implode(' AND ', $w) : '';
    $cnt = $db->prepare("SELECT COUNT(*) FROM sort_log $where");
    $cnt->execute($a);
    $pages = max(1, (int)ceil($cnt->fetchColumn() / 50));
    $page = min($page, $pages);
    $st = $db->prepare("SELECT * FROM sort_log $where ORDER BY id DESC LIMIT 50 OFFSET " . (($page - 1) * 50));
    $st->execute($a);
    echo '<div class="card"><table><tr><th>Time</th><th>Run #</th><th>Grade</th><th>Blown</th></tr>';
    foreach ($st as $r) {
        echo '<tr><td>' . e(local_dt($r['ts'])) . '</td><td>' . e($r['session_id'] ?? '-') . '</td>',
             '<td>G' . (int)$r['grade'] . '</td><td>' . ($r['blown'] ? 'YES' : '-') . '</td></tr>';
    }
    echo '</table><div class="pager">';
    if ($page > 1) echo '<a href="?tab=log&' . $qs . '&page=' . ($page - 1) . '">&larr; Prev</a>';
    echo '<span class="muted">page ' . $page . ' / ' . $pages . '</span>';
    if ($page < $pages) echo '<a href="?tab=log&' . $qs . '&page=' . ($page + 1) . '">Next &rarr;</a>';
    echo '</div></div>';
}
echo '</div>';
page_foot();
