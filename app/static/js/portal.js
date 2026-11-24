const portalToggle = document.getElementById('portalMenuToggle');
const portalSidebar = document.getElementById('portalSidebar');
const portalLayout = document.getElementById('portalLayout');
const portalAccount = document.getElementById('portalAccount');
if (portalToggle && portalSidebar && portalLayout) {
    const setNavigation = (expanded) => {
        portalSidebar.hidden = !expanded;
        portalToggle.setAttribute('aria-expanded', String(expanded));
        portalLayout.classList.toggle('nav-collapsed', !expanded);
    };
    setNavigation(!window.matchMedia('(max-width: 800px)').matches);
    portalToggle.addEventListener('click', () => setNavigation(portalToggle.getAttribute('aria-expanded') !== 'true'));
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !portalAccount?.open && !document.querySelector('dialog[open]') && window.matchMedia('(max-width: 800px)').matches) {
            setNavigation(false);
            portalToggle.focus();
        }
    });
}

if (portalAccount) {
    document.addEventListener('click', (event) => {
        if (!portalAccount.contains(event.target)) portalAccount.open = false;
    });
    portalAccount.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || !portalAccount.open) return;
        event.stopPropagation();
        portalAccount.open = false;
        portalAccount.querySelector('summary').focus();
    });
}

for (const button of document.querySelectorAll('[data-scope-dialog]')) {
    button.addEventListener('click', () => {
        document.getElementById(button.dataset.scopeDialog).showModal();
    });
}
