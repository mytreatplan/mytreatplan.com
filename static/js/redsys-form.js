/*
 * The way to Redsys's payment page (payments/redsys_form.html, epic-payments entry 3):
 * post the signed form to Redsys at once. Without JavaScript the doctor presses the
 * page's button.
 */
(() => {
  const form = document.querySelector('[data-redsys-form]')
  if (form) form.submit()
})()
