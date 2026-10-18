import React,{useEffect,useState} from 'react'

function value(value, fallback='not returned'){return value===undefined||value===null||value===''?fallback:String(value)}

export default function LinuxOneStatusPanel({health,audit=[],apiBase,onRefresh}){
  apiBase=apiBase||window.xerxes?.apiBase||import.meta.env.VITE_API_BASE_URL||null
  const risk=health?.risk_service||{}
  const score=risk.last_score
  const healthy=risk.state==='ready'&&risk.health?.status==='ok'
  const policy=audit[0]?.authorization==='denied'?'Denied':audit[0]?.authorization==='allowed'?'Allowed':'Not evaluated'
  const [config,setConfig]=useState({ssh_host:'',ssh_user:'',ssh_key:'',remote_port:5001,local_port:5001}),[missing,setMissing]=useState([]),[editing,setEditing]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('')
  const configured=Boolean(risk.configured||risk.configuration?.configured)
  useEffect(()=>{if(!apiBase)return;fetch(`${apiBase}/linuxone/config`).then(r=>r.json()).then(data=>{setMissing(data.missing||[]);if(!data.configured)setEditing(true)}).catch(()=>{})},[apiBase])
  const request=async(path,options={})=>{setBusy(true);setError('');try{const response=await fetch(`${apiBase}${path}`,options);const data=await response.json().catch(()=>({}));if(!response.ok)throw Error(data.error?.message||'LinuxONE request failed');if(onRefresh)onRefresh();else window.location.reload();return data}catch(exc){setError(exc.message)}finally{setBusy(false)}}
  const save=event=>{event.preventDefault();request('/linuxone/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)}).then(data=>{if(data&&!data.error){setEditing(false);setMissing([])}})}
  const state=healthy?'Connected and healthy':risk.state==='unavailable'&&risk.tunnel==='ready'?'Remote service unhealthy':risk.state==='ready'?'Connected':risk.state==='configured'?'Configured but disconnected':configured?'Configured but disconnected':'Not configured'
  return <section className="linuxone-panel" aria-label="LinuxONE risk status">
    <h3>LinuxONE risk</h3>
    <div className="metric"><span>Connection</span><b className={healthy?'status-good':risk.state==='unavailable'?'status-error':'status-neutral'}>{state}</b></div>
    {!editing&&risk.state!=='ready'&&<button className="linuxone-action" onClick={()=>setEditing(true)}>Configure</button>}
    {editing&&<form className="linuxone-config" onSubmit={save}><label>SSH host<input value={config.ssh_host} onChange={e=>setConfig({...config,ssh_host:e.target.value})} required/></label><label>SSH username<input value={config.ssh_user} onChange={e=>setConfig({...config,ssh_user:e.target.value})} required/></label><label>External private-key path<input value={config.ssh_key} onChange={e=>setConfig({...config,ssh_key:e.target.value})} required/></label><label>Remote port<input type="number" min="1" max="65535" value={config.remote_port} onChange={e=>setConfig({...config,remote_port:e.target.value})}/></label><label>Local port<input type="number" min="1" max="65535" value={config.local_port} onChange={e=>setConfig({...config,local_port:e.target.value})}/></label>{missing.length>0&&<small>Missing: {missing.join(', ')}</small>}{error&&<small className="risk-error">{error}</small>}<button type="submit" disabled={busy}>Save configuration</button></form>}
    {!editing&&risk.state==='configured'&&<button className="linuxone-action" disabled={busy} onClick={()=>request('/linuxone/connect',{method:'POST'})}>Connect</button>}
    {!editing&&risk.state==='ready'&&<><button className="linuxone-action" disabled={busy} onClick={onRefresh}>Refresh status</button><button className="linuxone-action" disabled={busy} onClick={()=>request('/linuxone/disconnect',{method:'POST'})}>Disconnect</button></>}
    {error&&!editing&&<small className="risk-error">{error}</small>}
    <div className="runtime-line"><span>SSH tunnel</span><code>{value(risk.tunnel,'disabled')}</code></div>
    <div className="runtime-line"><span>Remote health</span><code>{value(risk.health?.status,'not verified')}</code></div>
    {risk.health_checked_at&&<div className="runtime-line"><span>Health checked</span><code>{risk.health_checked_at}</code></div>}
    {risk.error&&<small className="risk-error">{risk.error}</small>}
    <div className="runtime-line"><span>Architecture</span><code>{value(risk.architecture)}</code></div>
    <div className="runtime-line"><span>Model</span><code>{value(risk.health?.model_version||risk.model?.model_version)}</code></div>
    <span className="risk-experimental">EXPERIMENTAL MODEL · synthetic training</span>
    <div className="metric"><span>LinuxONE assessment</span><b>{score?`${score.category} · ${score.risk_score}`:'No score recorded yet'}</b></div>
    {score&&<><div className="runtime-line"><span>Latency</span><code>{value(score.latency_ms)} ms</code></div><div className="runtime-line"><span>Received</span><code>{value(score.received_at,'historical')}</code></div></>}
    <div className="metric"><span>Xerxes local policy</span><b>{policy}</b></div>
    <small className="risk-advisory">Advisory only; local policy remains authoritative.</small>
  </section>
}
