/* Progressive enhancement: native select values remain the submitted source of truth. */
(() => {
    const normalize = (text) => text.normalize('NFKD').replace(/\p{Diacritic}/gu, '').toLowerCase();
    class SearchableSelect {
        constructor(select) {
            this.select = select;
            this.placeholder = select.dataset.placeholder || 'Select an option';
            this.wrapper = document.createElement('div');
            this.wrapper.className = 'searchable-select';
            this.input = document.createElement('input');
            this.input.type = 'text';
            this.input.id = `${select.id}-search`;
            this.input.autocomplete = 'off';
            this.input.spellcheck = false;
            this.input.setAttribute('role', 'combobox');
            this.input.setAttribute('aria-autocomplete', 'list');
            this.input.setAttribute('aria-haspopup', 'listbox');
            this.input.setAttribute('aria-expanded', 'false');
            this.input.setAttribute('aria-controls', `${select.id}-options`);
            if (select.required) this.input.setAttribute('aria-required', 'true');
            for (const attr of ['aria-invalid', 'aria-describedby']) {
                if (select.hasAttribute(attr)) this.input.setAttribute(attr, select.getAttribute(attr));
            }
            this.list = document.createElement('ul');
            this.list.id = `${select.id}-options`;
            this.list.className = 'select-options';
            this.list.setAttribute('role', 'listbox');
            this.list.hidden = true;
            const label = select.closest('.form-field').querySelector('label');
            label.htmlFor = this.input.id;
            label.id ||= `${select.id}-label`;
            this.list.setAttribute('aria-labelledby', label.id);
            this.wrapper.append(this.input, this.list);
            select.insertAdjacentElement('afterend', this.wrapper);
            select.classList.add('enhanced-native-select');
            select.setAttribute('aria-hidden', 'true');
            select.tabIndex = -1;
            this.sync();
            this.input.addEventListener('focus', () => { this.input.select(); this.open(''); });
            this.input.addEventListener('click', () => { if (this.list.hidden) this.open(''); });
            this.input.addEventListener('input', () => {
                this.input.setCustomValidity('');
                if (!this.input.value && this.select.value) this.choose('', false);
                this.open(this.input.value);
            });
            this.input.addEventListener('keydown', (event) => this.keydown(event));
            this.input.addEventListener('blur', () => setTimeout(() => { this.close(); this.sync(); }, 0));
            this.list.addEventListener('mousedown', (event) => event.preventDefault());
            this.select.addEventListener('change', () => this.sync());
            this.select.addEventListener('focus', () => this.input.focus());
            this.select.addEventListener('invalid', (event) => {
                event.preventDefault();
                this.input.setCustomValidity('Choose a value from the list.');
                this.input.focus();
                this.input.reportValidity();
            });
        }
        sync() {
            this.input.value = this.select.value ? this.select.selectedOptions[0]?.textContent || '' : '';
            this.input.disabled = this.select.disabled;
            this.input.placeholder = this.placeholder;
        }
        open(query) {
            if (this.input.disabled) return;
            const terms = normalize(query.trim()).split(/\s+/).filter(Boolean);
            this.matches = [...this.select.options].filter((o) => !o.disabled && (!o.value || terms.every((term) => normalize(`${o.textContent} ${o.value}`).includes(term))));
            if (terms.length) {
                this.matches = this.matches.filter((o) => o.value).sort((a, b) => {
                    const rank = (o) => terms.every((t) => normalize(o.textContent).startsWith(t)) ? 0 : 1;
                    return rank(a) - rank(b);
                });
            }
            this.list.replaceChildren();
            this.active = -1;
            this.matches.forEach((option, index) => {
                const item = document.createElement('li');
                item.id = `${this.select.id}-option-${index}`;
                item.setAttribute('role', 'option');
                item.setAttribute('aria-selected', String(option.value === this.select.value));
                item.textContent = option.textContent;
                item.addEventListener('click', () => this.choose(option.value));
                this.list.append(item);
            });
            if (!this.matches.length) {
                const empty = document.createElement('li');
                empty.className = 'no-options';
                empty.setAttribute('role', 'option');
                empty.setAttribute('aria-disabled', 'true');
                empty.textContent = 'No matching options';
                this.list.append(empty);
            }
            this.list.hidden = false;
            const box = this.wrapper.getBoundingClientRect();
            const menuWidth = Math.min(Math.max(box.width, 260), window.innerWidth - 32);
            const alignRight = box.left + menuWidth > window.innerWidth - 12;
            this.list.style.left = alignRight ? 'auto' : '0';
            this.list.style.right = alignRight ? '0' : 'auto';
            this.list.style.width = `${menuWidth}px`;
            const below = window.innerHeight - box.bottom - 8;
            const above = box.top - 8;
            const openAbove = below < 150 && above > below;
            this.list.style.top = openAbove ? 'auto' : 'calc(100% + 4px)';
            this.list.style.bottom = openAbove ? 'calc(100% + 4px)' : 'auto';
            this.list.style.maxHeight = `${Math.max(60, Math.min(230, openAbove ? above : below))}px`;
            this.wrapper.classList.add('is-open');
            this.input.setAttribute('aria-expanded', 'true');
            this.input.removeAttribute('aria-activedescendant');
        }
        close() {
            this.list.hidden = true;
            this.wrapper.classList.remove('is-open');
            this.input.setAttribute('aria-expanded', 'false');
            this.input.removeAttribute('aria-activedescendant');
        }
        choose(value, close = true) {
            this.select.value = value;
            this.input.setCustomValidity('');
            this.select.dispatchEvent(new Event('change', {bubbles: true}));
            this.sync();
            if (close) this.close();
        }
        keydown(event) {
            if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
                if (this.list.hidden && ['Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                if (this.list.hidden) this.open('');
                if (!this.matches.length) return;
                const last = this.matches.length - 1;
                this.active = event.key === 'Home' ? 0 : event.key === 'End' ? last :
                    event.key === 'ArrowDown' ? Math.min(last, this.active + 1) : (this.active < 0 ? last : Math.max(0, this.active - 1));
                [...this.list.children].forEach((item, index) => item.classList.toggle('is-highlighted', index === this.active));
                const active = this.list.children[this.active];
                this.input.setAttribute('aria-activedescendant', active.id);
                active.scrollIntoView({block: 'nearest'});
            } else if (event.key === 'Enter' && !this.list.hidden) {
                event.preventDefault();
                if (this.matches.length) this.choose(this.matches[Math.max(0, this.active)].value);
            } else if (event.key === 'Escape' || event.key === 'Tab') {
                if (event.key === 'Escape' && !this.list.hidden) event.preventDefault();
                this.close();
                this.sync();
            }
        }
        replace(options, placeholder, disabled) {
            this.select.replaceChildren(new Option(placeholder, ''), ...options.map((o) => new Option(o.label, o.value)));
            this.select.value = '';
            this.select.disabled = disabled;
            this.placeholder = placeholder;
            this.input.setCustomValidity('');
            this.close();
            this.sync();
        }
    }
    const controls = new Map();
    document.querySelectorAll('select[data-searchable]').forEach((select) => controls.set(select.id, new SearchableSelect(select)));
    const form = document.querySelector('form[data-regions-url]');
    const country = document.getElementById('country_code');
    const region = controls.get('state_region_code');
    if (!form || !country || !region) return;
    const error = form.querySelector('.lookup-load-error');
    const submit = form.querySelector('button[type=submit]');
    let controller;
    let sequence = 0;
    let loading = false;
    let failed = false;
    const loadRegions = async () => {
        const current = ++sequence;
        controller?.abort();
        error.hidden = true;
        failed = false;
        if (!country.value) {
            region.replace([], 'Select country first', true);
            loading = false;
            submit.disabled = false;
            return;
        }
        region.replace([], 'Loading states…', true);
        loading = true;
        submit.disabled = true;
        controller = new AbortController();
        try {
            const response = await fetch(`${form.dataset.regionsUrl}?country=${encodeURIComponent(country.value)}`, {
                signal: controller.signal, headers: {'Accept': 'application/json'}, credentials: 'same-origin'
            });
            if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('Unavailable');
            const options = await response.json();
            if (!Array.isArray(options)) throw new Error('Invalid choices');
            if (current !== sequence) return;
            region.replace(options, options.length ? 'Select state / region' : 'No listed states / regions', !options.length);
        } catch (exception) {
            if (current !== sequence) return;
            failed = true;
            error.hidden = false;
            region.replace([], 'States unavailable', true);
        } finally {
            if (current === sequence) { loading = false; submit.disabled = failed; }
        }
        return current === sequence && !failed;
    };
    country.addEventListener('change', loadRegions);
    form.applyAddressLocation = async (location) => {
        country.value = location.country_code;
        controls.get('country_code').sync();
        const loaded = await loadRegions();
        if (!loaded) return false;
        if ([...region.select.options].some(o => o.value === location.state_region_code)) region.choose(location.state_region_code);
        const zone = controls.get('timezone');
        if (location.timezone && [...zone.select.options].some(o => o.value === location.timezone)) zone.choose(location.timezone);
        return true;
    };
    form.querySelector('[data-retry-regions]').addEventListener('click', loadRegions);
    form.addEventListener('submit', (event) => { if (loading || failed) event.preventDefault(); });
})();
