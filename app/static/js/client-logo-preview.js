const logoPreview = document.getElementById('client-logo-preview');
const logoInput = document.getElementById('client-logo');
const background = document.getElementById('logo-background');
const previewImage = logoPreview.querySelector('img');
const placeholder = logoPreview.querySelector('span');
const savedLogo = previewImage.getAttribute('src');
let localLogo;

function showUnavailablePreview() {
    previewImage.hidden = true;
    placeholder.hidden = false;
    placeholder.textContent = localLogo ? 'This file could not be previewed.' : 'Saved logo unavailable';
}

previewImage.addEventListener('error', showUnavailablePreview);
if (savedLogo && previewImage.complete && !previewImage.naturalWidth) showUnavailablePreview();

background.addEventListener('change', () => {
    logoPreview.dataset.background = background.value;
});

logoInput.addEventListener('change', () => {
    const file = logoInput.files[0];
    if (localLogo) URL.revokeObjectURL(localLogo);
    localLogo = file ? URL.createObjectURL(file) : null;
    const source = localLogo || savedLogo;
    if (source) {
        previewImage.src = source;
    } else {
        previewImage.removeAttribute('src');
    }
    previewImage.hidden = !source;
    placeholder.hidden = Boolean(source);
    placeholder.textContent = 'Logo preview';
});
