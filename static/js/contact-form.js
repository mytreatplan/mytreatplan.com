/*
 * The contact form (publicsite/contact/_form.html, story 1.5). htmx posts it and swaps
 * #contact-form with the answer. Here, on the page's load and after every swap: the JS
 * flag `cf_js` is set (the server drops a message without it) and Send is enabled. After
 * a swap, the focus moves to the thank-you, the first field with an error, or the
 * general error box (the rate limit). When
 * the request fails (no answer, or an error status), the "try again" notice shows.
 */
(() => {
  if (!window.htmx) return

  const setUp = (root) => {
    const forms = root.matches && root.matches('form[data-contact-form]')
      ? [root] : root.querySelectorAll('form[data-contact-form]')
    forms.forEach((form) => {
      const flag = form.querySelector('input[name="cf_js"]')
      if (flag) flag.value = '1'
      const send = form.querySelector('[data-contact-send]')
      if (send) send.disabled = false
    })
  }

  htmx.onLoad((element) => {
    setUp(element)
    if (element.id !== 'contact-form') return // the page's own load
    if (element.matches('[data-contact-thanks]')) {
      element.focus()
      return
    }
    const invalid = element.querySelector('[aria-invalid="true"]') ||
      element.querySelector('[data-contact-errors]')
    if (invalid) invalid.focus()
  })

  const showFailure = (event) => {
    const form = event.target.closest && event.target.closest('form[data-contact-form]')
    if (!form) return
    const notice = form.querySelector('[data-contact-failed]')
    if (notice) notice.hidden = false
  }
  document.addEventListener('htmx:beforeRequest', (event) => {
    const form = event.target.closest && event.target.closest('form[data-contact-form]')
    const notice = form && form.querySelector('[data-contact-failed]')
    if (notice) notice.hidden = true
  })
  document.addEventListener('htmx:responseError', showFailure)
  document.addEventListener('htmx:sendError', showFailure)
})()
