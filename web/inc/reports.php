<?php
declare(strict_types=1);
require_once __DIR__ . '/db.php';

// Reads ?from=&to=&run= (local dates) -> ['where'=>sql, 'args'=>[...]] on a
// timestamp column. Returns [$where, $args, $filters].
function report_filters(string $col): array {
    $from = preg_match('/^\d{4}-\d{2}-\d{2}$/', $_GET['from'] ?? '') ? $_GET['from'] : null;
    $to   = preg_match('/^\d{4}-\d{2}-\d{2}$/', $_GET['to'] ?? '') ? $_GET['to'] : null;
    $run  = ctype_digit((string)($_GET['run'] ?? '')) ? (int)$_GET['run'] : null;
    $w = []; $a = [];
    if ($from) { $w[] = "$col >= ?"; $a[] = utc_dt($from . ' 00:00:00'); }
    if ($to)   { $w[] = "$col < ?";  $a[] = utc_dt(date('Y-m-d', strtotime($to . ' +1 day')) . ' 00:00:00'); }
    $where = $w ? 'WHERE ' . implode(' AND ', $w) : '';
    return [$where, $a, ['from' => $from, 'to' => $to, 'run' => $run]];
}

function run_rows(PDO $db, ?string $from, ?string $to, ?int $run): array {
    $w = []; $a = [];
    if ($from) { $w[] = 'started_at >= ?'; $a[] = utc_dt($from . ' 00:00:00'); }
    if ($to)   { $w[] = 'started_at < ?';  $a[] = utc_dt(date('Y-m-d', strtotime($to . ' +1 day')) . ' 00:00:00'); }
    if ($run)  { $w[] = 'id = ?';          $a[] = $run; }
    $where = $w ? 'WHERE ' . implode(' AND ', $w) : '';
    $st = $db->prepare("SELECT * FROM run_sessions $where ORDER BY id DESC");
    $st->execute($a);
    return $st->fetchAll();
}

function duration_s(?string $startUtc, ?string $endUtc): string {
    if (!$startUtc || !$endUtc) return '-';
    $s = strtotime($endUtc) - strtotime($startUtc);
    return sprintf('%d:%02d', intdiv($s, 60), $s % 60);
}

function defect_pct(int $g4, int $total): string {
    return $total ? round(100 * $g4 / $total, 1) . '%' : '-';
}
