import { useState, useEffect, useCallback } from 'react';
import { db } from './services/firebase';
import { fetchHealth, fetchAlerts, acknowledgeAlert } from './services/api';
import {
  collection, query, orderBy, limit,
  onSnapshot, getCountFromServer
} from 'firebase/firestore';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer
} from 'recharts';
import { format, formatDistanceToNow } from 'date-fns';

const CHART_WINDOW     = 50;
const REFRESH_INTERVAL = 30000;

const S = {
  app:        { minHeight:'100vh', background:'#f8fafc', fontFamily:'-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif', color:'#1e293b' },
  header:     { background:'#fff', borderBottom:'1px solid #e2e8f0', padding:'12px 24px', display:'flex', alignItems:'center', justifyContent:'space-between', position:'sticky', top:0, zIndex:10, boxShadow:'0 1px 4px rgba(0,0,0,0.06)' },
  headerLeft: { display:'flex', alignItems:'center', gap:12 },
  logo:       { width:38, height:38, background:'#2563eb', borderRadius:10, display:'flex', alignItems:'center', justifyContent:'center', color:'#fff', fontWeight:'bold', fontSize:18 },
  headerTitle:{ margin:0, fontSize:15, fontWeight:700 },
  headerSub:  { margin:0, fontSize:11, color:'#94a3b8' },
  headerRight:{ display:'flex', alignItems:'center', gap:12 },
  tabs:       { background:'#fff', borderBottom:'1px solid #e2e8f0', padding:'0 24px', display:'flex', gap:0 },
  tab:        (active) => ({ padding:'12px 20px', fontSize:13, fontWeight:500, cursor:'pointer', border:'none', background:'none', borderBottom: active ? '2px solid #2563eb' : '2px solid transparent', color: active ? '#2563eb' : '#64748b' }),
  main:       { maxWidth:1100, margin:'0 auto', padding:'24px', display:'flex', flexDirection:'column', gap:24 },
  row:        { display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:16 },
  row2:       { display:'grid', gridTemplateColumns:'1fr 1fr', gap:16 },
  card:       (fault) => ({ background:'#fff', borderRadius:16, padding:20, boxShadow:'0 1px 4px rgba(0,0,0,0.07)', border: fault ? '1.5px solid #ef4444' : '1px solid #e2e8f0' }),
  cardLabel:  { fontSize:12, color:'#64748b', fontWeight:500, marginBottom:8, display:'flex', justifyContent:'space-between', alignItems:'center' },
  cardValue:  { fontSize:32, fontWeight:700, color:'#1e293b' },
  cardUnit:   { fontSize:13, color:'#94a3b8', marginLeft:4 },
  fault:      { fontSize:11, color:'#ef4444', fontWeight:600, marginTop:4 },
  badge:      (color) => {
    const map = { green:['#dcfce7','#166534'], red:['#fee2e2','#991b1b'], yellow:['#fef9c3','#854d0e'], gray:['#f1f5f9','#475569'], blue:['#dbeafe','#1e40af'] };
    const [bg, text] = map[color] || map.gray;
    return { background:bg, color:text, fontSize:11, fontWeight:600, padding:'2px 10px', borderRadius:20 };
  },
  progressWrap: { background:'#fff', borderRadius:16, padding:20, boxShadow:'0 1px 4px rgba(0,0,0,0.07)', border:'1px solid #e2e8f0' },
  progressBar:  { background:'#e2e8f0', borderRadius:99, height:12, overflow:'hidden', margin:'12px 0 4px' },
  progressFill: (pct) => ({ height:'100%', width:`${pct}%`, background:'#2563eb', borderRadius:99, transition:'width 0.5s' }),
  statusRow:  { display:'flex', justifyContent:'space-between', alignItems:'center', padding:'12px 0', borderBottom:'1px solid #f1f5f9' },
  statusLeft: { display:'flex', alignItems:'center', gap:10 },
  dot:        (color) => { const map={green:'#22c55e',red:'#ef4444',yellow:'#eab308',gray:'#94a3b8',blue:'#3b82f6'}; return { width:10, height:10, borderRadius:'50%', background:map[color]||map.gray, flexShrink:0 }; },
  detailRow:  { display:'flex', justifyContent:'space-between', padding:'8px 0', borderBottom:'1px solid #f8fafc', fontSize:13 },
  detailKey:  { color:'#64748b' },
  detailVal:  { fontWeight:500, maxWidth:200, textAlign:'right', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' },
  alertCard:  (s) => { const map={critical:'#ef4444',medium:'#eab308',low:'#3b82f6'}; return { background:'#fff', borderRadius:12, padding:16, borderLeft:`4px solid ${map[s]||'#94a3b8'}`, boxShadow:'0 1px 4px rgba(0,0,0,0.06)', marginBottom:12 }; },
  ackBtn:     { fontSize:12, border:'1px solid #e2e8f0', borderRadius:8, padding:'4px 12px', cursor:'pointer', background:'#fff', fontWeight:500 },
  empty:      { textAlign:'center', padding:'48px 0', color:'#94a3b8' },
  sectionTitle: { fontSize:13, fontWeight:600, color:'#64748b', textTransform:'uppercase', letterSpacing:'0.05em', marginBottom:12 },
  readyBanner: { background:'#dcfce7', border:'1px solid #86efac', borderRadius:12, padding:'12px 16px', color:'#166534', fontSize:13, fontWeight:600, marginTop:8 },
  footer:     { textAlign:'center', padding:'24px', fontSize:12, color:'#94a3b8' },
  refreshBtn: { background:'none', border:'1px solid #e2e8f0', borderRadius:8, padding:'6px 10px', cursor:'pointer', fontSize:12, color:'#64748b' },
  noModel:    { textAlign:'center', padding:32, color:'#94a3b8', fontSize:13 }
};

function StatCard({ label, value, unit, color, fault, icon }) {
  const colors = { orange:'#f97316', blue:'#3b82f6', purple:'#8b5cf6', teal:'#14b8a6' };
  return (
    <div style={S.card(fault)}>
      <div style={S.cardLabel}>
        <span>{label}</span>
        <span style={{ color: colors[color], fontSize:18 }}>{icon}</span>
      </div>
      <div>
        <span style={S.cardValue}>{value ?? '—'}</span>
        <span style={S.cardUnit}>{unit}</span>
      </div>
      {fault && <div style={S.fault}>⚠ Sensor Fault</div>}
    </div>
  );
}

function StatusRow({ label, status, detail, color }) {
  return (
    <div style={S.statusRow}>
      <div style={S.statusLeft}>
        <div style={S.dot(color)} />
        <span style={{ fontSize:13, fontWeight:500 }}>{label}</span>
      </div>
      <div style={{ display:'flex', alignItems:'center', gap:8 }}>
        {detail && <span style={{ fontSize:12, color:'#94a3b8' }}>{detail}</span>}
        <span style={S.badge(color)}>{status}</span>
      </div>
    </div>
  );
}

export default function App() {
  const [readings, setReadings]         = useState([]);
  const [latest, setLatest]             = useState(null);
  const [totalCount, setTotalCount]     = useState(null);
  const [alerts, setAlerts]             = useState([]);
  const [renderStatus, setRenderStatus] = useState(null);
  const [activeModel, setActiveModel]   = useState(null);
  const [fbConnected, setFbConnected]   = useState(false);
  const [esp32Active, setEsp32Active]   = useState(false);
  const [lastUpdated, setLastUpdated]   = useState(null);
  const [tab, setTab]                   = useState('overview');

  useEffect(() => {
    const q = query(collection(db,'readings'), orderBy('created_at','desc'), limit(CHART_WINDOW));
    const unsub = onSnapshot(q, snap => {
      setFbConnected(true);
      const docs = snap.docs.map(d => ({ id:d.id, ...d.data() })).reverse();
      setReadings(docs);
      if (docs.length > 0) {
        const last = docs[docs.length - 1];
        setLatest(last);
        setLastUpdated(new Date());
        const ts = last.created_at?.toDate?.();
        if (ts) setEsp32Active((Date.now() - ts.getTime()) < 10000);
      }
    }, () => setFbConnected(false));
    return unsub;
  }, []);

  useEffect(() => {
    async function getCount() {
      const snap = await getCountFromServer(collection(db,'readings'));
      setTotalCount(snap.data().count);
    }
    getCount();
    const iv = setInterval(getCount, 10000);
    return () => clearInterval(iv);
  }, []);

  const refreshExternal = useCallback(async () => {
    const [health, alertData] = await Promise.all([fetchHealth(), fetchAlerts()]);
    setRenderStatus(health);
    setAlerts(alertData?.alerts || []);
  }, []);

  useEffect(() => {
    refreshExternal();
    const iv = setInterval(refreshExternal, REFRESH_INTERVAL);
    return () => clearInterval(iv);
  }, [refreshExternal]);

  useEffect(() => {
    const q = query(collection(db,'models'), orderBy('trained_at','desc'), limit(1));
    const unsub = onSnapshot(q, snap => {
      if (!snap.empty) setActiveModel({ id:snap.docs[0].id, ...snap.docs[0].data() });
      else setActiveModel(null);
    });
    return unsub;
  }, []);

  useEffect(() => {
    if (!latest) return;
    const iv = setInterval(() => {
      const ts = latest.created_at?.toDate?.();
      if (ts) setEsp32Active((Date.now() - ts.getTime()) < 10000);
    }, 3000);
    return () => clearInterval(iv);
  }, [latest]);

  const chartData = readings.map(r => ({
    time:      r.timestamp ? format(new Date(r.timestamp), 'HH:mm:ss') : '',
    temp:      parseFloat(r.winding_temp?.toFixed(2)),
    current:   parseFloat(r.current?.toFixed(3)),
    vibration: parseFloat(r.vibration?.toFixed(4)),
    oil:       parseFloat(r.oil_level?.toFixed(1)),
  }));

  const progress = totalCount ? Math.min((totalCount / 50000) * 100, 100) : 0;

  const handleAck = async (id) => {
    await acknowledgeAlert(id);
    setAlerts(prev => prev.filter(a => a.id !== id));
  };

  const TABS = ['overview','charts','alerts','system'];

  return (
    <div style={S.app}>

      {/* HEADER */}
      <header style={S.header}>
        <div style={S.headerLeft}>
          <div style={S.logo}>⚡</div>
          <div>
            <p style={S.headerTitle}>Transformer PM</p>
            <p style={S.headerSub}>Predictive Maintenance System</p>
          </div>
        </div>
        <div style={S.headerRight}>
          {lastUpdated && (
            <span style={{ fontSize:12, color:'#94a3b8' }}>
              {formatDistanceToNow(lastUpdated, { addSuffix:true })}
            </span>
          )}
          {alerts.length > 0 && (
            <span style={{ background:'#ef4444', color:'#fff', borderRadius:20, padding:'2px 10px', fontSize:12, fontWeight:600 }}>
              🔔 {alerts.length} alert{alerts.length > 1 ? 's' : ''}
            </span>
          )}
          <button style={S.refreshBtn} onClick={refreshExternal}>↻ Refresh</button>
        </div>
      </header>

      {/* TABS */}
      <div style={S.tabs}>
        {TABS.map(t => (
          <button key={t} style={S.tab(tab===t)} onClick={() => setTab(t)}>
            {t.charAt(0).toUpperCase() + t.slice(1)}
            {t === 'alerts' && alerts.length > 0 && (
              <span style={{ marginLeft:6, background:'#ef4444', color:'#fff', borderRadius:20, padding:'1px 7px', fontSize:11 }}>
                {alerts.length}
              </span>
            )}
          </button>
        ))}
      </div>

      <main style={S.main}>

        {/* ── OVERVIEW ── */}
        {tab === 'overview' && <>
          <div>
            <p style={S.sectionTitle}>Live Sensor Readings</p>
            <div style={S.row}>
              <StatCard label="Winding Temp" value={latest?.winding_temp?.toFixed(1)} unit="°C" color="orange" icon="🌡" fault={latest?.temp_fault} />
              <StatCard label="Current"      value={latest?.current?.toFixed(3)}      unit="A"  color="blue"   icon="⚡" />
              <StatCard label="Vibration"    value={latest?.vibration?.toFixed(4)}    unit="m/s²" color="purple" icon="📳" />
              <StatCard label="Oil Level"    value={latest?.oil_level?.toFixed(1)}    unit="%" color="teal"   icon="🛢" />
            </div>
          </div>

          <div style={S.progressWrap}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
              <div>
                <p style={{ margin:0, fontWeight:600, fontSize:14 }}>Data Collection Progress</p>
                <p style={{ margin:'2px 0 0', fontSize:12, color:'#94a3b8' }}>Target: 50,000 readings for Phase 1 training</p>
              </div>
              <div style={{ textAlign:'right' }}>
                <p style={{ margin:0, fontSize:28, fontWeight:700, color:'#2563eb' }}>{totalCount?.toLocaleString() ?? '—'}</p>
                <p style={{ margin:0, fontSize:12, color:'#94a3b8' }}>readings collected</p>
              </div>
            </div>
            <div style={S.progressBar}>
              <div style={S.progressFill(progress)} />
            </div>
            <div style={{ display:'flex', justifyContent:'space-between', fontSize:12, color:'#94a3b8' }}>
              <span>0</span>
              <span style={{ color:'#2563eb', fontWeight:600 }}>{progress.toFixed(1)}%</span>
              <span>50,000</span>
            </div>
            {totalCount >= 50000 && (
              <div style={S.readyBanner}>✅ Ready for Phase 1 unsupervised training</div>
            )}
          </div>

          <div style={S.card(false)}>
            <p style={S.sectionTitle}>Temperature — Last {CHART_WINDOW} Readings</p>
            <ResponsiveContainer width="100%" height={180}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="time" tick={{ fontSize:10 }} interval="preserveStartEnd" />
                <YAxis tick={{ fontSize:10 }} domain={['auto','auto']} />
                <Tooltip />
                <Line type="monotone" dataKey="temp" stroke="#f97316" dot={false} strokeWidth={2} name="Temp °C" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </>}

        {/* ── CHARTS ── */}
        {tab === 'charts' && (
          <div style={{ display:'flex', flexDirection:'column', gap:20 }}>
            {[
              { key:'temp',      label:'Winding Temperature (°C)', color:'#f97316' },
              { key:'current',   label:'Current (A)',               color:'#3b82f6' },
              { key:'vibration', label:'Vibration (m/s²)',          color:'#8b5cf6' },
              { key:'oil',       label:'Oil Level (%)',             color:'#14b8a6' },
            ].map(({ key, label, color }) => (
              <div key={key} style={S.card(false)}>
                <p style={S.sectionTitle}>{label}</p>
                <ResponsiveContainer width="100%" height={200}>
                  <LineChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis dataKey="time" tick={{ fontSize:10 }} interval="preserveStartEnd" />
                    <YAxis tick={{ fontSize:10 }} domain={['auto','auto']} />
                    <Tooltip />
                    <Line type="monotone" dataKey={key} stroke={color} dot={false} strokeWidth={2} name={label} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ))}
          </div>
        )}

        {/* ── ALERTS ── */}
        {tab === 'alerts' && (
          <div>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:16 }}>
              <p style={S.sectionTitle}>Active Alerts ({alerts.length})</p>
              <button style={S.refreshBtn} onClick={refreshExternal}>↻ Refresh</button>
            </div>
            {alerts.length === 0 ? (
              <div style={S.empty}>
                <div style={{ fontSize:40, marginBottom:8 }}>✅</div>
                <p style={{ fontWeight:600, margin:0 }}>No active alerts</p>
                <p style={{ fontSize:12, marginTop:4 }}>Alerts appear here after ML models are trained</p>
              </div>
            ) : alerts.map(a => (
              <div key={a.id} style={S.alertCard(a.severity)}>
                <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
                  <div>
                    <p style={{ margin:0, fontWeight:700, fontSize:14 }}>{a.label || a.alert_type}</p>
                    <p style={{ margin:'4px 0 0', fontSize:12, color:'#64748b' }}>
                      Confidence: {a.confidence ? `${(a.confidence*100).toFixed(1)}%` : 'N/A'} &nbsp;|&nbsp; Severity: {a.severity}
                    </p>
                    <p style={{ margin:'2px 0 0', fontSize:11, color:'#94a3b8' }}>
                      {a.timestamp?.toDate ? formatDistanceToNow(a.timestamp.toDate(), { addSuffix:true }) : '—'}
                    </p>
                  </div>
                  <button style={S.ackBtn} onClick={() => handleAck(a.id)}>Acknowledge</button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* ── SYSTEM ── */}
        {tab === 'system' && (
          <div style={{ display:'flex', flexDirection:'column', gap:20 }}>

            <div style={S.card(false)}>
              <p style={S.sectionTitle}>System Status</p>
              <StatusRow label="Firebase Connection" status={fbConnected ? 'Connected' : 'Disconnected'} color={fbConnected ? 'green' : 'red'} />
              <StatusRow label="Render Backend"      status={renderStatus ? 'Online' : 'Offline'} detail={renderStatus ? `Uptime ${Math.floor(renderStatus.uptime)}s` : ''} color={renderStatus ? 'green' : 'red'} />
              <StatusRow label="ESP32 Device"        status={esp32Active ? 'Transmitting' : 'Idle / Offline'} detail={latest?.seq != null ? `Seq #${latest.seq}` : ''} color={esp32Active ? 'green' : 'yellow'} />
              <StatusRow label="Data Collection"     status={totalCount >= 50000 ? 'Ready to Train' : 'Collecting'} detail={`${totalCount?.toLocaleString() ?? '—'} readings`} color={totalCount >= 50000 ? 'green' : 'blue'} />
              <StatusRow label="ML Model"            status={activeModel ? `v${activeModel.version} Active` : 'Not trained yet'} detail={activeModel ? `F1: ${activeModel.rf_f1?.toFixed(3)}` : 'Waiting for data'} color={activeModel ? 'green' : 'gray'} />
            </div>

            {latest && (
              <div style={S.card(false)}>
                <p style={S.sectionTitle}>Last Reading Detail</p>
                {[
                  ['Document ID',    latest.id],
                  ['Timestamp',      latest.timestamp],
                  ['Sequence #',     latest.seq ?? 'N/A'],
                  ['Uptime (ms)',    latest.uptime_ms?.toLocaleString() ?? 'N/A'],
                  ['Temp Fault',     latest.temp_fault ? '⚠ YES' : 'No'],
                  ['Label',          latest.label ?? 'Unlabeled'],
                  ['Anomaly Score',  latest.anomaly_score ?? 'Not scored yet'],
                  ['Predicted',      latest.predicted_label ?? 'Not predicted yet'],
                  ['Confidence',     latest.confidence ? `${(latest.confidence*100).toFixed(1)}%` : '—'],
                  ['Severity',       latest.severity ?? '—'],
                ].map(([k,v]) => (
                  <div key={k} style={S.detailRow}>
                    <span style={S.detailKey}>{k}</span>
                    <span style={S.detailVal}>{String(v)}</span>
                  </div>
                ))}
              </div>
            )}

            <div style={S.card(false)}>
              <p style={S.sectionTitle}>ML Model Status</p>
              {activeModel ? (
                [
                  ['Version',        activeModel.version],
                  ['Type',           activeModel.type],
                  ['RF F1 Score',    activeModel.rf_f1?.toFixed(4)],
                  ['GB F1 Score',    activeModel.gb_f1?.toFixed(4)],
                  ['Trained On',     `${activeModel.n_samples?.toLocaleString()} samples`],
                  ['Retrain Reason', activeModel.reason],
                ].map(([k,v]) => (
                  <div key={k} style={S.detailRow}>
                    <span style={S.detailKey}>{k}</span>
                    <span style={S.detailVal}>{String(v ?? '—')}</span>
                  </div>
                ))
              ) : (
                <div style={S.noModel}>
                  <div style={{ fontSize:36, marginBottom:8 }}>🧠</div>
                  <p style={{ margin:0, fontWeight:600 }}>No model trained yet</p>
                  <p style={{ margin:'4px 0 0', fontSize:12 }}>Available after Phase 1 at 50,000 readings</p>
                </div>
              )}
            </div>

          </div>
        )}

      </main>

      <footer style={S.footer}>
        Transformer PM v1.0 — {lastUpdated && `Last sync ${format(lastUpdated, 'HH:mm:ss')}`}
      </footer>
    </div>
  );
}