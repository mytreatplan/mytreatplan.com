// The patient list (patients/list.html, epic-ordering 9.2): choosing a page size submits its
// form at once (the Apply button does the same without JS).
(function () {
  'use strict';

  function start() {
    var size = document.querySelector('[data-page-size]');
    var apply = document.querySelector('[data-page-size-apply]');
    if (size && size.form) {
      size.addEventListener('change', function () { size.form.submit(); });
      if (apply) apply.hidden = true;
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
