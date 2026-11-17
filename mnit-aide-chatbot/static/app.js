(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const el = { app:$('app'), rail:$('rail'), scrim:$('scrim'), history:$('history'), thread:$('thread'),
    scroller:$('scroller'), welcome:$('welcome'), starters:$('starters'), input:$('input'),
    send:$('send'), composer:$('composer'), title:$('chatTitle'), status:$('status'),
    drawer:$('drawer'), toast:$('toast') };

  /* ---------- storage (safe if blocked) ---------- */
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
  };
  let chats = store.get('aide.chats', []);
  let activeId = store.get('aide.active', null);
  let busy = null;          // AbortController while streaming
  let pinned = true;        // auto-scroll while the user hasn't scrolled up

  const uid = () => Math.random().toString(36).slice(2, 10);
  const save = () => { store.set('aide.chats', chats.slice(0, 60)); store.set('aide.active', activeId); };
  const current = () => chats.find((c) => c.id === activeId);

  /* ---------- helpers ---------- */
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  function toast(msg) {
    el.toast.textContent = msg; el.toast.classList.add('show');
    clearTimeout(toast.t); toast.t = setTimeout(() => el.toast.classList.remove('show'), 1800);
  }
  const kindOf = (s) => s.page_type ? s.page_type.replace(/_/g, ' ')
    : (s.filename && s.filename.endsWith('.pdf') ? 'PDF document' : 'Web page');
  const titleOf = (s) => {
    if (s.title && s.title !== 'Unknown') return s.title;
    try { const u = new URL(s.source_url); return u.pathname.replace(/\/$/, '').split('/').pop() || 'AIDE home'; }
    catch { return s.filename || 'Source'; }
  };

  /* ---------- safe mini markdown ---------- */
  function inline(t, sources) {
    t = esc(t);
    t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
    t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    t = t.replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');
    t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    t = t.replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:])/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
    t = t.replace(/(^|[\s(])([\w.+-]+@[\w-]+(?:\.[\w-]+)+)/g, '$1<a href="mailto:$2">$2</a>');
    t = t.replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (m, nums) =>
      nums.split(/\s*,\s*/).map((n) => (+n >= 1 && +n <= sources.length)
        ? `<button type="button" class="cite" data-n="${n}" aria-label="Source ${n}">${n}</button>` : '').join(''));
    return t;
  }
  function render(md, sources) {
    const out = []; let list = null;
    const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
    const lines = md.replace(/\r/g, '').split('\n');
    const isRow = (l) => /^\s*\|.*\|\s*$/.test(l);
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trimEnd();
      let m;
      if (isRow(line) && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1])) {
        close();
        const cells = (r) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
        const head = cells(line); i += 2; const rows = [];
        while (i < lines.length && isRow(lines[i])) rows.push(cells(lines[i++]));
        i--;
        out.push(`<div class="tbl"><table><thead><tr>${head.map((c) => `<th>${inline(c, sources)}</th>`).join('')}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c, sources)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
      }
      else if ((m = line.match(/^(#{1,6})\s+(.*)/))) { close(); out.push(`<h4>${inline(m[2], sources)}</h4>`); }
      else if ((m = line.match(/^\s*[-*•]\s+(.*)/))) { if (list !== 'ul') { close(); out.push('<ul>'); list = 'ul'; } out.push(`<li>${inline(m[1], sources)}</li>`); }
      else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== 'ol') { close(); out.push('<ol>'); list = 'ol'; } out.push(`<li>${inline(m[1], sources)}</li>`); }
      else if (!line.trim()) close();
      else { close(); out.push(`<p>${inline(line, sources)}</p>`); }
    }
    close(); return out.join('');
  }

  /* ---------- sidebar ---------- */
  function renderHistory() {
    el.history.innerHTML = '';
    if (!chats.length) { el.history.innerHTML = '<p class="empty-hist">Your chats will appear here.</p>'; return; }
    for (const c of chats) {
      const row = document.createElement('div');
      row.className = 'h-item' + (c.id === activeId ? ' active' : '');
      row.innerHTML = `<button class="open" type="button">${esc(c.title)}</button><button class="del" type="button" aria-label="Delete chat">✕</button>`;
      row.querySelector('.open').onclick = () => { openChat(c.id); closeRail(); };
      row.querySelector('.del').onclick = () => {
        chats = chats.filter((x) => x.id !== c.id);
        if (activeId === c.id) { activeId = null; showChat(); }
        save(); renderHistory();
      };
      el.history.appendChild(row);
    }
  }
  const openRail = () => { el.rail.classList.add('open'); el.scrim.hidden = false; };
  const closeRail = () => { el.rail.classList.remove('open'); el.scrim.hidden = true; };
  $('openRail').onclick = openRail; el.scrim.onclick = closeRail;
  $('newChat').onclick = () => { if (busy) busy.abort(); activeId = null; showChat(); renderHistory(); closeRail(); el.input.focus(); };
  $('clearAll').onclick = () => { if (confirm('Delete all saved chats from this browser?')) { if (busy) busy.abort(); chats = []; activeId = null; save(); showChat(); renderHistory(); } };

  /* ---------- thread ---------- */
  function showChat() {
    closeDrawer();
    [...el.thread.children].forEach((n) => { if (n !== el.welcome) n.remove(); });
    const c = current();
    el.welcome.hidden = !!(c && c.messages.length);
    el.title.textContent = c ? c.title : 'New chat';
    if (c) c.messages.forEach((m, i) => addMessage(m, i));
    if (el.welcome.hidden) scrollDown(true); else el.scroller.scrollTop = 0;
  }
  function openChat(id) { if (busy) busy.abort(); activeId = id; save(); showChat(); renderHistory(); }

  function addMessage(m, index) {
    const wrap = document.createElement('div');
    wrap.className = 'msg ' + (m.role === 'user' ? 'user' : 'bot');
    wrap.dataset.index = index;
    if (m.role === 'user') {
      const b = document.createElement('div'); b.className = 'bubble'; b.textContent = m.content; wrap.appendChild(b);
    } else { fillBot(wrap, m); }
    el.thread.appendChild(wrap); return wrap;
  }

  function fillBot(wrap, m, streaming) {
    wrap.innerHTML = '';
    const sources = m.sources || [];
    if (m.error) {
      wrap.innerHTML = `<div class="err"><span>${esc(m.error)}</span><button type="button" data-retry>Try again</button></div>`;
      wrap.querySelector('[data-retry]').onclick = () => regenerate();
      return;
    }
    const a = document.createElement('div'); a.className = 'answer';
    if (!m.content && streaming) {
      a.innerHTML = '<div class="thinking"><span class="dots"><b></b><b></b><b></b></span><span>Searching AIDE pages…</span></div>';
    } else {
      a.innerHTML = render(m.content, sources) + (streaming ? '<span class="caret"></span>' : '');
    }
    wrap.appendChild(a);
    a.querySelectorAll('.cite').forEach((b) => {
      b.onclick = () => openSource(sources[+b.dataset.n - 1], +b.dataset.n);
      b.onmouseenter = () => toggleSrc(wrap, b.dataset.n, true);
      b.onmouseleave = () => toggleSrc(wrap, b.dataset.n, false);
    });
    if (streaming || !m.content) return;

    const cited = [...new Set([...m.content.matchAll(/\[(\d+(?:\s*,\s*\d+)*)\]/g)]
      .flatMap((x) => x[1].split(/\s*,\s*/).map(Number)))].filter((n) => n >= 1 && n <= sources.length);
    const show = cited.length ? cited : sources.slice(0, 3).map((_, i) => i + 1);
    if (show.length) {
      const row = document.createElement('div'); row.className = 'sources';
      row.innerHTML = `<span>${cited.length ? 'Sources' : 'Related pages'}</span>`;
      show.forEach((n) => {
        const s = sources[n - 1]; const direct = !s.text && s.source_url;
        const b = document.createElement(direct ? 'a' : 'button');
        if (direct) { b.href = s.source_url; b.target = '_blank'; b.rel = 'noopener'; b.title = s.source_url; } else b.type = 'button';
        b.className = 'src'; b.dataset.n = n;
        b.innerHTML = `<b>${n}</b><em>${esc(titleOf(s))}</em>`;
        if (!direct) b.onclick = () => openSource(s, n);
        row.appendChild(b);
      });
      wrap.appendChild(row);
    }
    const act = document.createElement('div'); act.className = 'actions';
    act.innerHTML = `<button type="button" data-a="copy">Copy</button><button type="button" data-a="up" aria-label="Helpful">Helpful</button><button type="button" data-a="down" aria-label="Not helpful">Not helpful</button><button type="button" data-a="redo">Regenerate</button>`;
    if (m.rating) act.querySelector(`[data-a="${m.rating}"]`).classList.add('sel');
    act.onclick = (e) => {
      const t = e.target.closest('button'); if (!t) return;
      const kind = t.dataset.a;
      if (kind === 'copy') navigator.clipboard?.writeText(m.content).then(() => toast('Answer copied'), () => toast('Copy failed'));
      else if (kind === 'redo') regenerate();
      else rate(wrap, m, kind, act);
    };
    wrap.appendChild(act);
  }
  const toggleSrc = (wrap, n, on) => wrap.querySelectorAll(`.src[data-n="${n}"]`).forEach((s) => s.classList.toggle('on', on));

  async function rate(wrap, m, kind, act) {
    const c = current(); const idx = +wrap.dataset.index;
    const question = (c.messages[idx - 1] || {}).content || '';
    m.rating = kind; save();
    act.querySelectorAll('[data-a="up"],[data-a="down"]').forEach((b) => b.classList.toggle('sel', b.dataset.a === kind));
    toast(kind === 'up' ? 'Thanks for the feedback' : 'Noted, thanks');
  }

  /* ---------- source drawer ---------- */
  let lastQuestion = '';
  function openSource(s, n) {
    if (!s) return;
    $('dKind').textContent = kindOf(s);
    $('dTitle').textContent = `[${n}] ${titleOf(s)}`;
    const link = $('dLink'); link.href = s.source_url || '#'; link.textContent = s.source_url || s.filename;
    link.hidden = !s.source_url;
    const words = [...new Set((lastQuestion.toLowerCase().match(/[a-z0-9@.]{4,}/g) || []))]
      .filter((w) => !['what','which','where','when','about','department','aide','does','have','list','with','from','that','this'].includes(w));
    let html = esc(s.text || 'Open the link above to read the original page.');
    if (words.length) html = html.replace(new RegExp('(' + words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi'), '<mark>$1</mark>');
    $('dText').innerHTML = html;
    el.drawer.hidden = false; el.app.classList.add('drawer-open'); el.drawer.scrollTop = 0;
  }
  function closeDrawer() { el.drawer.hidden = true; el.app.classList.remove('drawer-open'); }
  $('dClose').onclick = closeDrawer;
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeDrawer(); closeRail(); }
    if (e.key === '/' && document.activeElement !== el.input && !e.ctrlKey && !e.metaKey) { e.preventDefault(); el.input.focus(); }
  });

  /* ---------- scrolling ---------- */
  el.scroller.addEventListener('scroll', () => {
    pinned = el.scroller.scrollHeight - el.scroller.scrollTop - el.scroller.clientHeight < 80;
  });
  function scrollDown(force) {
    if (force || pinned) el.scroller.scrollTo({ top: el.scroller.scrollHeight, behavior: force ? 'instant' : 'auto' });
  }

  /* ---------- sending ---------- */
  function setBusy(on, controller) {
    busy = on ? controller : null;
    el.send.classList.toggle('busy', on);
    el.send.setAttribute('aria-label', on ? 'Stop answer' : 'Send question');
    el.send.disabled = !on && !el.input.value.trim();
  }

  async function ask(text) {
    text = text.trim(); if (!text || busy) return;
    let c = current();
    if (!c) {
      c = { id: uid(), title: text.length > 44 ? text.slice(0, 42) + '…' : text, messages: [] };
      chats.unshift(c); activeId = c.id;
    }
    el.welcome.hidden = true; el.title.textContent = c.title;
    c.messages.push({ role: 'user', content: text });
    addMessage(c.messages[c.messages.length - 1], c.messages.length - 1);
    el.input.value = ''; autosize(); pinned = true;
    await answer(c, text);
    renderHistory();
  }

  async function answer(c, text) {
    lastQuestion = text;
    const history = c.messages.slice(0, -1).filter((m) => !m.error).slice(-6).map((m) => ({ role: m.role, content: m.content }));
    const bot = { role: 'assistant', content: '', sources: [] };
    c.messages.push(bot);
    const wrap = addMessage(bot, c.messages.length - 1);
    fillBot(wrap, bot, true); scrollDown(true);

    const controller = new AbortController(); setBusy(true, controller);
    let failed = null;
    try {
      const res = await fetch('/api/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
        body: JSON.stringify({ message: text, history }),
      });
      if (!res.ok) {
        let d = ''; try { d = (await res.json()).detail; } catch {}
        throw new Error(typeof d === 'string' && d ? d : `Server error (${res.status}).`);
      }
      const data = await res.json();
      bot.sources = (data.sources || []).map((x) => ({ title: x.title, source_url: x.url || x.source_url, text: x.text }));
      const full = data.answer || ''; const step = Math.max(3, Math.ceil(full.length / 120));
      for (let n = 0; n < full.length; n += step) {
        if (controller.signal.aborted) throw new DOMException('Aborted', 'AbortError');
        bot.content = full.slice(0, n + step); fillBot(wrap, bot, true); scrollDown();
        await new Promise((r) => setTimeout(r, 16));
      }
      bot.content = full;
    } catch (err) {
      if (err.name === 'AbortError') { if (!bot.content) failed = 'Stopped.'; }
      else failed = err.message === 'Failed to fetch' ? 'Cannot reach the server. Check that it is running, then try again.' : err.message;
    }
    if (failed && !bot.content) { bot.error = failed; }
    fillBot(wrap, bot, false);
    if (!bot.content && !bot.error) { bot.error = 'No answer was returned.'; fillBot(wrap, bot, false); }
    setBusy(false); save(); scrollDown();
  }

  function regenerate() {
    if (busy) return;
    const c = current(); if (!c) return;
    while (c.messages.length && c.messages[c.messages.length - 1].role !== 'user') c.messages.pop();
    const last = c.messages[c.messages.length - 1]; if (!last) return;
    showChat(); pinned = true; answer(c, last.content).then(renderHistory);
  }

  /* ---------- composer ---------- */
  function autosize() { el.input.style.height = 'auto'; el.input.style.height = Math.min(el.input.scrollHeight, 160) + 'px'; if (!busy) el.send.disabled = !el.input.value.trim(); }
  el.input.addEventListener('input', autosize);
  el.input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); el.composer.requestSubmit(); } });
  el.composer.addEventListener('submit', (e) => { e.preventDefault(); if (busy) busy.abort(); else ask(el.input.value); });

  /* ---------- theme ---------- */
  $('themeBtn').onclick = () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    root.dataset.theme = dark ? 'light' : 'dark';
    try { localStorage.setItem('aide.theme', root.dataset.theme); } catch {}
  };

  /* ---------- boot ---------- */
  async function boot() {
    el.send.disabled = true;
    const groups = [
      { group: 'People', items: ['How many faculty members are in the AIDE department?', 'Who are the joint and adjunct faculty?'] },
      { group: 'Academics', items: ['What programs and courses does the AIDE department offer?', 'What labs and facilities are available?'] },
      { group: 'Research & contact', items: ['What research projects and patents does the department have?', 'How can I contact the AIDE department?'] },
    ];
    el.starters.innerHTML = groups.map((g) => `<div><h3>${esc(g.group)}</h3><ul>${g.items.map((q) => `<li><button type="button">${esc(q)}</button></li>`).join('')}</ul></div>`).join('');
    el.starters.onclick = (e) => { const b = e.target.closest('button'); if (b) ask(b.textContent); };
    try {
      const s = await (await fetch('/api/status')).json();
      el.status.classList.add(s.ready ? 'ok' : 'bad');
      el.status.lastElementChild.textContent = s.ready ? `${s.documents} pages indexed` : 'Run ingest.py';
      el.status.title = s.ready ? `${s.chunks} knowledge chunks loaded` : 'Knowledge base not found';
    } catch { el.status.classList.add('bad'); el.status.lastElementChild.textContent = 'Offline'; }
    if (!current()) activeId = null;
    showChat(); renderHistory(); el.input.focus();
  }
  boot();
})();
