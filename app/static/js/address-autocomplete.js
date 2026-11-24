/* Google lookup runs only on Find address and explicit result selection. */
(() => {
    const container = document.querySelector('[data-address-url]');
    if (!container) return;
    const form = container.closest('form');
    if (!form.applyAddressLocation) return;
    const street = form.querySelector('#address_line_1');
    const dialog = container.querySelector('.address-search-dialog');
    if (typeof dialog.showModal !== 'function') return;
    const opener = container.querySelector('[data-open-address]');
    const input = dialog.querySelector('#address-search-query');
    const country = form.querySelector('#country_code');
    const button = container.querySelector('[data-find-address]');
    const status = container.querySelector('.address-status');
    const provider = container.querySelector('.address-card-credit');
    const list = document.createElement('ul');
    list.className = 'select-options address-options';
    list.id = 'address-suggestions';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', 'Suggested addresses');
    list.hidden = true;
    dialog.querySelector('[data-address-results]').append(list);
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', list.id);
    input.setAttribute('aria-expanded', 'false');
    let busy = false, controller, version = 0, rows = [], active = -1;
    const close = () => {
        list.hidden = true;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
    };
    const cancel = () => {
        version++;
        controller?.abort();
        rows = [];
        active = -1;
        close();
        status.textContent = '';
    };
    const post = async (fields) => {
        controller = new AbortController();
        const body = new URLSearchParams({...fields, csrf_token: form.querySelector('[name=csrf_token]').value});
        const response = await fetch(container.dataset.addressUrl, {
            method: 'POST', body, credentials: 'same-origin', signal: controller.signal,
            headers: {'Accept': 'application/json'}
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Address lookup unavailable. Enter the address manually.');
        return data;
    };
    const run = async (operation) => {
        if (busy) return;
        busy = true;
        button.disabled = true;
        container.setAttribute('aria-busy', 'true');
        const current = version;
        try { await operation(current); }
        catch (error) {
            if (current === version && error.name !== 'AbortError') {
                close();
                status.textContent = error.message || 'Address lookup unavailable. Enter the address manually.';
            }
        } finally {
            busy = false;
            button.disabled = false;
            container.removeAttribute('aria-busy');
        }
    };
    const choose = (row) => run(async (current) => {
        close();
        status.textContent = 'Loading address...';
        const data = await post({action: 'details', selection: row.selection});
        if (current !== version) return;
        // Suites/floors in address line 2 remain user-controlled.
        if (!data.address_line_1) throw new Error('No street address returned. Enter the address manually.');
        street.value = data.address_line_1;
        form.querySelector('#city').value = data.city;
        form.querySelector('#postal_code').value = data.postal_code;
        await form.applyAddressLocation(data);
        if (current !== version) return;
        rows = [];
        const attribution = container.querySelector('[data-address-attributions]');
        attribution.replaceChildren();
        (data.attributions || []).forEach(item => {
            if (!item.uri.startsWith('https://')) return;
            const link = document.createElement('a');
            link.href = item.uri;
            link.textContent = item.name;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            attribution.append(' · ', link);
        });
        provider.hidden = false;
        dialog.close();
    });
    button.addEventListener('click', () => {
        if (busy) return;
        cancel();
        const text = input.value.trim();
        if (input.value.trim().length < 5 || text.length > 250) {
            status.textContent = 'Enter a street number and street name, then find the address.';
            input.focus();
            return;
        }
        if (country.value && !['US', 'CA'].includes(country.value)) {
            status.textContent = 'Lookup supports US and Canadian addresses. Enter other addresses manually.';
            return;
        }
        run(async (current) => {
            status.textContent = 'Finding address...';
            const data = await post({action: 'search', text, country: country.value});
            if (current !== version) return;
            if (!Array.isArray(data)) throw new Error('Address lookup unavailable. Enter the address manually.');
            rows = data;
            active = -1;
            list.replaceChildren();
            rows.forEach((row, index) => {
                const option = document.createElement('li');
                option.id = `address-option-${index}`;
                option.setAttribute('role', 'option');
                option.setAttribute('aria-selected', 'false');
                option.textContent = row.label;
                option.addEventListener('click', () => { if (!busy) choose(row); });
                list.append(option);
            });
            list.hidden = !rows.length;
            input.setAttribute('aria-expanded', String(rows.length > 0));
            status.textContent = rows.length ? 'Select an address.' : 'No matches. Refine the address or enter it manually.';
            input.focus();
        });
    });
    // Editing only dismisses stale results: no timers, requests, or automatic searches.
    input.addEventListener('input', cancel);
    country.addEventListener('change', cancel);
    list.addEventListener('mousedown', event => event.preventDefault());
    document.addEventListener('click', event => { if (!container.contains(event.target)) close(); });
    input.addEventListener('keydown', event => {
        if (event.key === 'Enter') {
            event.preventDefault();
            if (!list.hidden && rows.length && !busy) choose(rows[Math.max(0, active)]);
            else if (list.hidden && !busy) button.click();
            return;
        }
        if (list.hidden) return;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            active = event.key === 'ArrowDown' ? Math.min(rows.length - 1, active + 1) : Math.max(0, active - 1);
            [...list.children].forEach((item, index) => {
                item.classList.toggle('is-highlighted', index === active);
                item.setAttribute('aria-selected', String(index === active));
            });
            input.setAttribute('aria-activedescendant', `address-option-${active}`);
            list.children[active]?.scrollIntoView({block: 'nearest'});
        } else if (event.key === 'Escape' || event.key === 'Tab') close();
    });
    form.addEventListener('submit', event => {
        if (busy) { event.preventDefault(); status.textContent = 'Wait for address lookup to finish, then save.'; }
    });
    opener.addEventListener('click', () => {
        cancel();
        input.value = [street.value.trim(), form.querySelector('#city').value.trim(),
            form.querySelector('#postal_code').value.trim()].filter(Boolean).join(', ');
        dialog.showModal();
        input.focus();
    });
    dialog.querySelector('[data-close-address]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', cancel);
    opener.hidden = false;
})();
