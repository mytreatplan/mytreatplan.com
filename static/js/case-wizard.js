// The case wizard's step 2 (epic-ordering 9.3, 9.4): hides the add-ons not offered for the chosen
// service (maker or in-house) and size. The server decides what is accepted; without this
// script every offered add-on is listed and a tampered choice is refused.
(function () {
  'use strict';

  // The VirtuaOrtho branch (9.4): shows the rows and options of the chosen variant only; a
  // hidden group is disabled so its radios and aligner counts are not posted; each row's value carries its variant. The server decides.
  function startVirtuaortho() {
    var form = document.querySelector('[data-virtuaortho-form]');
    if (!form) return;
    var groups = form.querySelectorAll('[data-vo-group]');
    var addOns = form.querySelectorAll('[data-add-on]');
    var filter = form.querySelector('[data-vo-filter]');
    if (filter) filter.hidden = false;

    function variant() {
      var input = form.querySelector('input[name="variant_filter"]:checked');
      return input ? input.value : '';
    }

    function update() {
      var chosenVariant = variant();
      Array.prototype.forEach.call(groups, function (group) {
        var show = !filter || group.getAttribute('data-variant') === chosenVariant;
        group.hidden = !show;
        Array.prototype.forEach.call(group.querySelectorAll('input'), function (input) {
          input.disabled = !show;
        });
      });
      var product = form.querySelector('input[name="product"]:checked');
      var key = product && !product.disabled ? product.value : '';
      Array.prototype.forEach.call(addOns, function (row) {
        var offered = (row.getAttribute('data-offered') || '').split(' ');
        var show = !key || offered.indexOf(key) !== -1;
        row.hidden = !show;
        var box = row.querySelector('input');
        if (!show && box) box.checked = false;
      });
    }

    form.addEventListener('change', update);
    update();
  }

  function start() {
    startVirtuaortho();
    var form = document.querySelector('[data-case-form]');
    if (!form) return;
    var rows = form.querySelectorAll('[data-add-on]');

    function chosen(name) {
      var input = form.querySelector('input[name="' + name + '"]:checked');
      return input ? input.value : '';
    }

    function update() {
      var key = (chosen('kind') === 'in_house' ? 'in_house' : 'branded') + ':' + chosen('size');
      Array.prototype.forEach.call(rows, function (row) {
        var offered = (row.getAttribute('data-offered') || '').split(' ');
        var show = !chosen('size') || offered.indexOf(key) !== -1;
        row.hidden = !show;
        var box = row.querySelector('input');
        if (!show && box) box.checked = false;
      });
      var makers = form.querySelector('[data-maker-choices]');
      if (makers) makers.hidden = chosen('kind') === 'in_house';
    }

    form.addEventListener('change', update);
    update();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
