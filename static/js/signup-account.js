/*
 * Signup step 1 (signup/templates/signup/step_account.html):
 * - "Others: please specify" is shown only while "Others" is chosen. Without this script
 *   it stays visible, as a normal (optional unless "Others") field.
 * - The password strength meter, with zxcvbn-ts (core and the common dictionaries,
 *   vendored in static/js/). It is a hint only: the server-side rules decide.
 * - The CAPTCHA (signup.captcha): Cloudflare Turnstile, rendered explicitly into
 *   [data-turnstile] once Cloudflare's script (its data-script) is loaded. Tokens are
 *   single use, so every page that shows step 1 (a full load or a swap, e.g. the form
 *   refused) gets a new widget; widgets whose container was swapped out are removed.
 *   If the script cannot load (e.g. a blocker), [data-turnstile-error] is shown and step 1
 *   is not sent. A submit before the widget has its token is held, with
 *   [data-turnstile-pending] shown, and sent once the widget's callback gives the token.
 *   If the widget reports an error or a timeout, or no token comes within
 *   TOKEN_WAIT_MS, the waiting note gives way to the error note.
 *
 * Loaded on every boosted signup page (signup/_scripts.html). The setup runs on the
 * first load and again on every htmx swap (`htmx:load`), so step 1 works the same when it
 * arrives in a swapped page; each element is bound once (data-signup-bound). When step 1
 * arrives by a swap from a page without zxcvbn-ts, the scripts listed in
 * #password-strength's data-scripts are loaded first.
 */
(() => {
  const BOUND = 'signupBound'

  const once = (element) => {
    if (!element || element.dataset[BOUND]) return false
    element.dataset[BOUND] = 'true'
    return true
  }

  const setupToggles = () => {
    document.querySelectorAll('[data-show-if-field]').forEach((wrapper) => {
      const select = document.getElementById(wrapper.dataset.showIfField)
      if (!select || !once(wrapper)) return
      const update = () => { wrapper.hidden = select.value !== wrapper.dataset.showIfValue }
      select.addEventListener('change', update)
      update()
    })
  }

  // Turnstile: the script is loaded once per full page load; `widgets` maps each rendered
  // widget's id to its container.
  let turnstileLoading = null
  let turnstileFailed = false
  const widgets = new Map()
  const held = new WeakMap() // form -> {submitter, timer} of a submit waiting for the token
  const TOKEN_WAIT_MS = 15000

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
      if (!once(box)) return
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

  let loading = null
  const loadScripts = (urls) => {
    if (!loading) {
      // In order (core, then the dictionaries); a failure leaves the meter hidden.
      loading = urls.reduce((previous, url) => previous.then(() => new Promise((resolve, reject) => {
        const script = document.createElement('script')
        script.src = url
        script.onload = resolve
        script.onerror = reject
        document.head.appendChild(script)
      })), Promise.resolve()).catch(() => {})
    }
    return loading
  }

  const zxcvbnReady = () => {
    const zxcvbnts = window.zxcvbnts
    return Boolean(zxcvbnts && zxcvbnts.core && zxcvbnts['language-common'])
  }

  let zxcvbn = null
  const setupMeter = () => {
    const box = document.getElementById('password-strength')
    const password = document.getElementById('id_password1')
    if (!box || !password) return
    if (!zxcvbnReady()) {
      const urls = (box.dataset.scripts || '').split(' ').filter(Boolean)
      if (urls.length) loadScripts(urls).then(() => { if (zxcvbnReady()) setupMeter() })
      return
    }
    if (!once(password)) return

    if (!zxcvbn) {
      const common = window.zxcvbnts['language-common']
      zxcvbn = new window.zxcvbnts.core.ZxcvbnFactory({
        dictionary: { ...common.dictionary },
        graphs: common.adjacencyGraphs,
      })
    }
    const meter = document.getElementById('password-strength-meter')
    const label = document.getElementById('password-strength-label')
    const userInputs = () => box.dataset.userInputs.split(' ')
      .map((id) => document.getElementById(id))
      .filter((input) => input && input.value)
      .flatMap((input) => [input.value, ...input.value.split(/[\s@.\-_]+/)])
      .filter(Boolean)

    const update = () => {
      if (!password.value) {
        box.hidden = true
        return
      }
      const { score } = zxcvbn.check(password.value, userInputs())
      meter.value = score
      label.textContent = box.getAttribute(`data-label-${score}`) || ''
      box.hidden = false
    }
    password.addEventListener('input', update)
    update()
  }

  const setup = () => {
    setupToggles()
    setupMeter()
    setupTurnstile()
  }

  // A deferred script runs before DOMContentLoaded, so the deferred zxcvbn-ts scripts after
  // it in the head have run by then.
  if (document.readyState === 'complete') {
    setup()
  } else {
    document.addEventListener('DOMContentLoaded', setup)
  }
  document.addEventListener('htmx:load', setup)
})()
