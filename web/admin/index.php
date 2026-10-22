<?php
declare(strict_types=1);
require dirname(__DIR__) . '/inc/layout.php';
require_login();

$db = bean_db();
$day = date('Y-m-d');
$u0 = utc_dt($day . ' 00:00:00');
$u1 = utc_dt(date('Y-m-d', strtotime($day . ' +1 day')) . ' 00:00:00');

$tot = [1 => 0, 2 => 0, 3 => 0, 4 => 0];
$blown = 0;
$st = $db->prepare('SELECT grade, COUNT(*) c, COALESCE(SUM(blown),0) b
                    FROM sort_log WHERE ts >= ? AND ts < ? GROUP BY grade');
$st->execute([$u0, $u1]);
foreach ($st as $r) {
    $tot[(int)$r['grade']] = (int)$r['c'];
    $blown += (int)$r['b'];
}
$total = array_sum($tot);
$defect = $total ? round(100 * $tot[4] / $total, 1) : 0;

$runs = $db->prepare('SELECT COUNT(*) FROM run_sessions WHERE started_at >= ? AND started_at < ?');
$runs->execute([$u0, $u1]);
$runsToday = (int)$runs->fetchColumn();

$last = $db->query('SELECT * FROM run_sessions ORDER BY id DESC LIMIT 1')->fetch();

$max = max(1, max($tot));
$bars = '';
$gx = [1 => '#00C800', 2 => '#00C8C8', 3 => '#FFA500', 4 => '#FF0000'];
foreach ([1, 2, 3, 4] as $g) {
    $h = (int)round(160 * $tot[$g] / $max);
    $x = 20 + ($g - 1) * 120;
    $bars .= '<rect x="' . $x . '" y="' . (180 - $h) . '" width="80" height="' . $h . '" fill="' . $gx[$g] . '" rx="4"/>'
           . '<text x="' . ($x + 40) . '" y="' . (170 - $h) . '" fill="#E6E6E6" font-size="18" font-weight="700" text-anchor="middle">' . $tot[$g] . '</text>'
           . '<text x="' . ($x + 40) . '" y="200" fill="#9A9A9A" font-size="14" text-anchor="middle">G' . $g . '</text>';
}

page_head('Dashboard', 'admin');
echo '<div class="wrap"><h2>Dashboard - ' . e($day) . ' (local)</h2>';
echo '<div class="grid4" style="margin-bottom:12px">';
foreach ([['Beans today', $total], ['Defects (G4)', $tot[4]], ['Defect %', $defect . '%'], ['Runs today', $runsToday]] as $i => $s) {
    $col = $i === 1 || $i === 2 ? ' style="color:var(--g4)"' : '';
    echo '<div class="stat"><div class="n"' . $col . '>' . e($s[1]) . '</div><div class="l">' . e($s[0]) . '</div></div>';
}
echo '</div><div class="grid2">';

echo '<div class="card"><h2>Today per grade</h2><svg class="chart" viewBox="0 0 480 210">' . $bars . '</svg></div>';

echo '<div><div class="card"><h2>Engine</h2><div id="engine-card"><p class="muted">checking...</p></div>';
echo '</div><div class="card"><h2>Last run</h2>';
if (!$last) {
    echo '<p class="muted">No runs yet.</p>';
} else {
    $lt = $last['g1'] + $last['g2'] + $last['g3'] + $last['g4'];
    echo '<table><tr><td>Run</td><td>#' . (int)$last['id'] . ($last['batch_name'] ? ' - ' . e($last['batch_name']) : '') . '</td></tr>',
         '<tr><td>Started</td><td>' . e(local_dt($last['started_at'])) . '</td></tr>',
         '<tr><td>Ended</td><td>' . e(local_dt($last['ended_at'])) . '</td></tr>',
         '<tr><td>Beans</td><td>' . $lt . ' (blown ' . (int)$last['blown'] . ')</td></tr></table>';
}
echo '</div><p><a class="btn" href="reports.php">Open reports</a> <a class="btn" href="logout.php">Log out</a></p>';
echo '</div></div>';
page_foot();
