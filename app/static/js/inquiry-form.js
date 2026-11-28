(() => {
    const details = document.querySelector('#inquiry-details[data-min-words]');
    if (!details) return;
    const error = document.getElementById('inquiry-details-error');
    const minimum = Number(details.dataset.minWords);
    const message = `Please provide at least ${minimum} words describing the solution you need, your goals and the challenges you want to solve.`;
    let showError = details.getAttribute('aria-invalid') === 'true';
    function validate(event) {
        const words = details.value.match(/\p{L}[\p{L}\p{N}]*(?:['’\-][\p{L}\p{N}]+)*/gu) || [];
        const short = words.length < minimum;
        details.setCustomValidity(short ? message : '');
        if (showError && event) {
            error.textContent = short ? message : '';
            error.hidden = !short;
            if (short) {
                details.setAttribute('aria-invalid', 'true');
                details.setAttribute('aria-describedby', error.id);
            } else {
                details.removeAttribute('aria-invalid');
                details.removeAttribute('aria-describedby');
            }
        }
    }
    details.addEventListener('input', validate);
    details.addEventListener('invalid', event => { showError = true; validate(event); });
    // Server validation remains mandatory when JavaScript is disabled/bypassed.
    validate();
})();
