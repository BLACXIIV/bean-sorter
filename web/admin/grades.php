<?php
declare(strict_types=1);
require dirname(__DIR__) . '/inc/layout.php';
require_login();

$db = bean_db();
$msg = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    csrf_check();
    if (isset($_POST['del_sample'])) {
        $db->prepare('DELETE FROM samples WHERE id = ?')->execute([(int)$_POST['del_sample']]);
        $msg = 'Sample deleted.';
    } elseif (isset($_POST['clear_grade'])) {
        $db->prepare('DELETE FROM samples WHERE grade = ?')->execute([(int)$_POST['clear_grade']]);
        $msg = 'Grade cleared.';
    } elseif (isset($_POST['del_belt'])) {
        $db->prepare('DELETE FROM belt_colors WHERE id = ?')->execute([(int)$_POST['del_belt']]);
        $msg = 'Conveyor colour deleted.';
    } elseif (isset($_POST['clear_belt'])) {
        $db->exec('DELETE FROM belt_colors');
        $msg = 'Conveyor colours cleared.';
    }
    $r = engine_post('/api/settings/reload');      // engine re-reads model + belt
    if ($r === null || empty($r['ok'])) $msg .= ' Engine not reachable.';
}

$gc = [1 => '#00C800', 2 => '#00C8C8', 3 => '#FFA500', 4 => '#FF0000'];
$beltRows = $db->query(
    'SELECT id, hex, L, a, b, created_at FROM belt_colors ORDER BY id'
)->fetchAll();
$rows = $db->query(
    'SELECT s.id sid, s.grade, s.created_at, gc.hex, gc.L, gc.a, gc.b, gc.weight
     FROM samples s JOIN grade_colors gc ON gc.sample_id = s.id
     ORDER BY s.grade, s.id, gc.id'
)->fetchAll();
$byGrade = [1 => [], 2 => [], 3 => [], 4 => []];
foreach ($rows as $r) $byGrade[(int)$r['grade']][] = $r;

page_head('Grades & Colours', 'admin');
echo '<div class="wrap"><div class="btnrow"><h2 style="flex:1">Grades &amp; Colours</h2>',
     '<a class="btn" href="export.php?view=grades">Export Excel</a></div>';
if ($msg) echo '<p class="warn">' . e($msg) . '</p>';

echo '<details class="acc" open>',
     '<summary class="acc-h"><span class="dot" style="background:#343434"></span>',
     '<b>Conveyor colours</b><span class="cnt">' . count($beltRows) . ' colours</span></summary>',
     '<div class="acc-b" style="display:block;max-height:none"><table>',
     '<tr><th></th><th>#HEX</th><th>L</th><th>a</th><th>b</th><th>captured</th><th></th></tr>';
foreach ($beltRows as $r) {
    echo '<tr><td><span class="swatch" style="background:' . e($r['hex']) . '"></span></td>',
         '<td class="mono">' . e($r['hex']) . '</td><td>' . round($r['L'], 1) . '</td>',
         '<td>' . round($r['a'], 1) . '</td><td>' . round($r['b'], 1) . '</td>',
         '<td>' . e(local_dt($r['created_at'])) . '</td>',
         '<td><form method="post" data-confirm="Delete conveyor colour ' . e($r['hex']) . '?" style="display:inline">',
         '<input type="hidden" name="csrf" value="' . e(csrf_token()) . '">',
         '<input type="hidden" name="del_belt" value="' . (int)$r['id'] . '">',
         '<button class="danger" style="min-height:36px">delete</button></form></td></tr>';
}
echo '</table>',
     '<form method="post" data-confirm="Delete ALL conveyor colours?">',
     '<input type="hidden" name="csrf" value="' . e(csrf_token()) . '">',
     '<input type="hidden" name="clear_belt" value="1">',
     '<button class="danger">Clear all conveyor colours</button></form></div></details>';

foreach ([1, 2, 3, 4] as $g) {
    $samples = array_unique(array_column($byGrade[$g], 'sid'));
    echo '<details class="acc"' . ($g === 4 ? ' open' : '') . '>',
         '<summary class="acc-h"><span class="dot" style="background:' . $gc[$g] . '"></span>',
         '<b>Grade ' . $g . ($g === 4 ? ' (DEFECT)' : '') . '</b>',
         '<span class="cnt">' . count($samples) . ' samples &middot; ' . count($byGrade[$g]) . ' colours</span></summary>',
         '<div class="acc-b" style="display:block;max-height:none"><table>',
         '<tr><th></th><th>#HEX</th><th>L</th><th>a</th><th>b</th><th>wt%</th><th>sample</th><th>captured</th><th></th></tr>';
    $seen = [];
    foreach ($byGrade[$g] as $r) {
        $del = '';
        if (empty($seen[$r['sid']])) {
            $seen[$r['sid']] = true;
            $del = '<form method="post" data-confirm="Delete sample #' . (int)$r['sid'] . ' and its colours?" style="display:inline">' .
                   '<input type="hidden" name="csrf" value="' . e(csrf_token()) . '">' .
                   '<input type="hidden" name="del_sample" value="' . (int)$r['sid'] . '">' .
                   '<button class="danger" style="min-height:36px">delete</button></form>';
        }
        echo '<tr><td><span class="swatch" style="background:' . e($r['hex']) . '"></span></td>',
             '<td class="mono">' . e($r['hex']) . '</td><td>' . round($r['L'], 1) . '</td>',
             '<td>' . round($r['a'], 1) . '</td><td>' . round($r['b'], 1) . '</td>',
             '<td>' . round($r['weight'] * 100) . '%</td><td>#' . (int)$r['sid'] . '</td>',
             '<td>' . e(local_dt($r['created_at'])) . '</td><td>' . $del . '</td></tr>';
    }
    echo '</table>',
         '<form method="post" data-confirm="Delete ALL samples of Grade ' . $g . '?">',
         '<input type="hidden" name="csrf" value="' . e(csrf_token()) . '">',
         '<input type="hidden" name="clear_grade" value="' . $g . '">',
         '<button class="danger">Clear grade ' . $g . '</button></form></div></details>';
}
echo '</div>';
page_foot();
