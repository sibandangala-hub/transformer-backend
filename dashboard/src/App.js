import { useState, useEffect, useCallback } from 'react';
import { db } from './services/firebase';
import {
  collection, query, orderBy, limit,
  onSnapshot, getCountFromServer
} from 'firebase/firestore';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, AreaChart, Area
} from 'recharts';
import { format, formatDistanceToNow } from 'date-fns';

const BASE_URL = 'https://transformer-pm-api.onrender.com';
const API_KEY  = 'your_secret_key_123';
const CHART_WINDOW     = 60;
const REFRESH_INTERVAL = 30000;
const TARGET_READINGS  = 50000;

const C = {
  blue:   '#185FA5',
  blueLt: '#E6F1FB',
  blueMd: '#378ADD',
  green:  '#3B6D11',
  greenLt:'#EAF3DE',
  amber:  '#854F0B',
  amberLt:'#FAEEDA',
  red:    '#A32D2D',
  redLt:  '#FCEBEB',
  teal:   '#0F6E56',
  tealLt: '#E1F5EE',
  purple: '#534AB7',
  purpleLt:'#EEEDFE',
  coral:  '#993C1D',
  coralLt:'#FAECE7',
};

const css = {
  app: {
    minHeight: '100vh',
    background: '#f4f6f9',
    fontFamily: '-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif',
    color: '#1a1a2e',
  },
  header: {
    background: '#0c1445',
    padding: '0 24px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    height: 56,
    position: 'sticky',
    top: 0,
    zIndex: 100,
  },
  headerLeft: { display: 'flex', alignItems: 'center', gap: 12 },
  logoBox: {
    width: 32, height: 32,
    background: '#185FA5',
    borderRadius: 8,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 16, color: '#fff', fontWeight: 700,
  },
  brandName: { color: '#fff', fontSize: 14, fontWeight: 600, margin: 0 },
  brandSub:  { color: '#8892b0', fontSize: 11, margin: 0 },
  headerRight: { display: 'flex', alignItems: 'center', gap: 10 },
  navTabs: {
    background: '#fff',
    borderBottom: '1px solid #e8ecf0',
    padding: '0 24px',
    display: 'flex',
    gap: 0,
    overflowX: 'auto',
  },
  navTab: (active) => ({
    padding: '14px 20px',
    fontSize: 13,
    fontWeight: active ? 600 : 400,
    cursor: 'pointer',
    border: 'none',
    background: 'none',
    borderBottom: active ? '2px solid #185FA5' : '2px solid transparent',
    color: active ? '#185FA5' : '#5a6a7e',
    whiteSpace: 'nowrap',
    transition: 'all 0.15s',
  }),
  main: { maxWidth: 1200, margin: '0 auto', padding: '20px 20px 40px' },
  sectionLabel: {
    fontSize: 11, fontWeight: 600, color: '#8892b0',
    textTransform: 'uppercase', letterSpacing: '0.08em',
    margin: '0 0 12px',
  },
  grid4: { display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12 },
  grid3: { display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12 },
  grid2: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 },
  col:   { display: 'flex', flexDirection: 'column', gap: 16 },
  card: (accent) => ({
    background: '#fff',
    borderRadius: 12,
    border: accent ? `1.5px solid ${accent}` : '1px solid #e8ecf0',
    padding: '16px 20px',
    position: 'relative',
    overflow: 'hidden',
  }),
  cardAccentBar: (color) => ({
    position: 'absolute', top: 0, left: 0, right: 0,
    height: 3, background: color, borderRadius: '12px 12px 0 0',
  }),
  metricLabel: { fontSize: 12, color: '#8892b0', fontWeight: 500, margin: '0 0 6px' },
  metricValue: { fontSize: 28, fontWeight: 700, color: '#1a1a2e', margin: 0, lineHeight: 1 },
  metricUnit:  { fontSize: 13, color: '#8892b0', marginLeft: 4 },
  metricSub:   { fontSize: 11, color: '#8892b0', margin: '4px 0 0' },
  faultTag: {
    display: 'inline-block', fontSize: 10, fontWeight: 600,
    background: C.redLt, color: C.red,
    border: `1px solid #F09595`, borderRadius: 4,
    padding: '2px 6px', marginTop: 4,
  },
  statusDot: (color) => ({
    width: 8, height: 8, borderRadius: '50%',
    background: color, flexShrink: 0,
    boxShadow: `0 0 0 2px ${color}22`,
  }),
  badge: (bg, text) => ({
    fontSize: 11, fontWeight: 600, padding: '2px 8px',
    borderRadius: 99, background: bg, color: text,
    display: 'inline-block',
  }),
  progressWrap: { background: '#e8ecf0', borderRadius: 99, height: 8, overflow: 'hidden', margin: '10px 0 4px' },
  progressFill: (pct, color) => ({
    height: '100%', width: `${pct}%`,
    background: color, borderRadius: 99,
    transition: 'width 0.6s ease',
  }),
  alertRow: (severity) => {
    const map = { critical: [C.redLt, C.red, '#F09595'], medium: [C.amberLt, C.amber, '#EF9F27'], low: [C.blueLt, C.blue, '#85B7EB'] };
    const [bg, text, border] = map[severity] || [C.blueLt, C.blue, '#85B7EB'];
    return { background: bg, borderLeft: `3px solid ${border}`, borderRadius: '0 8px 8px 0', padding: '12px 16px', marginBottom: 10, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' };
  },
  detailRow: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '9px 0', borderBottom: '1px solid #f0f2f5', fontSize: 13,
  },
  detailKey: { color: '#8892b0' },
  detailVal: { fontWeight: 500, maxWidth: 240, textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  btn: (color, bg) => ({
    border: `1px solid ${color}`, background: bg || 'transparent',
    color: color, borderRadius: 8, padding: '7px 14px',
    fontSize: 12, fontWeight: 600, cursor: 'pointer',
  }),
  btnPrimary: (loading) => ({
    background: loading ? '#8892b0' : '#185FA5',
    color: '#fff', border: 'none', borderRadius: 8,
    padding: '8px 16px', fontSize: 12, fontWeight: 600,
    cursor: loading ? 'not-allowed' : 'pointer',
    display: 'flex', alignItems: 'center', gap: 6,
  }),
  divider: { height: 1, background: '#f0f2f5', margin: '16px 0' },
  systemRow: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '11px 0', borderBottom: '1px solid #f0f2f5',
  },
  systemLeft: { display: 'flex', alignItems: 'center', gap: 10 },
  empty: { textAlign: 'center', padding: '48px 0', color: '#8892b0' },
  emptyIcon: { fontSize: 36, marginBottom: 12, opacity: 0.4 },
  chartTooltip: {
    background: '#fff', border: '1px solid #e8ecf0', borderRadius: 8,
    padding: '8px 12px', fontSize: 12,
  },
};

