// The patient chooser (patients/_patient_chooser.html, epic-ordering 9.15): the server renders
// every row of the doctor's patients; this script shows the search box, filters the rows
// (code, first name, surname, reference: partial, case- and accent-insensitive) and pages them
// here in the browser, so the search term never leaves it (no request, no URL). Markup stays a
// plain <table> with a <thead>, so the DataTables plugin can replace this file later.
(function () {
  'use strict';

  function fold(text) {
    return text.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
  }

  function pageNumbers(current, total) {
    var wanted = {};
    [1, total].concat(range(Math.max(1, current - 2), Math.min(total, current + 2))).forEach(function (n) { wanted[n] = true; });
    var numbers = Object.keys(wanted).map(Number).sort(function (a, b) { return a - b; });
    var out = [], last = 0;
    numbers.forEach(function (n) {
      if (n - last > 1) out.push(null);
      out.push(n);
      last = n;
    });
    return out;
  }

  function range(from, to) {
    var out = [];
    for (var n = from; n <= to; n++) out.push(n);
    return out;
  }

  function button(label, className, hook) {
    var el = document.createElement('button');
    el.type = 'button';
    el.className = className;
    el.textContent = label;
    if (hook) el.setAttribute(hook[0], hook[1] || '');
    return el;
  }

  function init(root) {
    var controls = root.querySelector('[data-chooser-controls]');
    var search = root.querySelector('[data-patient-search]');
    var size = root.querySelector('[data-page-size]');
    var rows = Array.prototype.slice.call(root.querySelectorAll('tbody tr[data-patient]'));
    var none = root.querySelector('[data-no-match]');
    var pagination = root.querySelector('[data-pagination]');
    var showing = root.querySelector('[data-showing]');
    var links = root.querySelector('[data-page-links]');
    var template = root.getAttribute('data-showing-template') || '{first}-{last}/{total}';
    var page = 1;

    function render(focusHook) {
      var tokens = fold(search.value).split(/\s+/).filter(Boolean);
      var matches = rows.filter(function (row) {
        var text = row.getAttribute('data-search') || '';
        return tokens.every(function (token) { return text.indexOf(token) !== -1; });
      });
      var perPage = parseInt(size.value, 10) || 10;
      var pages = Math.max(1, Math.ceil(matches.length / perPage));
      page = Math.min(Math.max(page, 1), pages);
      var start = (page - 1) * perPage;
      var visible = matches.slice(start, start + perPage);
      rows.forEach(function (row) { row.hidden = visible.indexOf(row) === -1; });
      none.hidden = matches.length > 0;
      pagination.hidden = matches.length === 0;
      if (!matches.length) return;
      showing.textContent = template.replace('{first}', start + 1)
        .replace('{last}', start + visible.length).replace('{total}', matches.length);
      links.textContent = '';
      links.hidden = pages < 2;
      if (pages < 2) return;
      var list = document.createElement('ul');
      list.className = 'flex flex-wrap items-center gap-1';
      function item(el) {
        var li = document.createElement('li');
        li.appendChild(el);
        list.appendChild(li);
      }
      var edge = 'inline-flex min-h-[44px] items-center px-3 font-semibold text-green-deep underline underline-offset-4';
      if (page > 1) item(button(root.getAttribute('data-label-previous') || 'Previous', edge, ['data-prev']));
      pageNumbers(page, pages).forEach(function (n) {
        if (n === null) {
          var gap = document.createElement('span');
          gap.className = 'px-2 text-ink-muted';
          gap.setAttribute('aria-hidden', 'true');
          gap.textContent = '…';
          item(gap);
        } else if (n === page) {
          var here = button(n, 'inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-pill bg-green-ui px-3 font-black text-white', ['data-page-link', n]);
          here.setAttribute('aria-current', 'page');
          item(here);
        } else {
          item(button(n, 'inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-pill px-3 font-semibold text-green-deep hover:bg-ink/5', ['data-page-link', n]));
        }
      });
      if (page < pages) item(button(root.getAttribute('data-label-next') || 'Next', edge, ['data-next']));
      links.appendChild(list);
      if (focusHook) {
        var next = links.querySelector('[' + focusHook + ']') || links.querySelector('[aria-current]');
        if (next) next.focus();
      }
    }

    links.addEventListener('click', function (event) {
      var target = event.target.closest('button');
      if (!target) return;
      var hook = 'aria-current';
      if (target.hasAttribute('data-prev')) { page -= 1; hook = 'data-prev'; }
      else if (target.hasAttribute('data-next')) { page += 1; hook = 'data-next'; }
      else page = parseInt(target.getAttribute('data-page-link'), 10);
      render(hook);
      var table = root.querySelector('table');
      if (table && table.scrollIntoView && table.getBoundingClientRect().top < 0) table.scrollIntoView();
    });
    search.addEventListener('input', function () { page = 1; render(); });
    size.addEventListener('change', function () { page = 1; render(); });
    controls.hidden = false;
    render();
  }

  function start() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-patient-chooser]'), init);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
