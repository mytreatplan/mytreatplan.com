// The records upload of the patient file (records/_case_records.html, epic-case-lifecycle
// 10.5): once the storage widget has stored the file (`storage:uploaded`), post the new
// object's id with the section's hidden form, which links it to the case. Only the id is
// posted. Only events of the `case_records` purpose count (the portrait widget of the same
// page fires its own).
(function () {
  'use strict';

  function start() {
    var form = document.querySelector('[data-records-attach]');
    var widget = document.querySelector('[data-records-widget]');
    if (!form || !widget) return;
    var sent = false;
    widget.addEventListener('storage:uploaded', function (event) {
      var stored = event.detail || {};
      if (sent || typeof stored.id !== 'string' || stored.purpose !== 'case_records') return;
      sent = true;
      form.querySelector('input[name="object_id"]').value = stored.id;
      form.submit();
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
