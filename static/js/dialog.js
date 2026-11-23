// Generic dialog behaviour (epic-ordering 9.2; reusable, e.g. by the 9.3 case wizard):
// [data-opens-dialog=id] opens the <dialog> with that id as a modal (without JS it stays a
// plain link), [data-dialog-close] closes the dialog around it, a dialog with [data-open]
// opens on load, and closing returns focus to the opener. Esc and the focus trap are native.
(function () {
  'use strict';

  function start() {
    if (typeof HTMLDialogElement === 'undefined') return;
    var openers = {};
    function open(dialog, opener) {
      if (dialog.open) return;
      openers[dialog.id] = opener || null;
      dialog.showModal();
      var field = dialog.querySelector('[aria-invalid="true"]') ||
        dialog.querySelector('input:not([type=hidden]), select, textarea');
      if (field) field.focus();
    }
    Array.prototype.forEach.call(document.querySelectorAll('[data-opens-dialog]'), function (link) {
      link.addEventListener('click', function (event) {
        var dialog = document.getElementById(link.getAttribute('data-opens-dialog'));
        if (!dialog || typeof dialog.showModal !== 'function') return;
        event.preventDefault();
        open(dialog, link);
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll('dialog[data-dialog]'), function (dialog) {
      dialog.addEventListener('close', function () {
        var opener = openers[dialog.id];
        if (opener) opener.focus();
      });
      if (dialog.hasAttribute('data-open')) {
        openers[dialog.id] = document.querySelector('[data-opens-dialog="' + dialog.id + '"]');
        open(dialog, openers[dialog.id]);
      }
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-dialog-close]'), function (button) {
      button.addEventListener('click', function () {
        var dialog = button.closest('dialog');
        if (dialog) dialog.close();
      });
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
