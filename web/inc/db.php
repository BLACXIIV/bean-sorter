<?php
declare(strict_types=1);

define('ROOT', dirname(__DIR__, 2));          // project root (parent of web/)
// BEAN_DB selects a different database for dev runs (webcam colours are not
// valid for the Pi camera - never mix them). Relative paths sit under ROOT.
$env_db = getenv('BEAN_DB') ?: '';
define('DB_PATH', $env_db === '' ? ROOT . '/data/beans.db'
    : ($env_db[0] === '/' || preg_match('/^[A-Za-z]:[\\\\\/]/', $env_db)
        ? $env_db : ROOT . '/' . $env_db));
define('ENGINE', 'http://127.0.0.1:5000');

date_default_timezone_set('Asia/Manila');   // display tz; DB stays UTC

function bean_db(): PDO {
    static $pdo = null;
    if ($pdo === null) {
        if (!is_file(DB_PATH)) {
            http_response_code(500);
            exit('data/beans.db not found - start the engine first.');
        }
        $pdo = new PDO('sqlite:' . DB_PATH, null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $pdo->exec('PRAGMA busy_timeout=5000');
        $pdo->exec('PRAGMA journal_mode=WAL');
    }
    return $pdo;
}

function setting_get(string $key, $default = null) {
    $st = bean_db()->prepare('SELECT value FROM settings WHERE key = ?');
    $st->execute([$key]);
    $v = $st->fetchColumn();
    return $v === false ? $default : json_decode((string)$v, true);
}

function setting_set(string $key, $value): void {
    bean_db()->prepare(
        'INSERT INTO settings(key, value) VALUES(?, ?)
         ON CONFLICT(key) DO UPDATE SET value = excluded.value'
    )->execute([$key, json_encode($value)]);
}

function e($s): string {
    return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8');
}

// "2026-10-22 01:23:45" (UTC) -> "2026-10-22 09:23:45" (Asia/Manila)
function local_dt(?string $utc): string {
    if (!$utc) return '-';
    $d = new DateTime($utc, new DateTimeZone('UTC'));
    $d->setTimezone(new DateTimeZone('Asia/Manila'));
    return $d->format('Y-m-d H:i:s');
}

function local_day(?string $utc): string {
    if (!$utc) return '-';
    $d = new DateTime($utc, new DateTimeZone('UTC'));
    $d->setTimezone(new DateTimeZone('Asia/Manila'));
    return $d->format('Y-m-d');
}

// local Y-m-d H:i[:s] -> UTC string for WHERE clauses
function utc_dt(string $local): string {
    $d = new DateTime($local, new DateTimeZone('Asia/Manila'));
    $d->setTimezone(new DateTimeZone('UTC'));
    return $d->format('Y-m-d H:i:s');
}

function engine_get(string $path): ?array {
    $ctx = stream_context_create(['http' => ['timeout' => 1.5]]);
    $r = @file_get_contents(ENGINE . $path, false, $ctx);
    return $r === false ? null : json_decode($r, true);
}

function engine_post(string $path): ?array {
    $ctx = stream_context_create(['http' => [
        'method' => 'POST', 'timeout' => 2,
        'header' => "Content-Type: application/json\r\n", 'content' => '{}',
    ]]);
    $r = @file_get_contents(ENGINE . $path, false, $ctx);
    return $r === false ? null : json_decode($r, true);
}
