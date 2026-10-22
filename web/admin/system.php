<?php
declare(strict_types=1);
require dirname(__DIR__) . '/inc/layout.php';
require_login();

// DB backup download - before any output
if (isset($_GET['backup'])) {
    $db = bean_db();
    $db->exec('PRAGMA wal_checkpoint(TRUNCATE)');
    header('Content-Type: application/octet-stream');
    header('Content-Disposition: attachment; filename="beans_backup_' . date('Ymd_His') . '.db"');
    header('Content-Length: ' . filesize(DB_PATH));
    readfile(DB_PATH);
    exit;
}

$msg = ''; $err = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    csrf_check();
    if (isset($_POST['pw_cur'], $_POST['pw_new1'], $_POST['pw_new2'])) {
        if (!admin_password_ok((string)$_POST['pw_cur'])) {
            $err = 'Current password is wrong.';
        } elseif (strlen((string)$_POST['pw_new1']) < 6) {
            $err = 'New password must be at least 6 characters.';
        } elseif ($_POST['pw_new1'] !== $_POST['pw_new2']) {
            $err = 'New passwords do not match.';
        } else {
            setting_set('admin_password_hash',
                password_hash((string)$_POST['pw_new1'], PASSWORD_DEFAULT));
            unset($_SESSION['must_change']);
            $msg = 'Password changed.';
        }
    } elseif (isset($_POST['exit_kiosk'])) {
        if (PHP_OS_FAMILY !== 'Linux') {
            $err = 'Exit kiosk only works on the Pi (not permitted here).';
        } else {
            exec('pkill -f chromium 2>&1', $o, $rc);
            $msg = $rc === 0 ? 'Kiosk browser closed.' : 'Could not close chromium.';
        }
    } elseif (isset($_POST['shutdown'])) {
        if (PHP_OS_FAMILY !== 'Linux') {
            $err = 'Shutdown only works on the Pi (not permitted here).';
        } else {
            exec('systemctl poweroff 2>&1', $o, $rc);
            if ($rc !== 0) $err = 'Shutdown failed: ' . implode(' ', $o);
        }
    }
}

$dbsize = is_file(DB_PATH) ? round(filesize(DB_PATH) / 1024, 1) . ' KB' : 'missing';

page_head('System', 'admin');
echo '<div class="wrap"><h2>System</h2>';
if ($msg) echo '<p class="warn">' . e($msg) . '</p>';
if ($err) echo '<p class="err">' . e($err) . '</p>';

echo '<div class="grid2"><div class="card"><h2>Engine</h2><div id="engine-sys"><p class="muted">checking...</p></div>';
echo '</div><div class="card"><h2>Database</h2><p>Size: ' . e($dbsize) . '</p>',
     '<a class="btn" href="?backup=1">Download DB backup</a></div></div>';

echo '<div class="grid2"><div class="card"><h2>Change password</h2>',
     '<form method="post">',
     '<input type="hidden" name="csrf" value="' . e(csrf_token()) . '">',
     '<label>Current password</label><input type="password" name="pw_cur" required>',
     '<label>New password</label><input type="password" name="pw_new1" required>',
     '<label>Repeat new</label><input type="password" name="pw_new2" required>',
     '<div class="btnrow" style="margin-top:10px"><button class="primary">Change</button></div></form></div>',

     '<div class="card"><h2>Power</h2>',
     '<form method="post" data-confirm="Close the kiosk browser? The engine and PHP keep running in background until reboot.">',
     '<input type="hidden" name="csrf" value="' . e(csrf_token()) . '">',
     '<input type="hidden" name="exit_kiosk" value="1"><button>Exit kiosk</button></form><br>',
     '<form method="post" data-confirm="SHUT DOWN the Pi now?">',
     '<input type="hidden" name="csrf" value="' . e(csrf_token()) . '">',
     '<input type="hidden" name="shutdown" value="1"><button class="danger">Shutdown</button></form>',
     '<p class="muted" style="margin-top:8px">Requires the web server user to be allowed to run systemctl poweroff.</p></div></div>';
echo '</div>';
page_foot();
