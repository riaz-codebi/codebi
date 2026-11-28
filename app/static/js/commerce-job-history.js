/* Filter the complete authorized history while keeping controls and focus in place. */
(() => {
    const form=document.querySelector('[data-job-filters]');
    const panel=document.querySelector('[data-commerce-jobs-panel]');
    if (!form || !panel) return;
    const search=form.querySelector('[name="job_search"]');
    const error=form.querySelector('[data-job-filter-error]');
    let controller, timer, sequence=0;
    const load=async (page=1) => {
        clearTimeout(timer);
        controller?.abort();
        controller=new AbortController();
        const current=++sequence;
        const params=new URLSearchParams(new FormData(form));
        if (page>1) params.set('job_page',String(page));
        const target=new URL(form.dataset.jobsUrl,location.href);
        target.search=params.toString();
        error.hidden=true;
        panel.setAttribute('aria-busy','true');
        try {
            const response=await fetch(target,{credentials:'same-origin',headers:{Accept:'text/html'},signal:controller.signal});
            if (!response.ok) throw new Error('Jobs unavailable');
            const html=await response.text();
            if (current!==sequence) return;
            const fragment=new DOMParser().parseFromString(html,'text/html').querySelector('[data-job-history-content]');
            if (!fragment) throw new Error('Unexpected jobs response');
            panel.replaceChildren(document.importNode(fragment,true));
            const url=new URL(form.action,location.href);
            url.search=params.toString();url.hash='jobs';
            history.replaceState(null,'',url);
            document.dispatchEvent(new Event('commerce:jobs-updated'));
        } catch (failure) {
            if (current===sequence && failure.name!=='AbortError') error.hidden=false;
        } finally {
            if (current===sequence) panel.removeAttribute('aria-busy');
        }
    };
    search.addEventListener('input',() => { clearTimeout(timer);timer=setTimeout(() => load(),150); });
    search.addEventListener('search',() => load());
    form.addEventListener('change',event => {
        if (event.target.name!=='job_period') return;
        load();
    });
    form.addEventListener('submit',event => { event.preventDefault();load(); });
    panel.addEventListener('click',event => {
        const link=event.target.closest('[data-job-page]');
        if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button!==0) return;
        event.preventDefault();
        load(Number(new URL(link.href).searchParams.get('job_page')) || 1);
    });
})();
