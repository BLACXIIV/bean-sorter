<?php
declare(strict_types=1);
require dirname(__DIR__) . '/inc/layout.php';

$err = '';
$change = isset($_GET['change']) || !empty($_SESSION['must_change']);

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    csrf_check();
    if (isset($_POST['new1'], $_POST['new2'])) {           // forced change form
        $n1 = (string)$_POST['new1'];
        $n2 = (string)$_POST['new2'];
        if (strlen($n1) < 6) {
            $err = 'New password must be at least 6 characters.';
        } elseif ($n1 !== $n2) {
            $err = 'Passwords do not match.';
        } else {
            setting_set('admin_password_hash', password_hash($n1, PASSWORD_DEFAULT));
            unset($_SESSION['must_change']);
            $_SESSION['admin'] = true;
            header('Location: index.php');
            exit;
        }
        $change = true;
    } else {                                              // normal login
        $pw = (string)($_POST['password'] ?? '');
        if (admin_password_ok($pw)) {
            $_SESSION['admin'] = true;
            if (setting_get('admin_password_hash') === null) {
                $_SESSION['must_change'] = true;
                header('Location: login.php?change=1');
            } else {
                header('Location: index.php');
            }
            exit;
        }
        $err = 'Wrong password.';
    }
}

page_head('Login', 'admin');
echo '<div class="login-wrap card">';
if ($change) {
    echo '<h2>Set a new password</h2><p class="muted">The default password must be changed before continuing.</p>';
    if ($err) echo '<p class="err">' . e($err) . '</p>';
    echo '<form method="post">';
    csrf_field();
    echo '<label>New password</label><input type="password" name="new1" required autofocus>',
         '<label>Repeat new password</label><input type="password" name="new2" required>',
         '<div class="btnrow" style="margin-top:12px"><button class="primary big">Save password</button></div>',
         '</form>';
} else {
    echo '<h2>Admin login</h2>';
    if ($err) echo '<p class="err">' . e($err) . '</p>';
    echo '<form method="post">';
    csrf_field();
    echo '<label>Password</label><input type="password" name="password" required autofocus>',
         '<div class="btnrow" style="margin-top:12px"><button class="primary big">Log in</button></div>',
         '</form>';
    echo '<p class="muted" style="margin-top:10px">First run? Default password is <b>admin123</b> - you will be asked to change it.</p>';
}
echo '</div>';
page_foot();
