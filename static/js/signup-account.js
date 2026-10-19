/*
 * Signup step 1 (signup/templates/signup/step_account.html):
 * - "Others: please specify" is shown only while "Others" is chosen. Without this script
 *   it stays visible, as a normal (optional unless "Others") field.
 * - The password strength meter, with zxcvbn-ts (core and the common dictionaries,
 *   vendored in static/js/). It is a hint only: the server-side rules decide.
 *
 * The CAPTCHA widget is static/js/turnstile.js, loaded beside this script.
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
