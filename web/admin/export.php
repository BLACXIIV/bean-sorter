<?php
declare(strict_types=1);
require dirname(__DIR__) . '/inc/layout.php';
require dirname(__DIR__) . '/inc/reports.php';
require dirname(__DIR__) . '/vendor/autoload.php';   // web/vendor (committed)
require_login();

use PhpOffice\PhpSpreadsheet\Cell\Coordinate;
use PhpOffice\PhpSpreadsheet\Spreadsheet;
use PhpOffice\PhpSpreadsheet\Style\Fill;
use PhpOffice\PhpSpreadsheet\Writer\Xlsx;

$db = bean_db();
[, , $f] = report_filters('started_at');

function sheet_style($sheet, int $ncols): void {
    $sheet->freezePane('A2');
    $range = 'A1:' . Coordinate::stringFromColumnIndex($ncols) . '1';
    $sheet->getStyle($range)->applyFromArray([
        'font' => ['bold' => true, 'color' => ['rgb' => 'FFFFFF']],
        'fill' => ['fillType' => Fill::FILL_SOLID, 'startColor' => ['rgb' => '343434']],
    ]);
    for ($i = 1; $i <= $ncols; $i++) {
        $sheet->getColumnDimension(Coordinate::stringFromColumnIndex($i))->setAutoSize(true);
    }
}

function hex_cell($sheet, string $cell, string $hex): void {
    $rgb = ltrim($hex, '#');
    $r = hexdec(substr($rgb, 0, 2)); $g = hexdec(substr($rgb, 2, 2)); $b = hexdec(substr($rgb, 4, 2));
    $lum = 0.299 * $r + 0.587 * $g + 0.114 * $b;
    $sheet->getStyle($cell)->applyFromArray([
        'fill' => ['fillType' => Fill::FILL_SOLID, 'startColor' => ['rgb' => $rgb]],
        'font' => ['color' => ['rgb' => $lum > 140 ? '000000' : 'FFFFFF']],
    ]);
}

function write_runs($sheet, array $rows): void {
    $head = ['Run #', 'Batch', 'Start', 'End', 'Duration', 'Total', 'G1', 'G2', 'G3', 'G4', 'Defect %', 'Blown'];
    $sheet->fromArray($head, null, 'A1');
    $r = 2;
    foreach ($rows as $x) {
        $t = $x['g1'] + $x['g2'] + $x['g3'] + $x['g4'];
        $sheet->fromArray([
            (int)$x['id'], $x['batch_name'], local_dt($x['started_at']), local_dt($x['ended_at']),
            duration_s($x['started_at'], $x['ended_at']), $t,
            (int)$x['g1'], (int)$x['g2'], (int)$x['g3'], (int)$x['g4'],
            $t ? round(100 * $x['g4'] / $t, 1) : 0, (int)$x['blown'],
        ], null, 'A' . $r++);
    }
    sheet_style($sheet, count($head));
}

function write_daily($sheet, array $rows): void {
    $head = ['Date', 'Runs', 'Total', 'G1', 'G2', 'G3', 'G4', 'Defect %', 'Blown'];
    $sheet->fromArray($head, null, 'A1');
    $days = [];
    foreach ($rows as $x) {
        $d = local_day($x['started_at']);
        $days[$d] = $days[$d] ?? ['runs' => 0, 'total' => 0, 'g1' => 0, 'g2' => 0, 'g3' => 0, 'g4' => 0, 'blown' => 0];
        $days[$d]['runs']++;
        $days[$d]['total'] += $x['g1'] + $x['g2'] + $x['g3'] + $x['g4'];
        foreach ([1, 2, 3, 4] as $g) $days[$d]["g$g"] += $x["g$g"];
        $days[$d]['blown'] += $x['blown'];
    }
    krsort($days);
    $r = 2;
    foreach ($days as $d => $x) {
        $sheet->fromArray([$d, $x['runs'], $x['total'], $x['g1'], $x['g2'], $x['g3'], $x['g4'],
            $x['total'] ? round(100 * $x['g4'] / $x['total'], 1) : 0, $x['blown']], null, 'A' . $r++);
    }
    sheet_style($sheet, count($head));
}

