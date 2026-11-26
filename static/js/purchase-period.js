// The purchase page (subscriptions/upgrade_purchase.html): the voucher field belongs to the
// year paid upfront only, so it is hidden (and emptied) while monthly is chosen. Without
// this script the field stays visible and the server refuses a code posted with monthly.
(function () {
  var form = document.querySelector('[data-purchase-form]');
  if (!form) { return; }
  var field = form.querySelector('[data-voucher-field]');
  var input = form.querySelector('[data-voucher]');
  var radios = form.querySelectorAll('input[name="period"]');
  if (!field || !input || !radios.length) { return; }
  function sync() {
    var monthly = form.querySelector('input[name="period"]:checked');
    var hide = !!monthly && monthly.value === 'monthly';
    field.hidden = hide;
    if (hide) { input.value = ''; }
  }
  for (var i = 0; i < radios.length; i++) { radios[i].addEventListener('change', sync); }
  sync();
})();
