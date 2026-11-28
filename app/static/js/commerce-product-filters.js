/* Filter the published catalog on the server, including products beyond page one. */
(() => {
    const form=document.querySelector('[data-product-filters]');
    const panel=document.querySelector('[data-product-results]');
    if (!form || !panel) return;
    const error=form.querySelector('[data-product-filter-error]');
    let controller,timer,sequence=0;
    const load=async page => {
        clearTimeout(timer);controller?.abort();controller=new AbortController();
        const current=++sequence;
        const url=new URL(form.action,location.href);
        url.search=new URLSearchParams(new FormData(form)).toString();
        if (page>1) url.searchParams.set('page',page);
        error.hidden=true;panel.setAttribute('aria-busy','true');
        try {
            const response=await fetch(url,{credentials:'same-origin',signal:controller.signal,headers:{Accept:'text/html'}});
            if (!response.ok) throw new Error('Products unavailable');
            const html=await response.text();
            if (current!==sequence) return;
            const content=new DOMParser().parseFromString(html,'text/html').querySelector('[data-product-results]');
            if (!content) throw new Error('Unexpected product response');
            panel.replaceChildren(...Array.from(content.childNodes,node=>document.importNode(node,true)));
            history.replaceState(null,'',url);
        } catch (failure) {
            if (current===sequence && failure.name!=='AbortError') error.hidden=false;
        } finally { if (current===sequence) panel.removeAttribute('aria-busy'); }
    };
    form.querySelector('[name=q]').addEventListener('input',() => {clearTimeout(timer);timer=setTimeout(()=>load(1),150);});
    form.querySelector('[name=q]').addEventListener('search',()=>load(1));
    form.addEventListener('change',event=>{if(event.target.name==='category') load(1);});
    form.addEventListener('submit',event=>{event.preventDefault();load(1);});
    panel.addEventListener('click',event=>{
        const link=event.target.closest('.table-pagination a');
        if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button!==0) return;
        event.preventDefault();load(Number(new URL(link.href).searchParams.get('page')) || 1);
    });
})();
