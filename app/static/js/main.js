const navToggle = document.getElementById("navToggle");
const navLinks = document.getElementById("navLinks");

if (navToggle && navLinks) {
    navToggle.addEventListener("click", () => {
        navLinks.classList.toggle("active");
    });
}

const heroFlyer = document.getElementById("heroFlyer");
const flyerModal = document.getElementById("flyerModal");
const flyerModalClose = document.getElementById("flyerModalClose");

if (heroFlyer && flyerModal && flyerModalClose) {
    const openModal = () => {
        flyerModal.classList.add("active");
        flyerModal.setAttribute("aria-hidden", "false");
    };

    const closeModal = () => {
        flyerModal.classList.remove("active");
        flyerModal.setAttribute("aria-hidden", "true");
    };

    heroFlyer.addEventListener("click", openModal);
    flyerModalClose.addEventListener("click", closeModal);

    flyerModal.addEventListener("click", (event) => {
        if (event.target === flyerModal) {
            closeModal();
        }
    });
}