import logo from './xerxes_logo.png';
const fixHomeLogo = () => { const image=document.querySelector('.hero-logo'); if(image) image.src=logo; };
let loading = false;
const mount = () => {
  fixHomeLogo();
  const api = window.xerxes?.apiBase;
  const panel = document.querySelector('.context');
  if (!api || !panel || loading) return;
  loading = true;
  const existing = panel.querySelector('.grant-list'); if (existing) existing.remove();
  const oldHeading = panel.querySelector('.grants-heading'); if (oldHeading) oldHeading.remove();
  fetch(`${api}/grants`).then(r => { if (!r.ok) throw new Error(`Grant refresh failed (${r.status})`); return r.json(); }).then(data => {
    const list = document.createElement('div'); list.className = 'grant-list';
    const heading = document.createElement('h3'); heading.className = 'grants-heading'; heading.textContent = 'Authorized locations'; panel.append(heading);
    const unique = new Map(); (data.grants || []).forEach(grant => unique.set(String(grant.path).toLowerCase(), grant)); unique.forEach(grant => {
      const row = document.createElement('div'); row.className = 'grant-row';
      const label = document.createElement('code'); label.textContent = grant.path;
      const button = document.createElement('button'); button.textContent = 'Revoke';
      button.onclick = async () => {
        if (!window.confirm(`Revoke folder access?\n\n${grant.path}`)) return;
        button.disabled = true;
        try { await window.xerxes.revokeFolder(grant.id); row.remove(); }
        catch (error) { button.disabled = false; window.dispatchEvent(new CustomEvent('xerxes-grant-error', {detail:error.message})); }
      };
      row.append(label, button); list.append(row);
    });
    if (list.children.length) panel.append(list);
  }).catch(error => { window.dispatchEvent(new CustomEvent('xerxes-grant-error', {detail:error.message})); }).finally(() => { loading = false; });
};
window.addEventListener('xerxes-grant-refresh', () => setTimeout(mount, 100));
new MutationObserver(() => { if (!document.querySelector('.grant-list')) mount(); }).observe(document.documentElement, {childList:true, subtree:true});
setTimeout(mount, 150);
