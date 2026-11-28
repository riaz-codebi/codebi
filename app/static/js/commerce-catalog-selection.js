/* Category choices are local; typing does not call the vendor. */
(() => {
    document.querySelectorAll('[data-catalog-selection]').forEach(form => {
        const mode = form.querySelector('[name="catalog_mode"]');
        const field = form.querySelector('[data-catalog-category]');
        const category = field.querySelector('select');
        const sync = () => {
            const selected = mode.value === 'category';
            field.hidden = !selected;
            category.disabled = !selected;
            category.required = selected;
            if (!selected) category.value = '';
            category.dispatchEvent(new Event('change', {bubbles: true}));
            const input = field.querySelector('[role="combobox"]');
            if (input) input.setAttribute('aria-required', String(selected));
        };
        mode.addEventListener('change', sync);
        sync();
    });
})();
