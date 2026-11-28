(() => {
    const connectionForm = document.querySelector('[data-connection-form]');
    if (connectionForm) {
        const editButton = connectionForm.querySelector('[data-connection-edit]');
        const password = connectionForm.querySelector('[name="password"]');
        const startEditing = () => {
            connectionForm.dataset.editing = 'true';
            connectionForm.querySelectorAll('[data-connection-editable]').forEach(input => { input.readOnly = false; });
            editButton.textContent = 'Save';
            editButton.type = 'submit';
        };
        editButton.addEventListener('click', event => {
            if (connectionForm.dataset.editing === 'true') return;
            event.preventDefault();
            startEditing();
        });
        connectionForm.addEventListener('submit', event => {
            if (connectionForm.dataset.editing !== 'true') {
                event.preventDefault();
                startEditing();
            }
        });
        password.addEventListener('focus', () => {
            if (!password.readOnly) password.placeholder = '';
        });
        password.addEventListener('blur', () => {
            if (!password.value && password.dataset.savedPassword === 'true') password.placeholder = '*****';
        });
    }
    document.querySelectorAll('[data-auto-dismiss]').forEach(notice => {
        const timer = setTimeout(() => notice.remove(), Number(notice.dataset.autoDismiss));
        notice.querySelector('[data-dismiss-notice]')?.addEventListener('click', () => {
            clearTimeout(timer);
            notice.remove();
        });
    });
    const dialog = document.getElementById('commerce-access-dialog');
    if (dialog) {
        const form = document.getElementById('commerce-access-form');
        const picker = document.getElementById('commerce-client-picker');
        const clientSelect = document.getElementById('commerce-new-client');
        const clientName = document.getElementById('commerce-selected-client');
        const title = document.getElementById('commerce-access-title');
        const save = document.getElementById('commerce-access-save');
        const enabled = form.elements.namedItem('enabled');
        const featureInputs = [...form.querySelectorAll('[name="features"]')];
        const updateFeatures = () => { featureInputs.forEach(input => { input.disabled = !enabled.checked; }); };
        enabled.addEventListener('change', updateFeatures);
        const open = (button) => {
            form.reset();
            const adding = !button.hasAttribute('data-commerce-settings');
            title.textContent = adding ? 'Add client' : 'Client access';
            picker.hidden = !adding;
            clientSelect.disabled = !adding;
            clientSelect.required = adding;
            clientName.hidden = adding;
            clientName.textContent = adding ? '' : button.dataset.clientName;
            if (adding) {
                form.removeAttribute('action');
                save.disabled = true;
            } else {
                form.action = button.dataset.accessUrl;
                const features = JSON.parse(button.dataset.clientFeatures);
                featureInputs.forEach(input => { input.checked = features.includes(input.value); });
                save.disabled = false;
            }
            updateFeatures();
            dialog.showModal();
            (adding ? clientSelect : enabled).focus();
        };
        document.addEventListener('click', event => {
            const button = event.target.closest('[data-commerce-settings], [data-commerce-add]');
            if (button && !button.disabled) open(button);
        });
        clientSelect.addEventListener('change', () => {
            const action = clientSelect.selectedOptions[0]?.dataset.accessUrl;
            save.disabled = !action;
            if (action) form.action = action;
            else form.removeAttribute('action');
        });
        dialog.querySelectorAll('[data-commerce-close]').forEach(button => {
            button.addEventListener('click', () => dialog.close());
        });
    }
    const clientTable = document.querySelector('.commerce-client-table');
    if (clientTable) {
        clientTable.addEventListener('click', event => {
            const row = event.target.closest('[data-commerce-client-row]');
            if (!row) return;
            if (event.target.closest('a,button,input,select,label') || window.getSelection()?.toString()) return;
            row.querySelector('[data-commerce-client-link]').click();
        });
    }
    const searchForm = document.querySelector('[data-commerce-search]');
    const clientCache = document.querySelector('[data-commerce-all-clients]');
    if (searchForm && clientTable && clientCache) {
        const input = searchForm.querySelector('[name="q"]');
        const filter = searchForm.querySelector('[name="feature"]');
        const count = searchForm.querySelector('[data-commerce-client-count]');
        const clients = [...clientCache.content.querySelectorAll('[data-commerce-search-row]')].map(row => ({
            row,
            name: row.dataset.clientName.toLocaleLowerCase(),
            features: JSON.parse(row.dataset.clientFeatures),
        }));
        let urlTimer;
        const search = () => {
            const query = input.value.trim().toLocaleLowerCase();
            const matches = clients.filter(client => client.name.includes(query) &&
                (!filter.value || client.features.includes(filter.value)));
            const body = clientTable.tBodies[0];
            body.replaceChildren(...matches.map(client => client.row));
            if (!matches.length) {
                const cell = body.insertRow().insertCell();
                cell.colSpan = 3;
                cell.textContent = query || filter.value ? 'No clients match your search.' : 'No clients have Commerce Hub access yet.';
            }
            count.textContent = matches.length + (matches.length === 1 ? ' client' : ' clients');
            // Only URL bookkeeping is deferred; filtering above is synchronous.
            clearTimeout(urlTimer);
            urlTimer = setTimeout(() => {
                const url = new URL(searchForm.action);
                url.search = new URLSearchParams(new FormData(searchForm)).toString();
                history.replaceState(null, '', url);
            }, 250);
        };
        input.addEventListener('input', search);
        input.addEventListener('search', search); // Native search-field clear control.
        filter.addEventListener('change', search);
        searchForm.addEventListener('submit', event => { event.preventDefault(); search(); });
        search();
    }
    let pollTimer, polling=false, pollAgain=false;
    const poll=async () => {
        if (polling) { pollAgain=true; return; }
        polling=true;
        let pending=false;
        const rows=[...document.querySelectorAll('[data-commerce-job]')];
        for (const row of rows) {
            if (!row.dataset.commerceJob) continue;
            try {
                const response=await fetch(row.dataset.commerceJob,{credentials:'same-origin',headers:{Accept:'application/json'}});
                if (!response.ok) {
                    if ([401,403,404].includes(response.status)) delete row.dataset.commerceJob;
                    else pending=true;
                    continue;
                }
                const job=await response.json();
                if (!row.isConnected) continue;
                row.querySelector('[data-job-status]').textContent=job.status_label || job.status;
                row.querySelector('[data-job-progress]').textContent=job.progress+'%';
                const count=row.querySelector('[data-job-count]');
                if (count) count.textContent=`${job.completed_products || 0} / ${job.total_products || 0}`;
                const published=row.querySelector('[data-job-published]');
                if (published) published.textContent=job.published?'Yes':'No';
                row.querySelector('[data-job-progress]').title=(job.stage || '').replaceAll('_',' ');
                const stage=row.querySelector('[data-job-stage]');
                if (stage) stage.textContent=(job.stage || '').replaceAll('_',' ');
                const elapsed=row.querySelector('[data-job-elapsed]');
                if (elapsed) elapsed.textContent=`${Math.floor((job.elapsed_seconds || 0)/60)}m ${(job.elapsed_seconds || 0)%60}s`;
                const runDate=row.querySelector('[data-job-run-date]');
                if (runDate && job.run_date_display) {
                    runDate.textContent=job.run_date_display;
                    runDate.dateTime=job.run_date_iso;
                    runDate.title=`${job.run_date_kind} / ${job.run_timezone}`;
                }

                const jobLabel=row.querySelector('[data-job-label]');
                if (jobLabel && job.job_label) jobLabel.textContent=job.job_label;
                row.querySelector('[data-job-error]').textContent=job.error_message || (job.error_code || '').replaceAll('_',' ');
                const catalog=row.querySelector('[data-job-catalog]');
                if (catalog) catalog.hidden=!job.published;
                const inventoryPreview=row.querySelector('[data-job-inventory-preview]');
                if (inventoryPreview) inventoryPreview.hidden=!job.preview_available;
                const connectionSample=row.querySelector('[data-job-connection-sample]');
                if (connectionSample) connectionSample.hidden=!job.connection_sample_available;
                const cancel=row.querySelector('[data-job-cancel]');
                if (cancel) cancel.hidden=!['queued','running'].includes(job.status);
                const resume=row.querySelector('[data-job-resume]');
                if (resume) resume.hidden=!job.can_resume;
                if (['queued','running'].includes(job.status)) pending=true;
                else delete row.dataset.commerceJob;
            } catch (_) { pending=true; }
        }
        polling=false;
        if (pollAgain) { pollAgain=false;pollTimer=setTimeout(poll,0); }
        else if (pending) pollTimer=setTimeout(poll,5000);
    };
    document.addEventListener('commerce:jobs-updated',() => { clearTimeout(pollTimer);poll(); });
    poll();
})();
