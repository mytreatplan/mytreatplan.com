// The patient page's portrait upload (patients/patient.html, epic-ordering 9.2): once the
// storage widget has stored the file (`storage:uploaded`), post the new object's id with the
// page's hidden form, which links it as the patient's portrait waiting for its checks. Only
// the id is posted.
(function () {
  'use strict';

  function start() {
    var form = document.querySelector('[data-portrait-attach]');
    var widget = document.querySelector('[data-portrait-widget]');
    if (!form || !widget) return;
    var sent = false;
    widget.addEventListener('storage:uploaded', function (event) {
      var stored = event.detail || {};
      if (sent || typeof stored.id !== 'string' || stored.purpose !== 'patient_portrait') return;
      sent = true;
      form.querySelector('input[name="object_id"]').value = stored.id;
      form.submit();
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
