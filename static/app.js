// Expand / collapse the bottom ("done") row when its header is clicked.
// Kept in a file of its own (no inline script) so the Content-Security-Policy
// can say script-src 'self'.
document.addEventListener('DOMContentLoaded', function () {
    var header = document.querySelector('.done-header');
    if (!header) {
        return;
    }
    header.addEventListener('click', function () {
        var doneColumn = document.querySelector('.done-column');
        var toggleIcon = document.querySelector('.toggle-icon');

        if (doneColumn.classList.contains('collapsed')) {
            doneColumn.classList.remove('collapsed');
            toggleIcon.textContent = '\u25B2';
        } else {
            doneColumn.classList.add('collapsed');
            toggleIcon.textContent = '\u25BC';
        }
    });
});
