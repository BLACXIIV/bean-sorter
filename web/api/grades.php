<?php
declare(strict_types=1);
require dirname(__DIR__) . '/inc/db.php';
header('Content-Type: application/json');

$rows = bean_db()->query(
    'SELECT gc.sample_id, gc.grade, gc.hex, gc.L, gc.a, gc.b, gc.weight,
            s.created_at, s.source
     FROM grade_colors gc JOIN samples s ON s.id = gc.sample_id
     ORDER BY gc.grade, gc.sample_id, gc.id'
)->fetchAll();

$out = [];
foreach ([1, 2, 3, 4] as $g) {
    $out[$g] = ['samples' => 0, 'colors' => []];
}
$seen = [];
foreach ($rows as $r) {
    $g = (int)$r['grade'];
    $sid = (int)$r['sample_id'];
    if (empty($seen[$g][$sid])) {
        $seen[$g][$sid] = true;
        $out[$g]['samples']++;
    }
    $out[$g]['colors'][] = [
        'hex' => $r['hex'], 'L' => (float)$r['L'], 'a' => (float)$r['a'],
        'b' => (float)$r['b'], 'weight' => (float)$r['weight'],
        'sample_id' => $sid, 'created_at' => local_dt($r['created_at']),
        'source' => $r['source'] ?? 'bean',
    ];
}
echo json_encode(['ok' => true, 'grades' => $out]);
