// The case wizard's steps (epic-ordering 9.16). Each step is a plain POST without this script;
// it only reveals and hides: on the maker step the brand select (for brand-made aligners) with
// the warning of the chosen maker and the name of an "other" maker; on the options steps the
// add-ons not offered for the chosen length (TPS) or row (VirtuaOrtho), and the aligner count
// of the chosen Spark on Demand row only. The server re-validates everything.
(function () {
  'use strict';

  function chosen(form, name) {
    var input = form.querySelector('input[name="' + name + '"]:checked');
    return input ? input.value : '';
  }

  function hideAddOns(form, key) {
    Array.prototype.forEach.call(form.querySelectorAll('[data-add-on]'), function (row) {
      var offered = (row.getAttribute('data-offered') || '').split(' ');
      var show = !key || offered.indexOf(key) !== -1;
      row.hidden = !show;
      var box = row.querySelector('input');
      if (!show && box) box.checked = false;
    });
  }

  function startMaker(form) {
    var choices = form.querySelector('[data-maker-choices]');
    var select = form.querySelector('[data-maker-select]');
    var other = form.querySelector('[data-other-name]');
    var warnings = form.querySelectorAll('[data-missing-login]');

    function update() {
      var brand = chosen(form, 'kind') === 'brand';
      if (choices) choices.hidden = !brand;
      var maker = select ? select.value : '';
      if (other) other.hidden = maker !== 'other';
      Array.prototype.forEach.call(warnings, function (warning) {
        warning.hidden = warning.getAttribute('data-missing-login') !== maker;
      });
    }

    form.addEventListener('change', update);
    update();
  }

  function startTps(form) {
    function update() { hideAddOns(form, chosen(form, 'size')); }
    form.addEventListener('change', update);
    update();
  }

  function startVirtuaortho(form) {
    function update() {
      var key = chosen(form, 'item');
      hideAddOns(form, key);
      Array.prototype.forEach.call(form.querySelectorAll('[data-vo-quantity]'), function (box) {
        box.hidden = box.getAttribute('data-vo-quantity') !== key;
      });
    }
    form.addEventListener('change', update);
    update();
  }

  function start() {
    var form = document.querySelector('[data-wizard-form]');
    if (!form) return;
    var step = form.getAttribute('data-step');
    if (step === 'maker') startMaker(form);
    else if (step === 'options_tps') startTps(form);
    else if (step === 'options_vo') startVirtuaortho(form);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
