// Run with Node: node tests/address_lookup_events.cjs
// Exercises the actual UI event handlers; fetch is mocked, so no Google calls occur.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
    constructor() {
        this.events = {};
        this.children = [];
        this.attributes = {};
        this.value = '';
        this.hidden = false;
        this.classList = {add() {}, toggle() {}};
    }
    addEventListener(name, handler) { (this.events[name] ||= []).push(handler); }
    fire(name, fields = {}) {
        const event = {target: this, preventDefault() { this.prevented = true; }, ...fields};
        for (const handler of this.events[name] || []) handler(event);
        return event;
    }
    setAttribute(key, value) { this.attributes[key] = value; }
    removeAttribute(key) { delete this.attributes[key]; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    focus() {}
    click() { this.fire('click'); }
    scrollIntoView() {}
}

const input = new Element();
const query = new Element();
const opener = new Element();
const dismiss = new Element();
const results = new Element();
const dialog = new Element();
dialog.showModal = () => { dialog.open = true; };
dialog.close = () => { dialog.open = false; dialog.fire('close'); };
dialog.querySelector = selector => ({'#address-search-query': query,
    '[data-address-results]': results, '[data-close-address]': dismiss})[selector];
const country = new Element();
country.value = 'US';
const city = new Element();
const postal = new Element();
const csrf = new Element();
csrf.value = 'test-csrf';
const button = new Element();
const status = new Element();
const provider = new Element();
const credits = new Element();
const form = new Element();
const container = new Element();
container.dataset = {addressUrl: '/portal/admin/address-suggestions'};
container.closest = () => form;
container.contains = () => true;
container.querySelector = selector => ({
    '[data-find-address]': button, '.address-status': status, '.address-card-credit': provider,
    '[data-address-attributions]': credits, '.address-search-dialog': dialog, '[data-open-address]': opener
})[selector];
form.querySelector = selector => ({'#address_line_1': input, '#country_code': country,
    '#city': city, '#postal_code': postal, '[name=csrf_token]': csrf})[selector];
let applied;
form.applyAddressLocation = async data => { applied = data; return true; };
const document = new Element();
document.querySelector = () => container;
document.createElement = () => new Element();
const requests = [];
const fetch = async (_url, options) => {
    requests.push(Object.fromEntries(options.body));
    return {ok: true, json: async () => options.body.get('action') === 'search'
        ? [{label: '123 Main St, Austin, TX', selection: 'server-choice'}]
        : {address_line_1: '123 Main St', city: 'Austin', postal_code: '78701',
           country_code: 'US', state_region_code: 'US-TX', timezone: 'America/Chicago', attributions: []}};
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../app/static/js/address-autocomplete.js'), 'utf8'),
                   {document, fetch, URLSearchParams, AbortController});
const settle = () => new Promise(resolve => setImmediate(resolve));

(async () => {
    assert.equal(requests.length, 0, 'opening form makes zero requests');
    for (const text of ['1', '12', '123', '123 Main', '123 Main Austin']) {
        input.value = text;
        input.fire('input');
    }
    city.value = 'Austin'; city.fire('input');
    postal.value = '78701'; postal.fire('input');
    await settle();
    assert.equal(requests.length, 0, 'typing/pasting/editing makes zero requests');
    opener.click();
    assert.equal(dialog.open, true, 'heading icon opens search dialog');
    assert.equal(requests.length, 0, 'opening dialog does not search');
    dismiss.click();
    assert.equal(dialog.open, false, 'cross closes dialog');
    assert.equal(input.value, '123 Main Austin', 'closing dialog preserves manual address');
    opener.click();
    query.value = '123 Main Austin'; query.fire('input');
    assert.equal(requests.length, 0, 'typing in dialog does not search');
    button.fire('click');
    button.fire('click');
    assert.equal(button.disabled, true, 'Find button disabled while pending');
    assert.equal(form.fire('submit').prevented, true, 'cannot save during pending lookup');
    await settle();
    assert.equal(requests.length, 1, 'double click makes one search request');
    assert.equal(requests[0].action, 'search');
    assert.equal(requests[0].csrf_token, 'test-csrf');
    const list = results.children[0];
    assert.equal(list.hidden, false);
    list.children[0].fire('click');
    await settle();
    assert.equal(requests.length, 2, 'one selected result adds exactly one details request');
    assert.equal(requests[1].selection, 'server-choice');
    assert.equal(input.value, '123 Main St');
    assert.equal(city.value, 'Austin');
    assert.equal(postal.value, '78701');
    assert.equal(applied.state_region_code, 'US-TX');
    assert.equal(applied.timezone, 'America/Chicago', 'timezone is passed to the searchable dropdown helper');
    assert.equal(dialog.open, false, 'selection fills card and automatically closes dialog');
    input.value = 'Other street'; input.fire('input');
    country.fire('change');
    form.fire('submit');
    await settle();
    assert.equal(requests.length, 2, 'editing and saving add no Google lookups');
    console.log('PASS: UI events make zero requests while typing, one search per click, one details request per selection.');
})().catch(error => { console.error(error); process.exitCode = 1; });
