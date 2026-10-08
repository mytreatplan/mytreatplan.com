/*
 * Team carousel, ported from the mytreatplan.ae fluid layout.
 * The browser does the scrolling (scroll-snap); this only says where to go. Without JS
 * the track still swipes; arrows stay hidden and no dots are drawn.
 * Dots count the stops the track can actually reach, not the cards, and are recounted on resize.
 */
(() => {
  const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
  const DOT_CLASS =
    'grid size-7 cursor-pointer place-items-center rounded-full ' +
    "before:block before:h-2.5 before:w-2.5 before:rounded-full before:bg-dot before:transition-all before:content-[''] " +
    'hover:before:bg-dot-hover aria-selected:before:w-[26px] aria-selected:before:bg-ink ' +
    'focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-green'

  document.querySelectorAll('[data-carousel]').forEach((carousel) => {
    const track = carousel.querySelector('[data-carousel-track]')
    const cards = [...carousel.querySelectorAll('[data-carousel-card]')]
    const prev = carousel.querySelector('[data-carousel-prev]')
    const next = carousel.querySelector('[data-carousel-next]')
    const dots = carousel.querySelector('[data-carousel-dots]')
    if (!track || cards.length < 2) return

    // Distance for one card, measured live (it changes with the viewport).
    const step = () => (cards[1].offsetLeft - cards[0].offsetLeft) || 1
    const range = () => Math.max(0, track.scrollWidth - track.clientWidth)
    const stops = () => Math.floor(range() / step()) + 1
    const current = () => Math.min(Math.round(track.scrollLeft / step()), stops() - 1)

    const goTo = (index) => {
      const last = stops() - 1
      const target = Math.max(0, Math.min(index, last))
      // The last stop goes to the very end so the last card is fully in view.
      track.scrollTo({ left: target === last ? range() : target * step(), behavior })
    }

    let dotButtons = []
    const drawDots = () => {
      if (!dots) return
      const count = stops()
      if (dotButtons.length === count) return
      dots.textContent = ''
      dotButtons = []
      if (count < 2) return
      const template = dots.dataset.labelTemplate || 'Go to position {n} of {total}'
      for (let i = 0; i < count; i++) {
        const dot = document.createElement('button')
        dot.type = 'button'
        dot.className = DOT_CLASS
        dot.setAttribute('role', 'tab')
        dot.setAttribute('aria-label', template.replace('{n}', i + 1).replace('{total}', count))
        dot.addEventListener('click', () => goTo(i))
        dots.appendChild(dot)
        dotButtons.push(dot)
      }
    }

    const refresh = () => {
      drawDots()
      const index = current()
      const fits = range() <= 2 // 2px of slack for sub-pixel widths
      if (prev) { prev.hidden = fits; prev.disabled = track.scrollLeft <= 2 }
      if (next) { next.hidden = fits; next.disabled = track.scrollLeft >= range() - 2 }
      dotButtons.forEach((dot, n) => dot.setAttribute('aria-selected', String(n === index)))
    }

    // Arrows are switched on here, not in the HTML: without JS a dead arrow is worse than none.
    if (prev) { prev.hidden = false; prev.addEventListener('click', () => goTo(current() - 1)) }
    if (next) { next.hidden = false; next.addEventListener('click', () => goTo(current() + 1)) }

    track.addEventListener('keydown', (event) => {
      const keys = {
        ArrowRight: () => goTo(current() + 1),
        ArrowLeft: () => goTo(current() - 1),
        Home: () => goTo(0),
        End: () => goTo(stops() - 1),
      }
      if (keys[event.key]) { event.preventDefault(); keys[event.key]() }
    })

    let pending = false
    track.addEventListener('scroll', () => {
      if (pending) return
      pending = true
      requestAnimationFrame(() => { pending = false; refresh() })
    }, { passive: true })

    window.addEventListener('resize', refresh)
    refresh()
  })
})()