const SENSOR_CONFIG = [
  { key: 'winding_temp', label: 'Winding temp',  unit: '°C',    color: '#D85A30', accentColor: C.coralLt, chartKey: 'temp',      precision: 1 },
  { key: 'current',      label: 'Load current',  unit: 'A',     color: C.blue,    accentColor: C.blueLt,  chartKey: 'current',   precision: 3 },
  { key: 'vibration',    label: 'Vibration',     unit: 'm/s²',  color: C.purple,  accentColor: C.purpleLt,chartKey: 'vibration', precision: 4 },
  { key: 'oil_level',    label: 'Oil level',     unit: '%',     color: C.teal,    accentColor: C.tealLt,  chartKey: 'oil',       precision: 1 },
];

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div style={css.chartTooltip}>
      <p style={{ margin: '0 0 4px', color: '#8892b0', fontSize: 11 }}>{label}</p>
      {payload.map(p => (
        <p key={p.name} style={{ margin: 0, color: p.color, fontWeight: 600 }}>
          {p.name}: {p.value}
        </p>
      ))}
    </div>
  );
}

function MetricCard({ label, value, unit, color, accentColor, fault, sub }) {
  return (
    <div style={css.card()}>
      <div style={css.cardAccentBar(color)} />
      <div style={{ paddingTop: 4 }}>
        <p style={css.metricLabel}>{label}</p>
        <div style={{ display: 'flex', alignItems: 'baseline' }}>
          <span style={{ ...css.metricValue, color }}>{value ?? '—'}</span>
          <span style={css.metricUnit}>{unit}</span>
        </div>
        {fault && <span style={css.faultTag}>Sensor fault</span>}
        {sub && <p style={css.metricSub}>{sub}</p>}
      </div>
    </div>
  );
}

