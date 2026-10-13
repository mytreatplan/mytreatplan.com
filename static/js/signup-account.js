/*
 * Signup step 1 (signup/templates/signup/step_account.html):
 * - "Others: please specify" is shown only while "Others" is chosen. Without this script
 *   it stays visible, as a normal (optional unless "Others") field.
 * - The password strength meter, with zxcvbn-ts (core and the common dictionaries,
 *   vendored in static/js/). It is a hint only: the server-side rules decide.
 */
(() => {
  document.querySelectorAll('[data-show-if-field]').forEach((wrapper) => {
    const select = document.getElementById(wrapper.dataset.showIfField)
    if (!select) return
    const update = () => { wrapper.hidden = select.value !== wrapper.dataset.showIfValue }
    select.addEventListener('change', update)
    update()
  })

  const box = document.getElementById('password-strength')
  const password = document.getElementById('id_password1')
  const zxcvbnts = window.zxcvbnts
  if (!box || !password || !zxcvbnts || !zxcvbnts.core || !zxcvbnts['language-common']) return

  const common = zxcvbnts['language-common']
  const zxcvbn = new zxcvbnts.core.ZxcvbnFactory({
    dictionary: { ...common.dictionary },
    graphs: common.adjacencyGraphs,
  })
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
})()
