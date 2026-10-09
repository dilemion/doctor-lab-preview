/* Shared, dependency-free catalog and published content renderer. */
(function (scope, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else {
    scope.DocnlabData = api;
    var script = document.currentScript;
    var base = new URL('../', script.src);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { api.start(base); });
    else api.start(base);
  }
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';
  function money(value) {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw new TypeError('Invalid price');
    return new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(value);
  }
  var currentCatalog = null;
  function hasDiscount(service) {
    return typeof service.price_rub === 'number' && Number.isFinite(service.price_rub) &&
      typeof service.old_price_rub === 'number' && Number.isFinite(service.old_price_rub) && service.old_price_rub > service.price_rub;
  }
  function filterServices(services, query, discountedOnly) {
    var q = String(query || '').trim().toLocaleLowerCase('ru-RU');
    var exactCode = q && services.some(function (s) { return String(s.code).toLowerCase() === q; });
    return services.filter(function (s) {
      var code = String(s.code).toLowerCase();
      var match = exactCode ? code === q : (!q || code.includes(q) || String(s.name).toLocaleLowerCase('ru-RU').includes(q));
      return (!discountedOnly || hasDiscount(s)) && match;
    });
  }
  function safeHttpUrl(value) {
    if (!value || typeof value !== 'string') return null;
    try {
      var url = new URL(value);
      return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  }
  function safeAssetUrl(value, base) {
    var absolute = safeHttpUrl(value);
    if (absolute) return absolute;
    if (typeof value !== 'string' || !value.startsWith('assets/')) return null;
    try {
      var decoded = decodeURIComponent(value);
      if (/[%\\?#:\x00-\x1f\x7f]/.test(decoded)) return null;
      if (decoded.split('/').some(function (part) { return !part || part === '.' || part === '..'; })) return null;
      return new URL(decoded, base).href;
    } catch (_) { return null; }
  }
  function published(rows) {
    return Array.isArray(rows) ? rows.filter(function (row) { return row.status === 'published'; }).sort(function (a, b) { return Number(a.sort_order || 0) - Number(b.sort_order || 0); }) : [];
  }
  function promotionIsCurrent(row, today) {
    return (!row.starts_on || row.starts_on <= today) && (!row.ends_on || row.ends_on >= today);
  }
  function moscowDate(date) {
    return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);
  }
  function dateLabel(value) {
    var parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? 'дата не указана' : new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', dateStyle: 'medium' }).format(parsed);
  }
  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function link(text, url, className) {
    var valid = safeHttpUrl(url);
    if (!valid) return element('span', className, text);
    var a = element('a', className, text);
    a.href = valid;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    return a;
  }
  function localLink(text, url, className) {
    var a = element('a', className, text); a.href = url; return a;
  }
  function empty(container, text) {
    container.replaceChildren(element('p', 'dl-empty', text));
  }
  async function load(url) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, 15000);
    try {
      var response = await fetch(url, { signal: controller.signal, cache: 'no-cache' });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      return await response.json();
    } finally { clearTimeout(timer); }
  }
  function validateCatalog(data) {
    if (!data || data.pricing_status !== 'confirmed_docnlab_moscow' || !Array.isArray(data.services) || !data.services.length) throw new Error('Catalog is not confirmed');
    var seen = new Set();
    data.services.forEach(function (s) {
      if (!s || typeof s.code !== 'string' || !s.code || seen.has(s.code) || typeof s.name !== 'string' || !s.name || typeof s.price_rub !== 'number' || !Number.isFinite(s.price_rub) || s.price_rub < 0) throw new Error('Invalid catalog service');
      seen.add(s.code);
    });
    return data;
  }
  function priceBlock(service) {
    var wrap = element('div', 'dl-price');
    if (hasDiscount(service)) {
      var old = element('del', 'dl-old-price', money(service.old_price_rub));
      old.setAttribute('aria-label', 'Старая цена ' + money(service.old_price_rub));
      wrap.append(old);
      if (typeof service.discount_percent === 'number' && service.discount_percent > 0 && service.discount_percent < 100) wrap.append(element('span', 'dl-discount', '−' + service.discount_percent + '%'));
    }
    wrap.append(element('strong', 'dl-current-price', money(service.price_rub)));
    return wrap;
  }
  function serviceCard(service, base, compact) {
    var card = element('article', compact ? 'dl-test dl-test--compact' : 'dl-test');
    var header = element('div', 'dl-test-header');
    var title = element('div', 'dl-test-title');
    var meta = element('div', 'dl-test-meta');
    meta.append(element('span', 'dl-code', service.code));
    if (service.is_sampling_service) meta.append(element('span', 'dl-tag', 'Забор биоматериала'));
    else if (service.is_profile) meta.append(element('span', 'dl-tag', 'Комплекс'));
    title.append(meta, element(compact ? 'h3' : 'h2', '', service.name));
    header.append(title, priceBlock(service));
    card.append(header);
    if (!compact && service.turnaround) card.append(element('p', 'dl-turnaround', 'Срок: ' + service.turnaround));
    if (!compact && Array.isArray(service.biomaterials)) {
      var materials = [...new Set(service.biomaterials.map(function (material) { return material.name; }).filter(Boolean))];
      if (materials.length) card.append(element('p', 'dl-turnaround', 'Биоматериал: ' + materials.join('; ')));
    }
    if (compact) {
      var target = new URL('analyses/', base); target.searchParams.set('q', service.code);
      card.append(localLink('Описание и подготовка →', target.href, 'dl-text-link'));
      return card;
    }
    var desc = service.description;
    if (desc && (desc.summary || (Array.isArray(desc.sections) && desc.sections.length))) {
      var details = element('details', 'dl-description');
      details.append(element('summary', '', 'Описание и подготовка'));
      if (desc.summary && (!Array.isArray(desc.sections) || !desc.sections.length)) details.append(element('p', '', desc.summary));
      (desc.sections || []).forEach(function (section) {
        if (section.title) details.append(element('h3', '', section.title));
        if (section.text) details.append(element('p', '', section.text));
      });
      var provenance = element('p', 'dl-source');
      provenance.append(link('Источник описания: ДНКом', desc.source_url || service.source_url));
      if (desc.updated_at) provenance.append(document.createTextNode(' · ' + dateLabel(desc.updated_at)));
      details.append(provenance); card.append(details);
    } else {
      card.append(link('Описание и подготовка на сайте ДНКом →', service.source_url, 'dl-text-link'));
    }
    return card;
  }
  function renderCatalog(data, base) {
    var root = document.querySelector('[data-analyses-catalog]');
    if (root) {
      var input = root.querySelector('[data-analysis-search]');
      var sale = root.querySelector('[data-discount-filter]');
      var list = root.querySelector('[data-analysis-list]');
      var count = root.querySelector('[data-result-count]');
      var prev = root.querySelector('[data-page-prev]');
      var next = root.querySelector('[data-page-next]');
      var pageLabel = root.querySelector('[data-page-label]');
      var page = 0; var size = 50;
      var initial = new URL(window.location.href).searchParams.get('q');
      if (initial) input.value = initial;
      function render() {
        var rows = filterServices(data.services, input.value, sale.checked);
        var pages = Math.max(1, Math.ceil(rows.length / size)); page = Math.min(page, pages - 1);
        list.replaceChildren();
        if (!rows.length) empty(list, 'По этому запросу анализы не найдены. Попробуйте другое название или точный код.');
        else rows.slice(page * size, (page + 1) * size).forEach(function (s) { list.append(serviceCard(s, base, false)); });
        count.textContent = 'Найдено: ' + rows.length.toLocaleString('ru-RU');
        pageLabel.textContent = 'Страница ' + (page + 1) + ' из ' + pages;
        prev.disabled = page === 0; next.disabled = page + 1 >= pages;
      }
      input.addEventListener('input', function () { page = 0; render(); });
      sale.addEventListener('change', function () { page = 0; render(); });
      root.querySelector('form').addEventListener('submit', function (e) { e.preventDefault(); page = 0; render(); });
      prev.addEventListener('click', function () { page -= 1; render(); list.scrollIntoView({ block: 'start' }); });
      next.addEventListener('click', function () { page += 1; render(); list.scrollIntoView({ block: 'start' }); });
      var status = root.querySelector('[data-analysis-status]');
      status.textContent = 'Цены обновлены ' + dateLabel(data.updated_at) + ' · Москва · ' + data.services.length.toLocaleString('ru-RU') + ' позиций';
      var ageDays = (Date.now() - new Date(data.updated_at).getTime()) / 86400000;
      if (ageDays > 3) { status.append(document.createTextNode(' · обновление задерживается, уточните цену перед визитом')); status.classList.add('dl-status--warning'); }
      if (['incomplete', 'unavailable'].includes(data.discount_status)) {
        var note = root.querySelector('[data-discount-status]');
        if (note) { note.hidden = false; note.textContent = 'Показаны только скидки, подтверждённые в карточках лаборатории.'; }
      }
      render();
    }
    document.querySelectorAll('[data-featured-analyses]').forEach(function (container) {
      container.replaceChildren();
      var codes = String(container.dataset.codes || '62.056,10.308,99.022,14.149,18.100,35.102').split(',');
      codes.forEach(function (code) {
        var s = data.services.find(function (service) { return service.code === code.trim(); });
        if (s) container.append(serviceCard(s, base, true));
      });
      if (!container.children.length) empty(container, 'Выбранные анализы пока не найдены в каталоге.');
    });
    document.querySelectorAll('[data-analysis-count]').forEach(function (n) { n.textContent = data.services.length.toLocaleString('ru-RU'); });
    document.querySelectorAll('[data-catalog-search-form]').forEach(function (form) {
      form.addEventListener('submit', function (event) {
        event.preventDefault();
        var target = new URL('analyses/', base); var input = form.querySelector('input');
        if (input && input.value.trim()) target.searchParams.set('q', input.value.trim());
        window.location.assign(target.href);
      });
    });
  }
  function manageContent(content, base) {
    if (!content || content.schema_version !== 1 || !content.settings || !['doctors', 'reviews', 'branches', 'promotions'].every(function (key) { return Array.isArray(content[key]); })) throw new Error('Invalid published content');
    var settings = content.settings;
    var today = moscowDate(new Date());
    var branches = published(content.branches);
    var branchMap = new Map(branches.map(function (branch) { return [branch.id, branch]; }));
    var branchRegion = document.querySelector('#centers [data-managed="branches"], #places [data-managed="branches"], [data-managed="branches"]');
    var branchSection = branchRegion && branchRegion.closest('section');
    var branchHref = branchSection && branchSection.id ? '#' + branchSection.id : null;
    function branchLinks(card, row) {
      var assigned = (Array.isArray(row.branch_ids) ? row.branch_ids : []).map(function (id) { return branchMap.get(id); }).filter(Boolean);
      if (!assigned.length) return;
      var paragraph = element('p', 'dl-applicability');
      paragraph.append(document.createTextNode('Центры: '));
      assigned.forEach(function (branch, i) {
        if (i) paragraph.append(document.createTextNode(' · '));
        paragraph.append(branchHref ? localLink(branch.name, branchHref, 'dl-text-link') : element('span', '', branch.name));
      });
      card.append(paragraph);
    }
    document.querySelectorAll('[data-branch-summary]').forEach(function (node) {
      node.replaceChildren();
      node.hidden = branches.length === 0;
      branches.forEach(function (branch) {
        var label = branch.name + (branch.opening_hours ? ' · ' + branch.opening_hours : '');
        node.append(branchHref ? localLink(label, branchHref, '') : element('span', '', label));
      });
    });
    document.querySelectorAll('[data-managed]').forEach(function (container) {
      var kind = container.dataset.managed;
      var rows = published(content[kind]);
      if (kind === 'promotions') rows = rows.filter(function (row) { return promotionIsCurrent(row, today); });
      container.replaceChildren();
      if (!rows.length) {
        empty(container, { doctors: 'Информация о врачах появится после подтверждения.', reviews: 'Отзывы с проверенными источниками будут добавлены.', branches: 'Адреса и часы работы появятся после подтверждения.', promotions: 'Сейчас нет опубликованных акций.' }[kind] || 'Информация будет добавлена.'); return;
      }
      rows.forEach(function (row) {
        var card = element('article', 'dl-content-card');
        if (kind === 'doctors') {
          var photo = safeAssetUrl(row.photo_url, base);
          if (photo) { var img = element('img', 'dl-doctor-photo'); img.src = photo; img.alt = row.name; img.loading = 'lazy'; card.append(img); }
          card.append(element('h3', '', row.name), element('p', 'dl-specialty', row.specialty));
          if (row.experience_years !== null && row.experience_years !== undefined) card.append(element('p', '', 'Опыт работы: ' + row.experience_years + ' лет'));
          if (row.bio) card.append(element('p', '', row.bio));
          branchLinks(card, row);
          if (row.rating && safeHttpUrl(row.rating_source_url)) card.append(link('Рейтинг ' + row.rating + ' · источник', row.rating_source_url, 'dl-text-link'));
          if (safeHttpUrl(row.booking_url || settings.booking_url)) card.append(link('Записаться', row.booking_url || settings.booking_url, 'dl-content-button'));
        } else if (kind === 'reviews') {
          card.append(element('p', 'dl-review-rating', 'Оценка: ' + row.rating + ' из 5'), element('blockquote', '', row.text), element('p', 'dl-review-author', row.author));
          if (row.date) card.append(element('p', 'dl-source', dateLabel(row.date)));
          card.append(link(row.source_name || 'Источник отзыва', row.source_url, 'dl-text-link'));
        } else if (kind === 'branches') {
          card.append(element('h3', '', row.name), element('p', '', row.address));
          if (row.opening_hours) card.append(element('p', 'dl-opening-hours', row.opening_hours));
          if (row.phone) { var phone = element('a', 'dl-text-link', row.phone); phone.href = 'tel:' + String(row.phone).replace(/[^+\d]/g, ''); card.append(phone); }
          if (row.email) { var email = element('a', 'dl-text-link', row.email); email.href = 'mailto:' + row.email; card.append(email); }
          if (safeHttpUrl(row.maps_url)) card.append(link('На карте', row.maps_url, 'dl-text-link'));
          if (safeHttpUrl(row.booking_url || settings.booking_url)) card.append(link('Записаться', row.booking_url || settings.booking_url, 'dl-content-button'));
        } else if (kind === 'promotions') {
          var promoImage = safeAssetUrl(row.image_url, base);
          if (promoImage) { var pic = element('img', 'dl-promotion-photo'); pic.src = promoImage; pic.alt = ''; pic.loading = 'lazy'; card.append(pic); }
          card.append(element('h3', '', row.title), element('p', '', row.description));
          if (row.ends_on) card.append(element('p', 'dl-source', 'До ' + dateLabel(row.ends_on)));
          if (row.terms) card.append(element('p', 'dl-source', row.terms));
          branchLinks(card, row);
          if (Array.isArray(row.test_codes) && row.test_codes.length) {
            var tests = element('p', 'dl-promotion-tests');
            tests.append(document.createTextNode('Анализы по акции: '));
            row.test_codes.forEach(function (code, i) {
              var test = currentCatalog && currentCatalog.services.find(function (service) { return service.code === code; });
              var target = new URL('analyses/', base); target.searchParams.set('q', code);
              if (i) tests.append(document.createTextNode(' · '));
              tests.append(localLink(code + (test ? ' — ' + test.name : ''), target.href, 'dl-text-link'));
            });
            card.append(tests);
          }
          if (safeHttpUrl(row.link_url)) card.append(link('Подробнее', row.link_url, 'dl-text-link'));
        }
        container.append(card);
      });
    });
    document.querySelectorAll('[data-setting-phone]').forEach(function (node) {
      node.textContent = settings.phone || 'Контакты будут добавлены';
      if (settings.phone) node.setAttribute('href', 'tel:' + String(settings.phone).replace(/[^+\d]/g, ''));
      else node.removeAttribute('href');
    });
    document.querySelectorAll('[data-setting-email]').forEach(function (node) {
      node.textContent = settings.email || ''; if (settings.email) node.setAttribute('href', 'mailto:' + settings.email); else node.removeAttribute('href');
    });
    document.querySelectorAll('[data-setting-booking]').forEach(function (node) {
      var url = safeHttpUrl(settings.booking_url);
      node.hidden = !url; if (url) node.href = url;
    });
    document.querySelectorAll('[data-booking-placeholder]').forEach(function (n) { n.hidden = Boolean(safeHttpUrl(settings.booking_url)); });
    document.querySelectorAll('[data-managed-legal]').forEach(function (node) {
      node.replaceChildren();
      [settings.legal_name, settings.legal_address, settings.legal_inn && 'ИНН ' + settings.legal_inn, settings.legal_ogrn && 'ОГРН ' + settings.legal_ogrn, settings.license_number && 'Лицензия ' + settings.license_number].filter(Boolean).forEach(function (text) { node.append(element('p', '', text)); });
      if (safeHttpUrl(settings.license_url)) node.append(link('Лицензия', settings.license_url, 'dl-text-link'));
      if (safeHttpUrl(settings.privacy_url)) node.append(link('Политика конфиденциальности', settings.privacy_url, 'dl-text-link'));
      if (!node.children.length) empty(node, 'Реквизиты и документы будут добавлены после подтверждения.');
    });
  }
  function interactions() {
    var modal = document.querySelector('[data-modal]'); var lastFocus;
    function close() { if (!modal) return; modal.hidden = true; modal.classList.remove('is-open'); document.body.style.overflow = ''; if (lastFocus) lastFocus.focus(); }
    document.addEventListener('click', function (event) {
      var button = event.target.closest('[data-book]');
      if (button && modal) { event.preventDefault(); lastFocus = button; modal.hidden = false; modal.classList.add('is-open'); document.body.style.overflow = 'hidden'; modal.querySelector('button[data-modal-close]').focus(); }
      if (event.target.closest('[data-modal-close]')) close();
      var lang = event.target.closest('[data-lang]');
      if (lang) { var sr = lang.dataset.lang === 'sr'; document.body.classList.toggle('lang-sr', sr); document.documentElement.lang = sr ? 'sr' : 'ru'; document.querySelectorAll('[data-lang]').forEach(function (b) { b.classList.toggle('is-active', b === lang); }); }
    });
    document.addEventListener('keydown', function (event) {
      if (!modal || modal.hidden) return;
      if (event.key === 'Escape') close();
      if (event.key === 'Tab') {
        var focusable = Array.from(modal.querySelectorAll('button, a[href], input, select')).filter(function (n) { return !n.hidden && !n.disabled && n.getClientRects().length; });
        if (!focusable.length) return;
        if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable[focusable.length - 1].focus(); }
        else if (!event.shiftKey && document.activeElement === focusable[focusable.length - 1]) { event.preventDefault(); focusable[0].focus(); }
      }
    });
    document.querySelectorAll('.reveal').forEach(function (n) { n.classList.add('is-in'); });
  }
  async function start(base) {
    interactions();
    var jobs = [];
    if (document.querySelector('[data-analyses-catalog], [data-featured-analyses], [data-analysis-count]')) jobs.push(load(new URL('data/analyses.json', base)).then(validateCatalog).then(function (data) { currentCatalog = data; renderCatalog(data, base); }).catch(function () {
      var status = document.querySelector('[data-analysis-status]');
      if (status) status.textContent = 'Не удалось загрузить цены. Проверьте соединение и обновите страницу.';
      document.querySelectorAll('[data-analysis-list], [data-featured-analyses]').forEach(function (n) { empty(n, 'Каталог временно недоступен. Попробуйте обновить страницу.'); });
      document.querySelectorAll('[data-page-prev], [data-page-next]').forEach(function (n) { n.disabled = true; });
    }));
    if (document.querySelector('[data-managed], [data-managed-legal], [data-setting-phone]')) jobs.push(load(new URL('data/content.json', base)).then(function (content) { manageContent(content, base); }).catch(function () {
      document.querySelectorAll('[data-managed]').forEach(function (n) { empty(n, 'Не удалось загрузить информацию. Попробуйте обновить страницу.'); });
    }));
    await Promise.all(jobs);
  }
  return { money: money, hasDiscount: hasDiscount, filterServices: filterServices, safeHttpUrl: safeHttpUrl, safeAssetUrl: safeAssetUrl, published: published, promotionIsCurrent: promotionIsCurrent, moscowDate: moscowDate, validateCatalog: validateCatalog, start: start };
});
