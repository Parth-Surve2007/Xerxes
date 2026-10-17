const mount = () => {
  const api = window.xerxes?.apiBase;
  const panel = document.querySelector('.context');
  if (!api || !panel || panel.querySelector('.grant-list')) return;
  fetch(`${api}/grants`).then(r => r.json()).then(data => {
    const list = document.createElement('div'); list.className = 'grant-list';
    (data.grants || []).forEach(grant => {
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
  }).catch(() => {});
};
new MutationObserver(mount).observe(document.documentElement, {childList:true, subtree:true});
setTimeout(mount, 150);
