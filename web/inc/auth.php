<?php
declare(strict_types=1);
require_once __DIR__ . '/db.php';

$__sess = ROOT . '/var/sessions';
if (!is_dir($__sess)) {
    mkdir($__sess, 0777, true);
}
session_save_path($__sess);
session_name('BEANSESSID');
session_start();

function csrf_token(): string {
    if (empty($_SESSION['csrf'])) {
        $_SESSION['csrf'] = bin2hex(random_bytes(16));
    }
    return $_SESSION['csrf'];
}

function csrf_field(): void {
    echo '<input type="hidden" name="csrf" value="' . e(csrf_token()) . '">';
}

function csrf_check(): void {
    if (!hash_equals($_SESSION['csrf'] ?? '', $_POST['csrf'] ?? '')) {
        http_response_code(400);
        exit('Bad CSRF token - reload the page.');
    }
}

function admin_password_ok(string $pw): bool {
    $hash = setting_get('admin_password_hash');
    if ($hash === null) {                 // first run: default password
        return $pw === 'admin123';
    }
    return password_verify($pw, (string)$hash);
}

function is_logged_in(): bool {
    return !empty($_SESSION['admin']);
}

// any admin page: not logged in -> login; still on default pw -> forced change
function require_login(): void {
    if (!is_logged_in()) {
        header('Location: login.php');
        exit;
    }
    if (!empty($_SESSION['must_change'])) {
        header('Location: login.php?change=1');
        exit;
    }
}