function write_colors($sheet, array $rows): void {
    $head = ['Grade', 'Sample #', 'Hex', 'L', 'a', 'b', 'Weight %', 'Captured at'];
    $sheet->fromArray($head, null, 'A1');
    $r = 2;
    foreach ($rows as $x) {
        $sheet->fromArray([
            (int)$x['grade'], (int)$x['sample_id'], $x['hex'],
            round($x['L'], 2), round($x['a'], 2), round($x['b'], 2),
            round($x['weight'] * 100, 1), local_dt($x['created_at']),
        ], null, 'A' . $r);
        hex_cell($sheet, 'C' . $r, $x['hex']);
        $r++;
    }
    sheet_style($sheet, count($head));
}

function write_log($sheet, array $rows): void {
    $head = ['Time', 'Run #', 'Grade', 'Blown'];
    $sheet->fromArray($head, null, 'A1');
    $r = 2;
    foreach ($rows as $x) {
        $sheet->fromArray([local_dt($x['ts']), $x['session_id'], (int)$x['grade'], (int)$x['blown']],
            null, 'A' . $r++);
    }
    sheet_style($sheet, count($head));
}

$colorRows = $db->query(
    'SELECT gc.sample_id, gc.grade, gc.hex, gc.L, gc.a, gc.b, gc.weight, s.created_at
     FROM grade_colors gc JOIN samples s ON s.id = gc.sample_id
     ORDER BY gc.grade, gc.sample_id, gc.id'
)->fetchAll();

$logRows = function () use ($db, $f) {
    $w = []; $a = [];
    if ($f['from']) { $w[] = 'ts >= ?'; $a[] = utc_dt($f['from'] . ' 00:00:00'); }
    if ($f['to'])   { $w[] = 'ts < ?';  $a[] = utc_dt(date('Y-m-d', strtotime($f['to'] . ' +1 day')) . ' 00:00:00'); }
    if ($f['run'])  { $w[] = 'session_id = ?'; $a[] = $f['run']; }
    $where = $w ? 'WHERE ' . implode(' AND ', $w) : '';
    $st = $db->prepare("SELECT * FROM sort_log $where ORDER BY id");
    $st->execute($a);
    return $st->fetchAll();
};

$view = $_GET['view'] ?? 'all';
$tab = in_array($_GET['tab'] ?? '', ['runs', 'daily', 'log'], true) ? $_GET['tab'] : 'runs';
$book = new Spreadsheet();
$book->removeSheetByIndex(0);

$add = function (string $title, callable $fn) use ($book) {
    $s = $book->createSheet();
    $s->setTitle($title);
    $fn($s);
};

if ($view === 'grades') {
    $add('Grade Colors', fn($s) => write_colors($s, $colorRows));
} elseif ($view === 'current') {
    if ($tab === 'runs')  $add('Runs', fn($s) => write_runs($s, run_rows($db, $f['from'], $f['to'], $f['run'])));
    if ($tab === 'daily') $add('Daily', fn($s) => write_daily($s, run_rows($db, $f['from'], $f['to'], $f['run'])));
    if ($tab === 'log')   $add('Sort Log', fn($s) => write_log($s, $logRows()));
} else {
    $add('Runs', fn($s) => write_runs($s, run_rows($db, null, null, null)));
    $add('Daily', fn($s) => write_daily($s, run_rows($db, null, null, null)));
    $add('Grade Colors', fn($s) => write_colors($s, $colorRows));
    $add('Sort Log', fn($s) => write_log($s, $logRows()));
}

$fname = 'bean_report_' . date('Ymd_Hi') . '.xlsx';
header('Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
header('Content-Disposition: attachment; filename="' . $fname . '"');
header('Cache-Control: max-age=0');
(new Xlsx($book))->save('php://output');
