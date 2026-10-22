<?php
declare(strict_types=1);
require dirname(__DIR__) . '/inc/db.php';
header('Content-Type: application/json');

$db = bean_db();

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $in = json_decode(file_get_contents('php://input'), true) ?: [];
    $action = $in['action'] ?? '';
    if ($action === 'delete' && isset($in['id'])) {
        $db->prepare('DELETE FROM belt_colors WHERE id = ?')->execute([(int)$in['id']]);
    } elseif ($action === 'clear_all') {
        $db->exec('DELETE FROM belt_colors');
    } else {
        http_response_code(400);
        exit(json_encode(['ok' => false, 'error' => 'bad action']));
    }
    $r = engine_post('/api/settings/reload');        // engine re-reads belt + model
    echo json_encode(['ok' => true,
                      'engine' => ($r !== null && !empty($r['ok']))]);
    exit;
}

$rows = $db->query(
    'SELECT id, hex, L, a, b, created_at FROM belt_colors ORDER BY id'
)->fetchAll();
$colors = [];
foreach ($rows as $r) {
    $colors[] = [
        'id' => (int)$r['id'], 'hex' => $r['hex'],
        'L' => (float)$r['L'], 'a' => (float)$r['a'], 'b' => (float)$r['b'],
        'created_at' => local_dt($r['created_at']),
    ];
}
echo json_encode(['ok' => true, 'colors' => $colors]);
