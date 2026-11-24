const estimateForm = document.querySelector('[data-estimate-form]');
if (estimateForm) {
    const pricingType = estimateForm.elements.pricing_type;
    const pricingFields = estimateForm.querySelectorAll('[data-pricing]');
    const total = estimateForm.querySelector('#estimated-total');
    const fixedAmount = estimateForm.elements.project_amount;
    const dollars = new Intl.NumberFormat('en-US');

    function formatFixedAmount() {
        const value = fixedAmount.value.trim();
        if (!/^(?:\d+|\d{1,3}(?:,\d{3})+)$/.test(value)) return;
        const amount = Number(value.replaceAll(',', ''));
        if (amount < 1 || amount > 99999999) return;
        fixedAmount.value = dollars.format(amount);
    }

    function hundredths(input) {
        const parts = input.value.match(/^(\d*)(?:\.(\d{0,2}))?$/);
        if (!parts || !input.validity.valid) return null;
        return BigInt(parts[1] || '0') * 100n + BigInt((parts[2] || '').padEnd(2, '0'));
    }

    function price(hours, rate) {
        const cents = (hours * rate + 50n) / 100n;
        return '$' + dollars.format((cents + 50n) / 100n);
    }

    function updateTotal() {
        const min = hundredths(estimateForm.elements.min_hours);
        const max = hundredths(estimateForm.elements.max_hours);
        const rate = hundredths(estimateForm.elements.hourly_rate);
        total.value = min && rate && max >= min
            ? (max === min ? price(min, rate) : price(min, rate) + ' – ' + price(max, rate))
            : '—';
    }

    function updatePricing() {
        for (const group of pricingFields) {
            const active = group.dataset.pricing === pricingType.value;
            group.hidden = !active;
            group.disabled = !active;
            for (const input of group.querySelectorAll('input')) input.required = active;
        }
        if (pricingType.value === 'hourly') updateTotal();
    }

    for (const option of pricingType) option.addEventListener('change', updatePricing);
    fixedAmount.addEventListener('blur', formatFixedAmount);
    formatFixedAmount();
    estimateForm.addEventListener('input', () => {
        if (pricingType.value === 'hourly') updateTotal();
    });
    updatePricing();
}
