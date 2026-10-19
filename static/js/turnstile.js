/*
 * The CAPTCHA (signup.captcha) of signup step 1 and of login: Cloudflare Turnstile,
 * rendered explicitly into [data-turnstile] (templates/includes/_turnstile.html) once
 * Cloudflare's script (its data-script) is loaded. Tokens are single use, so every page
 * that shows a widget (a full load or an htmx swap, e.g. a refused form) gets a new one;
 * widgets whose container was swapped out are removed.
 *
 * If the script cannot load (e.g. a blocker), [data-turnstile-error] is shown and the form
 * is not sent. A submit before the widget has its token is held, with
 * [data-turnstile-pending] shown, and sent once the widget's callback gives the token.
 * If the widget reports an error or a timeout, or no token comes within TOKEN_WAIT_MS,
 * the waiting note gives way to the error note.
 *
 * Loaded in the head of the pages with a widget (signup/_scripts.html, accounts/login.html).
 * The setup runs on the first load and again on every htmx swap (`htmx:load`); each widget
 * is rendered once (data-turnstile-bound).
 */
(() => {
  const BOUND = 'turnstileBound'
  const TOKEN_WAIT_MS = 15000

  // The script is loaded once per full page load; `widgets` maps each rendered widget's id
  // to its container.
  let turnstileLoading = null
  let turnstileFailed = false
  const widgets = new Map()
  const held = new WeakMap() // form -> {submitter, timer} of a submit waiting for the token

  const showNote = (form, name, show) => {
    const note = form && form.querySelector(`[data-turnstile-${name}]`)
    if (note) note.hidden = !show
  }

  const showLoadError = () => {
    document.querySelectorAll('[data-turnstile]').forEach((box) => {
      const form = box.closest('form')
      showNote(form, 'pending', false)
      showNote(form, 'error', true)
    })
  }

  const token = (form) => {
    const input = form.querySelector('input[name="cf-turnstile-response"]')
    return input ? input.value : ''
  }

  // No token is coming (widget error or timeout, or the wait ran out): stop waiting and
  // show the error note. A later token (Turnstile retries) hides it again.
  const onFailure = (form) => {
    if (!form) return
    const wait = held.get(form)
    if (wait) clearTimeout(wait.timer)
    held.delete(form)
    showNote(form, 'pending', false)
    showNote(form, 'error', true)
  }

  // Capture phase on the document: before htmx's (boosted) handler on the form.
  document.addEventListener('submit', (event) => {
    const form = event.target
    if (!(form instanceof HTMLFormElement) || !form.querySelector('[data-turnstile]')) return
    if (token(form)) return
    event.preventDefault()
    event.stopImmediatePropagation()
    if (turnstileFailed) {
      showNote(form, 'error', true)
      return
    }
    if (held.has(form)) return // already waiting
    showNote(form, 'error', false)
    showNote(form, 'pending', true)
    held.set(form, {
      submitter: event.submitter || null,
      timer: setTimeout(() => onFailure(form), TOKEN_WAIT_MS),
    })
  }, true)

  const onToken = (box) => () => {
    const form = box.closest('form')
    if (!form) return
    showNote(form, 'error', false)
    const wait = held.get(form)
    if (!wait) return
    clearTimeout(wait.timer)
    held.delete(form)
    showNote(form, 'pending', false)
    if (wait.submitter && wait.submitter.isConnected) form.requestSubmit(wait.submitter)
    else form.requestSubmit()
  }

  const setupTurnstile = () => {
    const boxes = document.querySelectorAll('[data-turnstile]')
    if (!boxes.length) return
    if (!window.turnstile) {
      const url = boxes[0].dataset.script
      if (turnstileFailed) showLoadError()
      if (!turnstileLoading && url) {
        turnstileLoading = new Promise((resolve, reject) => {
          const script = document.createElement('script')
          script.src = url
          script.async = true
          script.onload = resolve
          script.onerror = reject
          document.head.appendChild(script)
        }).then(() => {
          turnstileFailed = false
          document.querySelectorAll('[data-turnstile-error]').forEach((note) => { note.hidden = true })
          setupTurnstile()
        }).catch(() => {
          turnstileLoading = null
          turnstileFailed = true
          showLoadError()
        })
      }
      return
    }
    widgets.forEach((container, id) => {
      if (!document.contains(container)) {
        try { window.turnstile.remove(id) } catch (error) { /* already gone */ }
        widgets.delete(id)
      }
    })
    boxes.forEach((box) => {
      if (box.dataset[BOUND]) return
      box.dataset[BOUND] = 'true'
      const id = window.turnstile.render(box, {
        sitekey: box.dataset.sitekey,
        language: box.dataset.language || 'auto',
        callback: onToken(box),
        'error-callback': () => onFailure(box.closest('form')),
        'timeout-callback': () => onFailure(box.closest('form')),
      })
      if (id !== undefined && id !== null) widgets.set(id, box)
    })
  }

  if (document.readyState === 'complete') {
    setupTurnstile()
  } else {
    document.addEventListener('DOMContentLoaded', setupTurnstile)
  }
  document.addEventListener('htmx:load', setupTurnstile)
})()
