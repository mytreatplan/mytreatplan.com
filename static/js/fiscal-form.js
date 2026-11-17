// The fiscal entity form (accounts/settings_fiscal_form.html, epic-doctor-profile entry 5).
// Without this script the form still works: both kinds' name fields are shown and the
// server ignores the ones of the kind not chosen. With it: hide the other kind's fields,
// show the VAT id only for billing country AE, and label the tax id "Tax id" for AE.
(function () {
  'use strict';

  function start() {
    var form = document.querySelector('[data-fiscal-form]');
    if (!form) return;
    var person = form.querySelector('[data-fiscal-person]');
    var company = form.querySelector('[data-fiscal-company]');
    var vat = form.querySelector('[data-fiscal-vat]');
    var label = form.querySelector('[data-fiscal-tax-label]');
    var country = form.querySelector('[name="country"]');
    var kinds = form.querySelectorAll('input[name="kind"]');

    function kind() {
      for (var i = 0; i < kinds.length; i++) if (kinds[i].checked) return kinds[i].value;
      return 'person';
    }

    function refresh() {
      var isCompany = kind() === 'company';
      if (person) person.hidden = isCompany;
      if (company) company.hidden = !isCompany;
      var isAe = !!country && country.value === 'AE';
      if (vat) vat.hidden = !isAe;
      if (label) label.textContent = isAe ? label.dataset.labelAe : label.dataset.labelDefault;
    }

    for (var i = 0; i < kinds.length; i++) kinds[i].addEventListener('change', refresh);
    if (country) country.addEventListener('change', refresh);
    refresh();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
