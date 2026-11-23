// The patient list (patients/list.html, epic-ordering 9.2): choosing a page size submits its
// form at once (the Apply button does the same without JS), and the empty state's link
// moves focus to the add-patient form's first field.
(function () {
  'use strict';

  function start() {
    var size = document.querySelector('[data-page-size]');
    var apply = document.querySelector('[data-page-size-apply]');
    if (size && size.form) {
      size.addEventListener('change', function () { size.form.submit(); });
      if (apply) apply.hidden = true;
    }
    var add = document.querySelector('[data-add-patient]');
    var field = document.querySelector('[data-patient-form] input');
    if (add && field) add.addEventListener('click', function () { field.focus(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
