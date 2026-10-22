/*
 * The emailed password reset link's page (accounts/password_reset_link.html, decision 4):
 * the uid and token come in the URL fragment (`#<uidb64>.<token>`), which browsers never
 * send. Read it, remove it from the address (history.replaceState), and post it with the
 * page's form (CSRF). The server answers with the new-password page, or the "no longer
 * valid" page (an empty or wrong value included).
 */
(() => {
  const form = document.querySelector('[data-reset-link-form]')
  if (!form) return
  const value = window.location.hash.replace(/^#/, '')
  if (window.history && window.history.replaceState) {
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
  }
  form.elements.link.value = value
  form.submit()
})()