function SystemStatusRow({ label, status, detail, color }) {
  const dotColors = { green: '#22c55e', red: '#ef4444', yellow: '#f59e0b', gray: '#94a3b8', blue: C.blue };
  const badgeMap  = {
    green:  [C.greenLt,  C.green],
    red:    [C.redLt,    C.red],
    yellow: [C.amberLt,  C.amber],
    gray:   ['#f0f2f5',  '#5a6a7e'],
    blue:   [C.blueLt,   C.blue],
  };
  const [bg, text] = badgeMap[color] || badgeMap.gray;
  return (
    <div style={css.systemRow}>
      <div style={css.systemLeft}>
        <div style={css.statusDot(dotColors[color] || dotColors.gray)} />
        <span style={{ fontSize: 13, fontWeight: 500 }}>{label}</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        {detail && <span style={{ fontSize: 11, color: '#8892b0' }}>{detail}</span>}
        <span style={css.badge(bg, text)}>{status}</span>
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
  const [exporting, setExporting]       = useState(false);

  useEffect(() => {
    const q = query(collection(db,'readings'), orderBy('created_at','desc'), limit(CHART_WINDOW));
    const unsub = onSnapshot(q, snap => {
      setFbConnected(true);
      const docs = snap.docs.map(d => ({ id: d.id, ...d.data() })).reverse();
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
    try {
      const [hRes, aRes] = await Promise.all([
        fetch(`${BASE_URL}/health`),
        fetch(`${BASE_URL}/api/alerts`, { headers: { 'x-api-key': API_KEY } })
      ]);
      setRenderStatus(await hRes.json());
      const ad = await aRes.json();
      setAlerts(ad?.alerts || []);
    } catch { setRenderStatus(null); }
  }, []);

  useEffect(() => {
    refreshExternal();
    const iv = setInterval(refreshExternal, REFRESH_INTERVAL);
    return () => clearInterval(iv);
  }, [refreshExternal]);

  useEffect(() => {
    const q = query(collection(db,'models'), orderBy('trained_at','desc'), limit(1));
    const unsub = onSnapshot(q, snap => {
      setActiveModel(snap.empty ? null : { id: snap.docs[0].id, ...snap.docs[0].data() });
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

  const exportCSV = async () => {
    setExporting(true);
    try {
      const res = await fetch(`${BASE_URL}/api/export/csv`, { headers: { 'x-api-key': API_KEY } });
      if (!res.ok) throw new Error(`${res.status}`);
      const blob = await res.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      a.href = url;
      a.download = `transformer_${new Date().toISOString().slice(0,10)}.csv`;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) { alert('Export failed: ' + e.message); }
    setExporting(false);
  };

  const handleAck = async (id) => {
    try {
      await fetch(`${BASE_URL}/api/alerts/${id}/ack`, { method: 'PATCH', headers: { 'x-api-key': API_KEY } });
      setAlerts(prev => prev.filter(a => a.id !== id));
    } catch (e) { alert('Failed: ' + e.message); }
  };

  const chartData = readings.map(r => ({
    time:      r.timestamp ? format(new Date(r.timestamp), 'HH:mm:ss') : '',
    temp:      parseFloat(r.winding_temp?.toFixed(1)),
    current:   parseFloat(r.current?.toFixed(3)),
    vibration: parseFloat(r.vibration?.toFixed(4)),
    oil:       parseFloat(r.oil_level?.toFixed(1)),
    score:     r.anomaly_score != null ? parseFloat(r.anomaly_score.toFixed(3)) : null,
  }));

  const anomalyCount  = readings.filter(r => r.anomaly_score > 0.5).length;
  const progress      = totalCount ? Math.min((totalCount / TARGET_READINGS) * 100, 100) : 0;
  const criticalCount = alerts.filter(a => a.severity === 'critical').length;

  const TABS = [
    { key: 'overview',    label: 'Overview' },
    { key: 'charts',      label: 'Sensor charts' },
    { key: 'anomalies',   label: `Anomalies${anomalyCount > 0 ? ` (${anomalyCount})` : ''}` },
    { key: 'alerts',      label: `Alerts${alerts.length > 0 ? ` (${alerts.length})` : ''}` },
    { key: 'system',      label: 'System' },
  ];

  return (
    <div style={css.app}>

      {/* TOP BAR */}
      <header style={css.header}>
        <div style={css.headerLeft}>
          <div style={css.logoBox}>T</div>
          <div>
            <p style={css.brandName}>Transformer PM</p>
            <p style={css.brandSub}>Predictive Maintenance System</p>
          </div>
        </div>
        <div style={css.headerRight}>
          {criticalCount > 0 && (
            <span style={{ background: C.red, color: '#fff', borderRadius: 99, padding: '3px 10px', fontSize: 11, fontWeight: 700 }}>
              {criticalCount} critical
            </span>
          )}
          {lastUpdated && (
            <span style={{ fontSize: 11, color: '#8892b0' }}>
              {formatDistanceToNow(lastUpdated, { addSuffix: true })}
            </span>
          )}
          <button style={css.btnPrimary(exporting)} onClick={exportCSV} disabled={exporting}>
            {exporting ? 'Exporting...' : 'Export CSV'}
          </button>
          <button style={css.btn('#8892b0')} onClick={refreshExternal}>Refresh</button>
        </div>
      </header>

      {/* NAV TABS */}
      <nav style={css.navTabs}>
        {TABS.map(t => (
          <button key={t.key} style={css.navTab(tab === t.key)} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </nav>

      <main style={css.main}>

        {/* ═══════════════ OVERVIEW ═══════════════ */}
        {tab === 'overview' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

            {/* Sensor metric cards */}
            <div>
              <p style={css.sectionLabel}>Live sensor readings</p>
              <div style={css.grid4}>
                {SENSOR_CONFIG.map(s => (
                  <MetricCard
                    key={s.key}
                    label={s.label}
                    value={latest?.[s.key]?.toFixed(s.precision)}
                    unit={s.unit}
                    color={s.color}
                    fault={s.key === 'winding_temp' && latest?.temp_fault}
                  />
                ))}
              </div>
            </div>

            {/* Status + Progress row */}
            <div style={css.grid2}>

              {/* Collection progress */}
              <div style={css.card()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
                  <div>
                    <p style={css.metricLabel}>Data collection</p>
                    <p style={{ margin: 0, fontSize: 11, color: '#8892b0' }}>Target: {TARGET_READINGS.toLocaleString()} readings</p>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <p style={{ margin: 0, fontSize: 26, fontWeight: 700, color: C.blue }}>{totalCount?.toLocaleString() ?? '—'}</p>
                    <p style={{ margin: 0, fontSize: 11, color: '#8892b0' }}>readings</p>
                  </div>
                </div>
                <div style={css.progressWrap}>
                  <div style={css.progressFill(progress, progress >= 100 ? C.green : C.blue)} />
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#8892b0' }}>
                  <span>0</span>
                  <span style={{ color: C.blue, fontWeight: 600 }}>{progress.toFixed(1)}%</span>
                  <span>{TARGET_READINGS.toLocaleString()}</span>
                </div>
                {totalCount >= TARGET_READINGS && (
                  <div style={{ marginTop: 10, padding: '8px 12px', background: C.greenLt, borderRadius: 8, fontSize: 12, color: C.green, fontWeight: 600 }}>
                    Ready for Phase 1 training
                  </div>
                )}
              </div>

              {/* Quick system status */}
              <div style={css.card()}>
                <p style={css.metricLabel}>System status</p>
                <SystemStatusRow label="Firebase"      status={fbConnected ? 'Connected'    : 'Disconnected'}  color={fbConnected    ? 'green' : 'red'}    detail="" />
                <SystemStatusRow label="Render API"    status={renderStatus ? 'Online'       : 'Offline'}       color={renderStatus   ? 'green' : 'red'}    detail={renderStatus ? `${Math.floor(renderStatus.uptime)}s uptime` : ''} />
                <SystemStatusRow label="ESP32 device"  status={esp32Active  ? 'Transmitting' : 'Idle'}          color={esp32Active    ? 'green' : 'yellow'} detail={latest?.seq != null ? `seq #${latest.seq}` : ''} />
                <SystemStatusRow label="ML model"      status={activeModel  ? 'Active'        : 'Not trained'}  color={activeModel    ? 'green' : 'gray'}   detail={activeModel ? `F1: ${activeModel.rf_f1?.toFixed(3)}` : ''} />
              </div>
            </div>

            {/* Mini anomaly score chart */}
            {chartData.some(d => d.score !== null) && (
              <div style={css.card()}>
                <p style={css.metricLabel}>Anomaly score — last {CHART_WINDOW} readings</p>
                <ResponsiveContainer width="100%" height={140}>
                  <AreaChart data={chartData}>
                    <defs>
                      <linearGradient id="scoreGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%"  stopColor={C.red} stopOpacity={0.2} />
                        <stop offset="95%" stopColor={C.red} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f2f5" />
                    <XAxis dataKey="time" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                    <YAxis tick={{ fontSize: 10 }} domain={[0, 1]} />
                    <Tooltip content={<CustomTooltip />} />
                    <Area type="monotone" dataKey="score" stroke={C.red} fill="url(#scoreGrad)" strokeWidth={1.5} name="Anomaly score" dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Temperature mini chart */}
            <div style={css.card()}>
              <p style={css.metricLabel}>Winding temperature — last {CHART_WINDOW} readings</p>
              <ResponsiveContainer width="100%" height={160}>
                <AreaChart data={chartData}>
                  <defs>
                    <linearGradient id="tempGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor="#D85A30" stopOpacity={0.15} />
                      <stop offset="95%" stopColor="#D85A30" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f2f5" />
                  <XAxis dataKey="time" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                  <YAxis tick={{ fontSize: 10 }} domain={['auto','auto']} />
                  <Tooltip content={<CustomTooltip />} />
                  <Area type="monotone" dataKey="temp" stroke="#D85A30" fill="url(#tempGrad)" strokeWidth={2} name="Temp °C" dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>

          </div>
        )}

        {/* ═══════════════ CHARTS ═══════════════ */}
        {tab === 'charts' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <p style={css.sectionLabel}>Sensor data — last {CHART_WINDOW} readings</p>
            {SENSOR_CONFIG.map(s => (
              <div key={s.key} style={css.card()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                  <p style={{ margin: 0, fontSize: 13, fontWeight: 600, color: s.color }}>{s.label}</p>
                  <span style={{ fontSize: 22, fontWeight: 700, color: s.color }}>
                    {latest?.[s.key]?.toFixed(s.precision) ?? '—'}
                    <span style={{ fontSize: 12, color: '#8892b0', marginLeft: 4 }}>{s.unit}</span>
                  </span>
                </div>
                <ResponsiveContainer width="100%" height={180}>
                  <AreaChart data={chartData}>
                    <defs>
                      <linearGradient id={`grad_${s.key}`} x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%"  stopColor={s.color} stopOpacity={0.12} />
                        <stop offset="95%" stopColor={s.color} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f2f5" />
                    <XAxis dataKey="time" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                    <YAxis tick={{ fontSize: 10 }} domain={['auto','auto']} />
                    <Tooltip content={<CustomTooltip />} />
                    <Area type="monotone" dataKey={s.chartKey} stroke={s.color} fill={`url(#grad_${s.key})`} strokeWidth={2} name={`${s.label} (${s.unit})`} dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            ))}
          </div>
        )}

        {/* ═══════════════ ANOMALIES ═══════════════ */}
        {tab === 'anomalies' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <p style={css.sectionLabel}>Anomaly detection — phase 1 results</p>
              <div style={{ display: 'flex', gap: 8 }}>
                <span style={css.badge(C.redLt, C.red)}>High: score &gt; 0.7</span>
                <span style={css.badge(C.amberLt, C.amber)}>Medium: score &gt; 0.5</span>
                <span style={css.badge(C.greenLt, C.green)}>Normal: score &lt; 0.3</span>
              </div>
            </div>

            {/* Anomaly score chart */}
            <div style={css.card()}>
              <p style={{ margin: '0 0 12px', fontSize: 13, fontWeight: 600 }}>Combined anomaly score</p>
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f2f5" />
                  <XAxis dataKey="time" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                  <YAxis tick={{ fontSize: 10 }} domain={[0, 1]} />
                  <Tooltip content={<CustomTooltip />} />
                  <Area type="monotone" dataKey="score" stroke={C.red} fill={C.redLt} strokeWidth={1.5} name="Anomaly score" dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>

            {/* Anomalous readings list */}
            <div style={css.card()}>
              <p style={{ margin: '0 0 14px', fontSize: 13, fontWeight: 600 }}>
                Flagged readings in current window ({readings.filter(r => r.anomaly_score > 0.5).length} found)
              </p>
              {readings.filter(r => r.anomaly_score > 0.5).length === 0 ? (
                <div style={css.empty}>
                  <div style={css.emptyIcon}>✓</div>
                  <p style={{ fontWeight: 600, margin: 0, fontSize: 14 }}>No anomalies in current window</p>
                  <p style={{ fontSize: 12, marginTop: 4 }}>Showing last {CHART_WINDOW} readings</p>
                </div>
              ) : (
                readings.filter(r => r.anomaly_score > 0.5).reverse().map(r => {
                  const score   = r.anomaly_score;
                  const severity = score > 0.7 ? 'critical' : 'medium';
                  const badgeColor = severity === 'critical' ? [C.redLt, C.red] : [C.amberLt, C.amber];
                  return (
                    <div key={r.id} style={{ ...css.alertRow(severity), marginBottom: 8 }}>
                      <div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                          <span style={css.badge(badgeColor[0], badgeColor[1])}>Score: {score.toFixed(3)}</span>
                          {r.iso_anomaly && <span style={css.badge(C.purpleLt, C.purple)}>IsoForest</span>}
                          {r.ae_anomaly  && <span style={css.badge(C.coralLt, C.coral)}>Autoencoder</span>}
                        </div>
                        <p style={{ margin: 0, fontSize: 12, color: '#5a6a7e' }}>
                          T: {r.winding_temp?.toFixed(1)}°C &nbsp;|&nbsp;
                          I: {r.current?.toFixed(3)}A &nbsp;|&nbsp;
                          Vib: {r.vibration?.toFixed(4)} &nbsp;|&nbsp;
                          Oil: {r.oil_level?.toFixed(1)}%
                        </p>
                        <p style={{ margin: '2px 0 0', fontSize: 11, color: '#8892b0' }}>{r.timestamp}</p>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}

        {/* ═══════════════ ALERTS ═══════════════ */}
        {tab === 'alerts' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <p style={css.sectionLabel}>Active alerts ({alerts.length})</p>
              <button style={css.btn('#8892b0')} onClick={refreshExternal}>Refresh</button>
            </div>

            {alerts.length === 0 ? (
              <div style={{ ...css.card(), ...css.empty }}>
                <div style={css.emptyIcon}>✓</div>
                <p style={{ fontWeight: 600, margin: 0, fontSize: 14 }}>No active alerts</p>
                <p style={{ fontSize: 12, marginTop: 4 }}>Alerts appear here when anomalies are detected</p>
              </div>
            ) : (
              alerts.map(a => (
                <div key={a.id} style={css.alertRow(a.severity)}>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <span style={{ fontSize: 13, fontWeight: 600 }}>{a.label || a.alert_type}</span>
                      <span style={css.badge(
                        a.severity === 'critical' ? C.redLt : a.severity === 'medium' ? C.amberLt : C.blueLt,
                        a.severity === 'critical' ? C.red   : a.severity === 'medium' ? C.amber   : C.blue
                      )}>{a.severity}</span>
                    </div>
                    <div style={{ display: 'flex', gap: 16, fontSize: 12, color: '#5a6a7e' }}>
                      {a.winding_temp && <span>T: {a.winding_temp?.toFixed(1)}°C</span>}
                      {a.current      && <span>I: {a.current?.toFixed(3)}A</span>}
                      {a.vibration    && <span>Vib: {a.vibration?.toFixed(4)}</span>}
                      {a.oil_level    && <span>Oil: {a.oil_level?.toFixed(1)}%</span>}
                    </div>
                    {a.confidence && (
                      <p style={{ margin: '4px 0 0', fontSize: 11, color: '#8892b0' }}>
                        Confidence: {(a.confidence * 100).toFixed(1)}%
                        {a.timestamp?.toDate && ` — ${formatDistanceToNow(a.timestamp.toDate(), { addSuffix: true })}`}
                      </p>
                    )}
                  </div>
                  <button style={{ ...css.btn('#8892b0'), marginLeft: 12, whiteSpace: 'nowrap' }} onClick={() => handleAck(a.id)}>
                    Acknowledge
                  </button>
                </div>
              ))
            )}
          </div>
        )}

        {/* ═══════════════ SYSTEM ═══════════════ */}
        {tab === 'system' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

            {/* System status card */}
            <div style={css.card()}>
              <p style={css.metricLabel}>System status</p>
              <SystemStatusRow label="Firebase connection" status={fbConnected ? 'Connected'    : 'Disconnected'}   color={fbConnected          ? 'green'  : 'red'}    detail="" />
              <SystemStatusRow label="Render backend"      status={renderStatus ? 'Online'        : 'Offline'}        color={renderStatus         ? 'green'  : 'red'}    detail={renderStatus ? `Uptime: ${Math.floor(renderStatus.uptime)}s` : ''} />
              <SystemStatusRow label="ESP32 device"        status={esp32Active  ? 'Transmitting'  : 'Idle / offline'} color={esp32Active          ? 'green'  : 'yellow'} detail={latest?.seq != null ? `Seq #${latest.seq}` : ''} />
              <SystemStatusRow label="Data collection"     status={totalCount >= TARGET_READINGS ? 'Ready to train' : 'Collecting'} color={totalCount >= TARGET_READINGS ? 'green' : 'blue'} detail={`${totalCount?.toLocaleString() ?? '—'} readings`} />
              <SystemStatusRow label="Phase 1 ML"          status={activeModel ? 'Trained' : 'Pending'}              color={activeModel          ? 'green'  : 'gray'}   detail={activeModel ? `F1: ${activeModel.rf_f1?.toFixed(3)}` : 'Awaiting training'} />
              <SystemStatusRow label="Phase 2 ML"          status="Pending"                                           color="gray"                                        detail="Requires labeled data" />
            </div>

            {/* Last reading detail */}
            {latest && (
              <div style={css.card()}>
                <p style={css.metricLabel}>Last reading detail</p>
                {[
                  ['Document ID',    latest.id],
                  ['Timestamp',      latest.timestamp],
                  ['Sequence',       latest.seq ?? 'N/A'],
                  ['Uptime (ms)',    latest.uptime_ms?.toLocaleString() ?? 'N/A'],
                  ['Temp fault',     latest.temp_fault ? 'YES — sensor fault' : 'No'],
                  ['Anomaly score',  latest.anomaly_score?.toFixed(4) ?? 'Not scored yet'],
                  ['Severity',       latest.severity ?? '—'],
                  ['Label',          latest.label ?? 'Unlabeled'],
                  ['Predicted',      latest.predicted_label ?? 'Not predicted yet'],
                  ['Confidence',     latest.confidence ? `${(latest.confidence * 100).toFixed(1)}%` : '—'],
                ].map(([k, v]) => (
                  <div key={k} style={css.detailRow}>
                    <span style={css.detailKey}>{k}</span>
                    <span style={{ ...css.detailVal, color: k === 'Temp fault' && latest.temp_fault ? C.red : undefined }}>
                      {String(v)}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {/* ML model status */}
            <div style={css.card()}>
              <p style={css.metricLabel}>ML model</p>
              {activeModel ? (
                [
                  ['Version',         activeModel.version],
                  ['Type',            activeModel.type],
                  ['RF F1 score',     activeModel.rf_f1?.toFixed(4)],
                  ['GB F1 score',     activeModel.gb_f1?.toFixed(4)],
                  ['Trained samples', activeModel.n_samples?.toLocaleString()],
                  ['Retrain reason',  activeModel.reason],
                ].map(([k, v]) => (
                  <div key={k} style={css.detailRow}>
                    <span style={css.detailKey}>{k}</span>
                    <span style={css.detailVal}>{String(v ?? '—')}</span>
                  </div>
                ))
              ) : (
                <div style={css.empty}>
                  <div style={css.emptyIcon}>◎</div>
                  <p style={{ fontWeight: 600, margin: 0, fontSize: 14 }}>No model trained yet</p>
                  <p style={{ fontSize: 12, marginTop: 4 }}>Supervised model available after Phase 2 training</p>
                </div>
              )}
            </div>

          </div>
        )}

      </main>

      <footer style={{ textAlign: 'center', padding: '20px', fontSize: 11, color: '#8892b0', borderTop: '1px solid #e8ecf0', background: '#fff' }}>
        Transformer PM v2.0 &nbsp;—&nbsp; {lastUpdated && `Last sync: ${format(lastUpdated, 'HH:mm:ss')}`} &nbsp;—&nbsp; <a href="https://transformer-pm.web.app" style={{ color: C.blue }}>transformer-pm.web.app</a>
      </footer>

    </div>
  );
}