/* Preserve old bookmarked section anchors after moving to separate tab views. */
(() => {
    const home=document.querySelector('[data-commerce-home]');
    if (!home) return;
    const url=new URL(location.href);
    const legacy=url.hash.slice(1);
    if (!url.searchParams.has('tab') && ['connections','jobs','exports'].includes(legacy) && legacy!==home.dataset.activeTab) {
        url.searchParams.set('tab',legacy);
        location.replace(url);
    }
})();
