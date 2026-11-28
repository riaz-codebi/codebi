/* Ask before queueing a resource-intensive extraction; never intercept login tests. */
(() => {
    const dialog=document.getElementById('commerce-extraction-dialog');
    if (!dialog) return;
    const confirm=dialog.querySelector('[data-extraction-confirm]');
    const scope=dialog.querySelector('[data-extraction-scope]');
    const inventoryNote=dialog.querySelector('[data-extraction-inventory-note]');
    let pending=null, approved=null;
    document.querySelectorAll('form[data-extraction-kind]').forEach(form => {
        form.addEventListener('submit',event => {
            if (approved===form) { approved=null; return; }
            event.preventDefault();
            if (dialog.open) return;
            pending={form,button:event.submitter};
            if (inventoryNote) inventoryNote.hidden=form.dataset.extractionKind!=='inventory';
            const mode=form.querySelector('[name="catalog_mode"]');
            const category=form.querySelector('[name="category_id"]');
            scope.textContent=form.dataset.extractionKind==='catalog'
                ? (mode?.value==='category' ? 'catalog data for '+category.selectedOptions[0].textContent.trim() : 'catalog data for all products')
                : 'inventory data';
            confirm.disabled=false;
            dialog.showModal();
        });
    });
    dialog.querySelector('[data-extraction-cancel]').addEventListener('click',() => dialog.close());
    dialog.addEventListener('close',() => { if (!dialog.open) pending=null; });
    confirm.addEventListener('click',() => {
        if (!dialog.open || !pending || confirm.disabled) return;
        const {form,button}=pending;
        pending=null;
        confirm.disabled=true;
        dialog.close();
        approved=form;
        try { form.requestSubmit(button || undefined); }
        finally { approved=null; }
    });
})();
