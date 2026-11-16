// The profile photo's upload page (accounts/settings_photo.html, epic-doctor-profile
// entry 4): once the storage widget has stored the file (`storage:uploaded`), post the
// new object's id with the page's hidden form, which links it as the doctor's photo
// waiting for its checks and goes back to "My details". Only the id is posted.
(function () {
  'use strict';

  function start() {
    var form = document.querySelector('[data-photo-attach]');
    var widget = document.querySelector('[data-photo-widget]');
    if (!form || !widget) return;
    var sent = false;
    widget.addEventListener('storage:uploaded', function (event) {
      var stored = event.detail || {};
      if (sent || typeof stored.id !== 'string' || stored.purpose !== 'profile_photo') return;
      sent = true;
      form.querySelector('input[name="object_id"]').value = stored.id;
      form.submit();
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
