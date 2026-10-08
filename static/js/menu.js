/*
 * Burger menu for phones (<=700px), ported from the mytreatplan.ae fluid layout.
 * The button toggles the nav panel; Escape, a link tap, or resizing above 700px closes it.
 * Styling hangs off html.js (set here, so the nav only collapses when this script runs),
 * aria-expanded on the button and data-open on the panel.
 */
(() => {
  const button = document.getElementById('site-menu-toggle')
  const panel = document.getElementById('site-menu')
  if (!button || !panel) return
  // Only now may the phone nav collapse into the burger panel (CSS keys off html.js).
  document.documentElement.classList.add('js')

  const setOpen = (open) => {
    panel.toggleAttribute('data-open', open)
    button.setAttribute('aria-expanded', String(open))
    document.body.style.overflow = open ? 'hidden' : ''
  }
  const close = () => setOpen(false)

  button.addEventListener('click', () => setOpen(!panel.hasAttribute('data-open')))
  panel.querySelectorAll('a').forEach((link) => link.addEventListener('click', close))
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && panel.hasAttribute('data-open')) close()
  })
  window.addEventListener('resize', () => {
    if (window.innerWidth > 700 && panel.hasAttribute('data-open')) close()
  })
})()
