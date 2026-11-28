/* One explicit confirmation before a login plus first-page sample test. */
(() => {
    const dialog=document.getElementById('commerce-connection-test-dialog');
    if (!dialog) return;
    let pending=null,approved=null;
    document.querySelectorAll('form[data-test-connection]').forEach(form=>{
        form.addEventListener('submit',event=>{
            if (approved===form) { approved=null;return; }
            event.preventDefault();
            if (dialog.open) return;
            pending={form,button:event.submitter};
            dialog.querySelector('[data-test-connection-type]').textContent=form.dataset.testConnection;
            dialog.querySelector('[data-test-inventory-note]').hidden=form.dataset.testConnection!=='inventory';
            dialog.showModal();
        });
    });
    dialog.querySelector('[data-test-cancel]').addEventListener('click',()=>dialog.close());
    dialog.addEventListener('close',()=>{pending=null;});
    dialog.querySelector('[data-test-confirm]').addEventListener('click',()=>{
        if (!dialog.open || !pending) return;
        const {form,button}=pending;pending=null;dialog.close();approved=form;
        try { form.requestSubmit(button||undefined); } finally { approved=null; }
    });
})();
