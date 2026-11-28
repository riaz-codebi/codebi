(() => {
    const dialog=document.getElementById('commerce-product-dialog');
    if (!dialog) return;
    const body=dialog.querySelector('[data-product-dialog-body]');
    let controller, sequence=0;
    const message=(text,role='status') => {
        const p=document.createElement('p');p.textContent=text;p.setAttribute('role',role);
        body.replaceChildren(p);
    };
    const initialize=() => {
        const gallery=body.querySelector('[data-product-gallery]');
        const images=[...body.querySelectorAll('[data-gallery-index]')];
        if (gallery && images.length) {
            let index=0;
            const hero=gallery.querySelector('[data-gallery-image]');
            const failure=gallery.querySelector('[data-gallery-error]');
            hero.addEventListener('error',() => {hero.hidden=true;failure.hidden=false;});
            const show=(next) => {
                index=(next+images.length)%images.length;
                hero.hidden=false;failure.hidden=true;
                hero.src=images[index].dataset.imageUrl;
                hero.alt=images[index].dataset.imageAlt;
                gallery.querySelector('[data-gallery-count]').textContent=`${index+1} / ${images.length}`;
                images.forEach((button,i) => button.setAttribute('aria-pressed',String(i===index)));
            };
            images.forEach((button,i) => button.addEventListener('click',() => show(i)));
            gallery.querySelector('[data-gallery-prev]').addEventListener('click',() => show(index-1));
            gallery.querySelector('[data-gallery-next]').addEventListener('click',() => show(index+1));
            gallery.querySelector('[data-gallery-stage]').addEventListener('keydown',event => {
                if (!['ArrowLeft','ArrowRight'].includes(event.key)) return;
                event.preventDefault();show(index+(event.key==='ArrowRight'?1:-1));
            });
        }
        const tabs=[...body.querySelectorAll('[data-product-tab]')];
        const panels=[...body.querySelectorAll('[data-product-panel]')];
        const select=(index,focus=false) => {
            tabs.forEach((tab,i) => {tab.setAttribute('aria-selected',String(i===index));tab.tabIndex=i===index?0:-1;});
            panels.forEach((panel,i) => panel.hidden=i!==index);
            if (focus) tabs[index].focus();
        };
        tabs.forEach((tab,index) => {
            tab.addEventListener('click',() => select(index));
            tab.addEventListener('keydown',event => {
                if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
                event.preventDefault();
                const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
                select(next,true);
            });
        });
    };
    document.addEventListener('click',async event => {
        const button=event.target.closest('[data-product-view]');
        if (!button) return;
        event.preventDefault();
        controller?.abort();controller=new AbortController();
        const current=++sequence;
        if (!dialog.open) dialog.showModal();
        body.setAttribute('aria-busy','true');message('Loading product details...');
        try {
            const url=new URL(button.dataset.productUrl,location.href);
            if (url.origin!==location.origin) throw new Error('Invalid product URL');
            const response=await fetch(url,{credentials:'same-origin',headers:{Accept:'text/html'},signal:controller.signal});
            if (!response.ok) throw new Error('Product unavailable');
            const text=await response.text();
            if (current!==sequence || !dialog.open) return;
            const content=new DOMParser().parseFromString(text,'text/html').querySelector('[data-product-detail]');
            if (!content) throw new Error('Unexpected product response');
            body.replaceChildren(document.importNode(content,true));
            dialog.scrollTop=0;initialize();
        } catch (error) {
            if (current===sequence && error.name!=='AbortError') message('Product details could not be loaded. Please try again.','alert');
        } finally {
            if (current===sequence) body.removeAttribute('aria-busy');
        }
    });
    const reset=() => {controller?.abort();sequence++;body.replaceChildren();body.removeAttribute('aria-busy');};
    const close=() => {reset();dialog.close();};
    dialog.querySelector('[data-product-close]').addEventListener('click',close);
    // A delayed close event from the previous product must not clear a reopened dialog.
    dialog.addEventListener('close',() => {if (!dialog.open) reset();});
    dialog.addEventListener('click',event => {
        if (event.target!==dialog) return;
        const box=dialog.getBoundingClientRect();
        if (event.clientX<box.left || event.clientX>box.right || event.clientY<box.top || event.clientY>box.bottom) close();
    });
})();
