/*
 * Connection loss on the signup pages (signup entry 8). Loaded once per full page load
 * (signup/_scripts.html); the boosted forms (hx-boost) swap only the body.
 *
 * When a boosted form cannot be sent (htmx:sendError, htmx:timeout), htmx changes nothing
 * on the page, so what was typed stays. This shows the banner (signup/_connection.html,
 * [data-connection-lost]) and remembers the form. The browser's `online` event, or the
 * banner's "Try again" ([data-connection-retry]), sends that form again, once. An error
 * response (4xx/5xx) to a boosted form is swapped in like a full page load. Without
 * JavaScript the forms are plain POSTs.
 */
(() => {
  if (!window.htmx) return
  htmx.config.timeout = 20000 // ms; a send taking longer counts as a lost connection

  let failed = null // the boosted form whose send failed, until it is sent again
  const submitters = new WeakMap() // form -> the button that submitted it

  const boostedForm = (element) =>
    element instanceof HTMLFormElement && element.closest('[hx-boost="true"]') ? element : null

  const banner = () => document.querySelector('[data-connection-lost]')
  const showBanner = (show) => {
    const element = banner()
    if (element) element.hidden = !show
  }

  // The button that submitted a form (e.g. action=resend), to send it again with the form:
  // htmx forgets it once the focus moves (to "Try again").
  document.addEventListener('submit', (event) => {
    const form = boostedForm(event.target)
    if (form && event.submitter) submitters.set(form, event.submitter)
  }, true)

  document.addEventListener('htmx:beforeRequest', (event) => {
    if (boostedForm(event.detail.elt) && event.detail.elt === failed) {
      failed = null
      showBanner(false)
    }
  })

  // An error response (e.g. a CSRF 403, a 404 or a 500) to a boosted form: swap it in as
  // a full load would show it (htmx 2 does not swap 4xx/5xx by default). The send itself
  // worked, so no connection banner.
  document.addEventListener('htmx:beforeSwap', (event) => {
    if (boostedForm(event.detail.elt) && event.detail.xhr.status >= 400) {
      event.detail.shouldSwap = true
      event.detail.isError = false
    }
  })

  const onFailure = (event) => {
    const form = boostedForm(event.detail.elt)
    if (!form) return // e.g. the live email check: nothing to send again
    failed = form
    showBanner(true)
  }
  document.addEventListener('htmx:sendError', onFailure)
  document.addEventListener('htmx:timeout', onFailure)

  const retry = () => {
    const form = failed
    if (!form) return
    failed = null
    showBanner(false)
    if (!form.isConnected) return
    const submitter = submitters.get(form)
    let extra = null
    if (submitter && submitter.name && submitter.isConnected) {
      extra = document.createElement('input')
      extra.type = 'hidden'
      extra.name = submitter.name
      extra.value = submitter.value
      form.appendChild(extra)
    }
    try {
      htmx.trigger(form, 'submit')
    } finally {
      if (extra) extra.remove() // htmx has read the values by now
    }
  }

  window.addEventListener('online', retry)
  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-connection-retry]')) retry()
  })
})()
