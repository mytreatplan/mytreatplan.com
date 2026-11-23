// The case wizard's step 2 (epic-ordering 9.3): hides the add-ons not offered for the chosen
// service (maker or in-house) and size. The server decides what is accepted; without this
// script every offered add-on is listed and a tampered choice is refused.
(function () {
  'use strict';

  function start() {
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
