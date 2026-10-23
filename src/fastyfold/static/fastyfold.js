/* fastyfold: theme, ⌘K palette, keyboard shortcuts, list navigation and board drag-and-drop. */
(function () {
    const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
    const typing = el => el && (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || el.isContentEditable);

    // Every htmx request carries the CSRF token.
    document.addEventListener('htmx:configRequest', e => { e.detail.headers['X-CSRF-Token'] = csrf(); });

    document.addEventListener('alpine:init', () => {
        // Autocomplete for foreign keys and many-to-many: options come from the /_lookup endpoint.
        Alpine.data('ffCombo', o => ({
            name: o.name, url: o.url, multiple: o.multiple, selected: o.selected || [], disabled: o.disabled, required: o.required,
            open: false, q: '', items: [], hi: 0, loaded: false,

            toggle() {
                if (this.disabled) return;
                this.open = !this.open;
                if (!this.open) return;
                this.q = '';
                this.fetch();
                this.$nextTick(() => this.$refs.q.focus());
            },
            async fetch() {
                const q = this.q;
                const res = await fetch(`${this.url}?q=${encodeURIComponent(q)}`, { headers: { Accept: 'application/json' } });
                if (!res.ok || q !== this.q) return;   // a newer query is on its way
                this.items = (await res.json()).results;
                this.hi = 0;
                this.loaded = true;
            },
            isSelected(it) { return this.selected.some(s => s.value === it.value); },
            pick(it) {
                if (!it) return;
                if (this.multiple) {
                    this.selected = this.isSelected(it) ? this.selected.filter(s => s.value !== it.value) : [...this.selected, it];
                    return;
                }
                this.selected = [it];
                this.open = false;
                this.$refs.button.focus();
            },
            remove(it) { this.selected = this.selected.filter(s => s.value !== it.value); },
            clear() { this.selected = []; this.open = false; },
            // Esc closes an open menu and goes no further; on a closed one it keeps its global meaning (back to list).
            escape(e) {
                if (!this.open) return;
                e.stopPropagation();
                this.open = false;
                this.$refs.button.focus();
            },
            key(e) {
                const n = this.items.length;
                if (e.key === 'ArrowDown') { e.preventDefault(); if (n) this.hi = (this.hi + 1) % n; }
                else if (e.key === 'ArrowUp') { e.preventDefault(); if (n) this.hi = (this.hi - 1 + n) % n; }
                else if (e.key === 'Enter') { e.preventDefault(); this.pick(this.items[this.hi]); }   // never submit the form from here
                else if (e.key === 'Backspace' && !this.q && this.multiple && this.selected.length) this.selected = this.selected.slice(0, -1);
            },
        }));

        Alpine.data('ff', () => ({
            theme: localStorage.getItem('ff-theme') || 'system',
            palette: false,
            keys: false,
            pq: '',
            pi: 0,
            results: [],
            searched: null,
            cursor: -1,
            gPending: false,
            prefix: document.body.dataset.prefix || '',
            jumps: JSON.parse(document.body.dataset.jumps || '{}'),

            setTheme(t) {
                this.theme = t;
                try { localStorage.setItem('ff-theme', t); } catch (e) { /* private mode */ }
                const dark = t === 'dark' || (t === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
                document.documentElement.classList.toggle('dark', dark);
            },
            toggleTheme() { this.setTheme(document.documentElement.classList.contains('dark') ? 'light' : 'dark'); },

            // ── palette ──────────────────────────────────────────────────
            openPalette() {
                this.palette = true;
                this.pi = 0;
                this.search();
                this.$nextTick(() => this.$refs.pq?.select());   // select, so typing replaces the last query
            },
            async search() {
                const q = this.pq;
                const res = await fetch(`${this.prefix}/_search?q=${encodeURIComponent(q)}`, { headers: { Accept: 'application/json' } });
                if (!res.ok || q !== this.pq) return;   // a newer query is on its way
                let k = 0;
                this.results = (await res.json()).groups.map(g => ({ ...g, items: g.items.map(it => ({ ...it, k: k++ })) }));
                this.pi = 0;
                this.searched = q;
            },
            flat() { return this.results.flatMap(g => g.items); },
            hl(text) {
                const esc = s => s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
                const q = this.pq.trim();
                const i = q ? text.toLowerCase().indexOf(q.toLowerCase()) : -1;
                if (i < 0) return esc(text);
                return esc(text.slice(0, i)) + `<mark class="rounded-sm bg-accent/25 text-fg">${esc(text.slice(i, i + q.length))}</mark>` + esc(text.slice(i + q.length));
            },
            run(it, newTab = false) {
                this.palette = false;
                if (it.cmd === 'theme') return this.toggleTheme();
                if (it.cmd === 'keys') return (this.keys = true);
                if (it.url) newTab ? window.open(it.url, '_blank') : (location.href = it.url);
            },

            // ── list rows (table, board, split) ──────────────────────────
            rows() { return [...document.querySelectorAll('[data-row]')]; },
            moveCursor(step) {
                const rows = this.rows();
                if (!rows.length) return;
                this.cursor = Math.max(0, Math.min(rows.length - 1, this.cursor + step));
                rows.forEach((r, i) => r.toggleAttribute('data-active', i === this.cursor));
                const row = rows[this.cursor];
                row.scrollIntoView({ block: 'nearest' });
                if (row.dataset.preview) htmx.trigger(row, 'click');
            },
            activeRow() { return this.rows()[this.cursor] || document.querySelector('[data-row][data-active]'); },

            // ── keyboard ─────────────────────────────────────────────────
            key(e) {
                const k = e.key, mod = e.metaKey || e.ctrlKey;
                if (mod && k.toLowerCase() === 'k') { e.preventDefault(); this.palette ? (this.palette = false) : this.openPalette(); return; }
                if (k === 'Escape') {
                    if (this.palette || this.keys) { this.palette = this.keys = false; return; }
                    const back = document.querySelector('[data-back]');
                    if (back && !typing(e.target)) location.href = back.href;
                    return;
                }
                if (this.palette) {
                    if (k === 'Enter') {
                        // Typed faster than the debounce: search first so Enter never runs a stale result.
                        e.preventDefault();
                        const go = () => { const it = this.flat()[this.pi]; if (it) this.run(it, mod); };
                        this.searched === this.pq ? go() : this.search().then(go);
                        return;
                    }
                    const n = this.flat().length;
                    if (!n) return;
                    if (k === 'ArrowDown') { e.preventDefault(); this.pi = (this.pi + 1) % n; }
                    else if (k === 'ArrowUp') { e.preventDefault(); this.pi = (this.pi - 1 + n) % n; }
                    return;
                }
                // Form saving works even while typing.
                if (mod && k.toLowerCase() === 's') { const b = document.querySelector('[data-save]'); if (b) { e.preventDefault(); b.click(); } return; }
                if (mod && k === 'Enter') { const b = document.querySelector('[data-continue]'); if (b) { e.preventDefault(); b.click(); } return; }
                if (typing(e.target) || mod || e.altKey) return;

                if (this.gPending) {
                    this.gPending = false;
                    const url = this.jumps[k.toLowerCase()];
                    if (url) location.href = url;
                    return;
                }
                if (k === 'g') { this.gPending = true; setTimeout(() => (this.gPending = false), 900); return; }
                if (k === '?') { this.keys = !this.keys; return; }
                if (k === '/') { e.preventDefault(); this.openPalette(); return; }
                if (k === 't') { this.toggleTheme(); return; }
                if (k === 'n') { const a = document.querySelector('[data-new]'); if (a) location.href = a.href; return; }
                if (k === 'f') { window.dispatchEvent(new CustomEvent('ff-filters')); return; }
                if (['1', '2', '3'].includes(k)) { const a = document.querySelector(`[data-view-key="${k}"]`); if (a) location.href = a.href; return; }
                if (k === 'j' || k === 'ArrowDown') { if (this.rows().length) { e.preventDefault(); this.moveCursor(1); } return; }
                if (k === 'k' || k === 'ArrowUp') { if (this.rows().length) { e.preventDefault(); this.moveCursor(-1); } return; }
                if (k === 'Enter' || k === 'e') { const r = this.activeRow(); if (r) location.href = r.dataset.url; return; }
                if (k === 'x') { const box = this.activeRow()?.querySelector('input[name=ids]'); if (box) box.click(); return; }
                // @action(key=...): runs on the selected rows (bulk bar) or the previewed record (split view).
                const act = [...document.querySelectorAll(`[data-action-key="${CSS.escape(k.toLowerCase())}"]`)].find(b => b.offsetParent);
                if (act) act.click();
            },
        }));
    });

    // ── board drag and drop ──────────────────────────────────────────────
    let dragged = null;
    document.addEventListener('dragstart', e => {
        dragged = e.target.closest?.('[data-pk]');
        if (dragged) { e.dataTransfer.effectAllowed = 'move'; dragged.classList.add('opacity-50'); }
    });
    document.addEventListener('dragend', () => { dragged?.classList.remove('opacity-50'); dragged = null; });
    document.addEventListener('dragover', e => { if (dragged && e.target.closest?.('[data-column]')) e.preventDefault(); });
    document.addEventListener('drop', async e => {
        const col = e.target.closest?.('[data-column]');
        if (!dragged || !col) return;
        e.preventDefault();
        const card = dragged;
        const from = card.closest('[data-column]');
        if (from === col) return;
        col.querySelector('[data-drop]').prepend(card);
        const body = new URLSearchParams({ value: col.dataset.column, _csrf: csrf() });
        const res = await fetch(`${col.dataset.moveUrl}/${card.dataset.pk}/move`, { method: 'POST', body, headers: { 'X-CSRF-Token': csrf() } });
        if (!res.ok) { from.querySelector('[data-drop]').prepend(card); alert('Could not move this card. Reload and try again.'); }
    });
})();
