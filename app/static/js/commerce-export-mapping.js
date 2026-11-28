(() => {
    const samplePanel=document.querySelector('[data-mapping-sample]');
    const sampleValues=samplePanel?.querySelector('[data-sample-values]');
    const highlight=input=>{
        if (!sampleValues || !input?.matches('[data-mapping-value]')) return;
        const key=(input.dataset.mappingValue==='automatic'?'auto.':'')+input.value;
        let selected;
        sampleValues.querySelectorAll('[data-sample-key]').forEach(row=>{
            const active=input.dataset.mappingValue!=='constant' && row.dataset.sampleKey===key;
            row.classList.toggle('sample-field-selected',active);
            if (active) selected=row;
        });
        if (selected) {
            const viewport=sampleValues.getBoundingClientRect(),item=selected.getBoundingClientRect();
            if (item.top<viewport.top) sampleValues.scrollTop-=viewport.top-item.top;
            else if (item.bottom>viewport.bottom) sampleValues.scrollTop+=Math.min(item.bottom-viewport.bottom,item.top-viewport.top);
        }
    };
    document.querySelectorAll('[data-mapping-row]').forEach(row => {
        const include=row.querySelector('input[type=checkbox]');
        const mode=row.querySelector('[data-mapping-mode]');
        const sync=()=>{
            row.classList.toggle('mapping-excluded',!include.checked);
            mode.disabled=!include.checked;
            row.querySelectorAll('[data-mapping-value]').forEach(input=>{
                input.hidden=input.dataset.mappingValue!==mode.value;
                input.disabled=!include.checked || input.hidden;
            });
        };
        include.addEventListener('change',sync);mode.addEventListener('change',sync);sync();
        row.addEventListener('focusin',event=>highlight(event.target));
        row.addEventListener('change',event=>highlight(event.target));
    });
    if (samplePanel) {
        const defaultsDialog=document.getElementById('mapping-default-values-dialog');
        const populateDefaults=()=>{
            const content=sampleValues.querySelector('[data-default-values-content]');
            if (!content) return false;
            defaultsDialog.querySelector('[data-default-values-body]').replaceChildren(document.importNode(content.content,true));
            return true;
        };
        samplePanel.querySelector('[data-view-default-values]').addEventListener('click',()=>{
            if (populateDefaults() && !defaultsDialog.open) defaultsDialog.showModal();
        });
        defaultsDialog.querySelector('[data-close-default-values]').addEventListener('click',()=>defaultsDialog.close());
        const selector=samplePanel.querySelector('[data-sample-selector]');
        const input=selector.querySelector('input');
        const error=samplePanel.querySelector('[data-sample-error]');
        let controller,sequence=0;
        selector.addEventListener('submit',async event=>{
            event.preventDefault();controller?.abort();controller=new AbortController();
            const current=++sequence;
            const url=new URL(selector.dataset.sampleUrl,location.href);
            url.searchParams.set('product_id',input.value.trim());
            error.hidden=true;sampleValues.setAttribute('aria-busy','true');
            try {
                const response=await fetch(url,{credentials:'same-origin',signal:controller.signal,headers:{Accept:'text/html'}});
                if (!response.ok) throw new Error(response.status===404?'Product ID was not found in the published catalog.':'Could not load the sample. Try again.');
                const html=await response.text();
                if (current!==sequence) return;
                const content=new DOMParser().parseFromString(html,'text/html').querySelector('[data-mapping-sample-content]');
                if (!content) throw new Error('Could not load the sample. Try again.');
                sampleValues.replaceChildren(document.importNode(content,true));sampleValues.scrollTop=0;
                if (defaultsDialog.open) populateDefaults();
                document.querySelector('[data-selected-sample]').value=content.dataset.sampleProductId;
                input.value=content.dataset.sampleProductId;
                const page=new URL(location.href);page.searchParams.set('sample_product_id',input.value);
                history.replaceState(null,'',page);
            } catch (failure) {
                if (current===sequence && failure.name!=='AbortError') {error.textContent=failure.message;error.hidden=false;}
            } finally {if (current===sequence) sampleValues.removeAttribute('aria-busy');}
        });
    }
    const dialog=document.getElementById('export-destination-dialog');
    if (!dialog) return;
    const form=dialog.querySelector('form'),choice=form.querySelector('select');
    const sync=()=>form.querySelector('[data-destination-vendor]').value=choice.selectedOptions[0]?.dataset.vendor || '';
    choice.addEventListener('change',sync);sync();
    document.querySelector('[data-open-export-destination]').addEventListener('click',()=>dialog.showModal());
    dialog.querySelector('[data-close-export-destination]').addEventListener('click',()=>dialog.close());
})();
