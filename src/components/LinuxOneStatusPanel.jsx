import React from 'react'

function value(value, fallback='not returned'){return value===undefined||value===null||value===''?fallback:String(value)}

export default function LinuxOneStatusPanel({health,audit=[]}){
  const risk=health?.risk_service||{}
  const score=risk.last_score
  const healthy=risk.state==='ready'&&risk.health?.status==='ok'
  const policy=audit[0]?.authorization==='denied'?'Denied':audit[0]?.authorization==='allowed'?'Allowed':'Not evaluated'
  return <section className="linuxone-panel" aria-label="LinuxONE risk status">
    <h3>LinuxONE risk</h3>
    <div className="metric"><span>Connection</span><b className={healthy?'status-good':risk.state==='unavailable'?'status-error':'status-neutral'}>{healthy?'CONNECTED':value(risk.state,'DISABLED').toUpperCase()}</b></div>
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
