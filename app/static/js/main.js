const navToggle = document.getElementById("navToggle");
const navLinks = document.getElementById("navLinks");

if (navToggle && navLinks) {
    navToggle.addEventListener("click", () => {
        navLinks.classList.toggle("active");
    });
}

for (const ecosystem of document.querySelectorAll('.ecosystem-visual')) {
    const motionButton = ecosystem.querySelector('.ecosystem-motion');
    const diagram = ecosystem.querySelector('.ecosystem-system');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let paused = false;

    function syncMotion() {
        ecosystem.toggleAttribute('data-paused', paused || reducedMotion.matches);
        if (paused || reducedMotion.matches) {
            diagram.pauseAnimations();
        } else {
            diagram.unpauseAnimations();
        }
        motionButton.textContent = paused ? 'Play animation' : 'Pause animation';
    }

    motionButton.hidden = false;
    motionButton.addEventListener('click', () => {
        paused = !paused;
        syncMotion();
    });
    reducedMotion.addEventListener('change', syncMotion);
    syncMotion();
}
