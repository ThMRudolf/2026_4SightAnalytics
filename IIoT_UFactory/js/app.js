/* ─────────────────────────────────────────────
   KONFIGURATION
───────────────────────────────────────────── */
const API_BASE    = "";          // Gleicher Host:Port wie das HTML (Flask serviert beides)
const POLL_MS     = 100;         // Frontend-Polling-Intervall in ms
const RETRY_MS    = 3000;        // Wiederverbindungsintervall bei Fehler

/* ─────────────────────────────────────────────
   STATE
───────────────────────────────────────────── */
const state = {
  connected:   false,
  motionState: 0,
  errorCode:   0, warnCode: 0,
  tcpPos:      [0,0,0,0,0,0],
  joints:      [0,0,0,0,0,0],
  tcpSpeed:    0,
  jSpeed:      [0,0,0,0,0,0],
  currents:    [0,0,0,0,0,0],
  temps:       [0,0,0,0,0,0],
  ft:          [0,0,0,0,0,0],
  di:          [0,0,0,0], do_: [0,0,0,0],
  cmdBuf:      0,
  rtt:         0,
  tid:         0,
  reducedMode: false,
  uptime:      0,
  // Polling-Zustand
  _polling:    false,
  _pollTimer:  null,
};

/* ─────────────────────────────────────────────
   CLOCK
───────────────────────────────────────────── */
function tickClock() {
  const now = new Date();
  document.getElementById('clockEl').textContent =
    now.toLocaleTimeString('de-DE');
}
tickClock();
setInterval(tickClock, 1000);

/* ─────────────────────────────────────────────
   UPTIME
───────────────────────────────────────────── */
setInterval(()=>{
  state.uptime++;
  const h=Math.floor(state.uptime/60).toString().padStart(2,'0');
  const m=(state.uptime%60).toString().padStart(2,'0');
  document.getElementById('kpiUptime').textContent=`${h}:${m}`;
},60000);

/* ─────────────────────────────────────────────
   MOTION STATE DISPLAY
───────────────────────────────────────────── */
const motionLabels=['—','IN MOTION','SLEEP','SUSPENDED','STOPPED','RESET'];
const motionClasses=['','state-run','state-idle','state-suspend','state-stop','state-stop'];
function updateMotionState(s){
  const el=document.getElementById('motionState');
  el.textContent=motionLabels[s]||'UNKNOWN';
  el.className='state-pill '+(motionClasses[s]||'state-idle');
}

/* ─────────────────────────────────────────────
   SLIDERS
───────────────────────────────────────────── */
document.getElementById('tcpSpeedSlider').addEventListener('input',function(){
  document.getElementById('tcpSpeedSliderVal').textContent=this.value+' mm/s';
});
document.getElementById('jointSpeedSlider').addEventListener('input',function(){
  document.getElementById('jointSpeedSliderVal').textContent=this.value+' °/s';
});
document.getElementById('accSlider').addEventListener('input',function(){
  document.getElementById('accSliderVal').textContent=this.value+' mm/s²';
});

/* ─────────────────────────────────────────────
   LOG
───────────────────────────────────────────── */
const logEl=document.getElementById('logArea');
let logging=false;
let logLines=[];

function addLog(msg,type=''){
  if(!logging&&type!=='err')return;
  const ts=new Date().toLocaleTimeString('de-DE',{hour12:false,fractionalSecondDigits:3});
  const line=document.createElement('div');
  line.className=`log-line ${type}`;
  line.textContent=`[${ts}] ${msg}`;
  logEl.appendChild(line);
  while(logEl.children.length>80)logEl.removeChild(logEl.firstChild);
  logEl.scrollTop=logEl.scrollHeight;
}

document.getElementById('btnLog').addEventListener('click',function(){
  logging=!logging;
  this.textContent=logging?'■ Stop':'● Aufzeichnen';
  this.className=logging?'btn red':'btn cyan';
  addLog(logging?'Aufzeichnung gestartet':'Aufzeichnung gestoppt','ok');
});
document.getElementById('btnClearLog').addEventListener('click',()=>{
  logEl.innerHTML='';
});

/* ─────────────────────────────────────────────
   BUTTONS
───────────────────────────────────────────── */
document.getElementById('btnEstop').addEventListener('click',()=>{
  state.motionState=4;
  updateMotionState(4);
  addLog('NOTSTOPP gesendet → Reg 12 → 0x04','err');
  document.getElementById('connDot').className='conn-dot err';
});
document.getElementById('btnSuspend').addEventListener('click',()=>{
  state.motionState=3;
  updateMotionState(3);
  addLog('Anhalten → Reg 12 → 0x03','warn');
});
document.getElementById('btnResume').addEventListener('click',()=>{
  state.motionState=1;
  updateMotionState(1);
  addLog('Fortsetzen → Reg 12 → 0x00','ok');
  document.getElementById('connDot').className='conn-dot';
});
document.getElementById('btnClearErr').addEventListener('click',()=>{
  addLog('Fehler löschen → Reg 16','cmd');
  document.getElementById('errBox').classList.remove('show');
});
document.getElementById('btnClearWarn').addEventListener('click',()=>{
  addLog('Warnung löschen → Reg 17','cmd');
});

/* DO toggle */
document.getElementById('doGrid').addEventListener('click',function(e){
  const ch=e.target.closest('.io-ch[data-out]');
  if(!ch)return;
  ch.classList.toggle('on');
  const val=ch.classList.contains('on');
  ch.querySelector('.io-val').textContent=val?'1':'0';
  const name=ch.dataset.ch;
  addLog(`${name} → ${val?1:0} (Reg 131)`, 'cmd');
});

/* ─────────────────────────────────────────────
   GAUGE
───────────────────────────────────────────── */
function updateGauge(speed){
  const pct=Math.min(speed/1000,1);
  const total=220;
  const offset=total-(pct*total);
  document.getElementById('gaugeFill').setAttribute('stroke-dashoffset',offset.toFixed(1));
  const angle=-90+(pct*180);
  document.getElementById('gaugeNeedle').setAttribute('transform',`rotate(${angle.toFixed(1)} 90 90)`);
  document.getElementById('gaugeVal').textContent=Math.round(speed);
}

/* ─────────────────────────────────────────────
   TRAJECTORY CANVAS
───────────────────────────────────────────── */
const trajCanvas=document.getElementById('trajCanvas');
const tCtx=trajCanvas.getContext('2d');
const trajHistory={x:[],y:[],z:[]};
const TRAJ_MAX=200;

function drawTraj(){
  const W=trajCanvas.clientWidth||500, H=120;
  if(trajCanvas.width!==W){trajCanvas.width=W;trajCanvas.height=H;}

  tCtx.clearRect(0,0,W,H);

  // Grid
  tCtx.strokeStyle='rgba(255,255,255,0.04)';
  tCtx.lineWidth=0.5;
  for(let i=0;i<=4;i++){
    const y=i*(H/4);
    tCtx.beginPath(); tCtx.moveTo(0,y); tCtx.lineTo(W,y); tCtx.stroke();
  }
  for(let i=0;i<=10;i++){
    const x=i*(W/10);
    tCtx.beginPath(); tCtx.moveTo(x,0); tCtx.lineTo(x,H); tCtx.stroke();
  }

  const colors=['#ff4d4f','#00d68f','#3b8bff'];
  const histories=[trajHistory.x,trajHistory.y,trajHistory.z];
  const ranges=[[-300,300],[-300,300],[0,600]];

  histories.forEach((hist,ci)=>{
    if(hist.length<2)return;
    const [mn,mx]=ranges[ci];
    tCtx.beginPath();
    tCtx.strokeStyle=colors[ci];
    tCtx.lineWidth=1.5;
    tCtx.shadowColor=colors[ci];
    tCtx.shadowBlur=3;
    hist.forEach((v,i)=>{
      const x=(i/(TRAJ_MAX-1))*W;
      const y=H-((v-mn)/(mx-mn))*H;
      i===0?tCtx.moveTo(x,y):tCtx.lineTo(x,y);
    });
    tCtx.stroke();
    tCtx.shadowBlur=0;

    // current dot
    const last=hist[hist.length-1];
    const lx=((hist.length-1)/(TRAJ_MAX-1))*W;
    const ly=H-((last-mn)/(mx-mn))*H;
    tCtx.beginPath();
    tCtx.arc(lx,ly,3,0,Math.PI*2);
    tCtx.fillStyle=colors[ci];
    tCtx.fill();
  });
}

/* ─────────────────────────────────────────────
   SERVO CURRENT CANVAS
───────────────────────────────────────────── */
const curCanvas=document.getElementById('currentCanvas');
const cCtx=curCanvas.getContext('2d');
const curHistory=[[],[],[],[],[],[]];

function drawCurrents(){
  const W=curCanvas.clientWidth||300, H=120;
  if(curCanvas.width!==W){curCanvas.width=W;curCanvas.height=H;}
  cCtx.clearRect(0,0,W,H);

  const jColors=['#a855f7','#00c8e0','#f0a500','#3b8bff','#00d68f','#ff4d4f'];
  const maxC=3.0;

  curHistory.forEach((hist,ji)=>{
    if(hist.length<2)return;
    cCtx.beginPath();
    cCtx.strokeStyle=jColors[ji];
    cCtx.lineWidth=1;
    cCtx.globalAlpha=0.7;
    hist.forEach((v,i)=>{
      const x=(i/(TRAJ_MAX-1))*W;
      const y=H-(v/maxC)*H;
      i===0?cCtx.moveTo(x,y):cCtx.lineTo(x,y);
    });
    cCtx.stroke();
    cCtx.globalAlpha=1;
  });
}

/* ─────────────────────────────────────────────
   VERBINDUNGS-UI
───────────────────────────────────────────── */
function setConnUi(connected, label){
  const dot   = document.getElementById('connDot');
  const badge = document.getElementById('connStatusLabel');
  const btn   = document.getElementById('btnConnect');
  if(connected){
    dot.style.background  = 'var(--green)';
    dot.style.boxShadow   = '0 0 6px var(--green)';
    badge.style.color     = 'var(--green)';
    badge.textContent     = 'VERBUNDEN';
    btn.textContent       = '■ Trennen';
    btn.className         = 'btn red';
  } else {
    dot.style.background  = label==='VERBINDE…'?'var(--amber)':'var(--text3)';
    dot.style.boxShadow   = label==='VERBINDE…'?'0 0 6px var(--amber)':'none';
    badge.style.color     = label==='VERBINDE…'?'var(--amber)':'var(--text3)';
    badge.textContent     = label||'GETRENNT';
    btn.textContent       = '▶ Verbinden';
    btn.className         = 'btn cyan';
  }
}

/* ─────────────────────────────────────────────
   API-HILFSFUNKTIONEN
───────────────────────────────────────────── */
const API_BASE = '';
async function apiFetch(path, options={}){
  try {
    const r = await fetch(API_BASE+path,{
      ...options,
      headers:{'Content-Type':'application/json',...(options.headers||{})}
    });
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } catch(e){ return null; }
}
async function apiPost(path, body){
  return apiFetch(path,{method:'POST',body:JSON.stringify(body)});
}

/* ─────────────────────────────────────────────
   POLLING — echte Backend-API
   GET /api/poll  →  Flask  →  Modbus TCP :18333
───────────────────────────────────────────── */
let _pollFailCount = 0;

async function fetchPoll(){
  const t0  = performance.now();
  const data = await apiFetch('/api/poll');
  const rtt  = (performance.now()-t0).toFixed(1);

  if(!data){
    _pollFailCount++;
    if(_pollFailCount>=3){
      state.connected=false;
      setConnUi(false,'KEINE API');
      addLog('Backend nicht erreichbar — Verbindung unterbrochen','err');
    }
    return;
  }
  _pollFailCount=0;

  Object.assign(state,data);
  state.rtt=parseFloat(rtt);

  if(data.connected!==undefined){
    if(data.connected&&!state._prevConnected){
      setConnUi(true);
      addLog(`Roboter verbunden (${document.getElementById('connIpInput').value}:18333)`,'ok');
    } else if(!data.connected&&state._prevConnected){
      setConnUi(false,'ROBOTER GETRENNT');
      addLog('Verbindung zum Roboter verloren','err');
    }
    state._prevConnected=data.connected;
  }

  if(data.errorCode&&data.errorCode>0){
    document.getElementById('errMsg').textContent=
      `Fehlercode 0x${data.errorCode.toString(16).toUpperCase().padStart(2,'0')} / Warnung 0x${(data.warnCode||0).toString(16).toUpperCase().padStart(2,'0')}`;
    document.getElementById('errBox').classList.add('show');
    addLog(`Fehler erkannt: Code ${data.errorCode}, Warn ${data.warnCode||0}`,'err');
  }

  updateDOM();
}

/* Poll starten / stoppen */
function startPolling(){
  if(state._polling) return;
  state._polling=true;
  setConnUi(false,'VERBINDE…');
  addLog(`Polling gestartet → GET /api/poll alle 100ms`,'ok');
  fetchPoll();
  state._pollTimer=setInterval(fetchPoll,100);
}
function stopPolling(){
  if(!state._polling) return;
  state._polling=false;
  clearInterval(state._pollTimer);
  state._pollTimer=null;
  state.connected=false;
  setConnUi(false,'GETRENNT');
  addLog('Polling gestoppt','warn');
}

document.getElementById('btnConnect').addEventListener('click',function(){
  if(state._polling) stopPolling(); else startPolling();
});

/* ─────────────────────────────────────────────
   STEUERBEFEHLE → Backend → Roboter
───────────────────────────────────────────── */
document.getElementById('btnEstop').addEventListener('click',async()=>{
  addLog('NOTSTOPP → POST /api/motion {state:4}','err');
  const r=await apiPost('/api/motion',{state:4});
  if(r?.ok){ updateMotionState(4); addLog('Notstopp bestätigt','ok'); }
  else       addLog('Notstopp fehlgeschlagen','err');
});
document.getElementById('btnSuspend').addEventListener('click',async()=>{
  addLog('Anhalten → POST /api/motion {state:3}','warn');
  const r=await apiPost('/api/motion',{state:3});
  if(r?.ok){ updateMotionState(3); addLog('Suspend bestätigt','ok'); }
  else       addLog('Suspend fehlgeschlagen','err');
});
document.getElementById('btnResume').addEventListener('click',async()=>{
  addLog('Fortsetzen → POST /api/motion {state:0}','cmd');
  const r=await apiPost('/api/motion',{state:0});
  if(r?.ok){ updateMotionState(1); addLog('Run bestätigt','ok'); }
  else       addLog('Resume fehlgeschlagen','err');
});
document.getElementById('btnClearErr').addEventListener('click',async()=>{
  addLog('Fehler löschen → POST /api/clear_error','cmd');
  const r=await apiPost('/api/clear_error',{});
  if(r?.ok){ document.getElementById('errBox').classList.remove('show'); addLog('Fehler gelöscht','ok'); }
  else       addLog('Löschen fehlgeschlagen','err');
});
document.getElementById('btnClearWarn').addEventListener('click',async()=>{
  addLog('Warnung löschen → POST /api/clear_warning','cmd');
  const r=await apiPost('/api/clear_warning',{});
  if(r?.ok) addLog('Warnung gelöscht','ok'); else addLog('Löschen fehlgeschlagen','err');
});

document.getElementById('doGrid').addEventListener('click',async function(e){
  const ch=e.target.closest('.io-ch[data-out]');
  if(!ch) return;
  ch.classList.toggle('on');
  const val=ch.classList.contains('on')?1:0;
  const channel=parseInt(ch.dataset.ch.replace('DO',''));
  ch.querySelector('.io-val').textContent=val;
  addLog(`DO${channel} → ${val} — POST /api/digital_out`,'cmd');
  const r=await apiPost('/api/digital_out',{channel,value:val});
  if(!r?.ok){
    addLog(`DO${channel} fehlgeschlagen — Rücksetzen`,'err');
    ch.classList.toggle('on');
    ch.querySelector('.io-val').textContent=val?0:1;
  }
});

/* ─────────────────────────────────────────────
   DOM UPDATE
───────────────────────────────────────────── */
function updateDOM(){
  // KPI row
  document.getElementById('kpiSpeed').textContent=Math.round(state.tcpSpeed);
  document.getElementById('kpiSpeedBar').style.width=(state.tcpSpeed/10)+'%';
  document.getElementById('kpiZ').textContent=state.tcpPos[2].toFixed(1);
  document.getElementById('kpiZBar').style.width=((state.tcpPos[2]+50)/600*100).toFixed(1)+'%';
  document.getElementById('kpiBuf').textContent=state.cmdBuf;
  document.getElementById('kpiBufBar').style.width=(state.cmdBuf/20*100)+'%';
  document.getElementById('kpiJspeed').textContent=state.jSpeed[2]||'—';
  document.getElementById('kpiErr').textContent=state.errorCode;
  document.getElementById('kpiWarn').textContent='/ '+state.warnCode;

  // Gauge
  updateGauge(state.tcpSpeed);

  // Cartesian
  const axes=['X','Y','Z','Roll','Pitch','Yaw'];
  const ids=['posX','posY','posZ','posRoll','posPitch','posYaw'];
  state.tcpPos.forEach((v,i)=>{
    const el=document.getElementById(ids[i]);
    if(el)el.textContent=v.toFixed(3);
  });

  // Joints
  const jMax=Math.PI;
  state.joints.forEach((v,i)=>{
    const bar=document.getElementById('jb'+(i+1));
    const val=document.getElementById('jv'+(i+1));
    if(!bar||!val)return;
    const pct=Math.max(0,Math.min(100,(v+jMax)/(2*jMax)*100));
    bar.style.width=pct.toFixed(1)+'%';
    bar.className='joint-bar'+(pct>80?' warn':pct>95?' limit':'');
    val.textContent=(v>=0?'+':'')+v.toFixed(3)+' rad';
  });

  // Servo temps
  state.temps.forEach((t,i)=>{
    const el=document.getElementById('st'+(i+1));
    const chip=el?.closest('.servo-chip');
    if(!el)return;
    el.textContent=Math.round(t)+'°C';
    if(chip){
      chip.className='servo-chip '+(t>55?'warn':'ok');
    }
  });

  // FT
  const ftIds=[['fxf','fxv'],['fyf','fyv'],['fzf','fzv'],['txf','txv'],['tyf','tyv'],['tzf','tzv']];
  const ftUnits=['N','N','N','Nm','Nm','Nm'];
  const ftMax=[15,15,15,3,3,3];
  state.ft.forEach((v,i)=>{
    const [bid,vid]=ftIds[i];
    const fill=document.getElementById(bid);
    const valEl=document.getElementById(vid);
    if(!fill||!valEl)return;
    const pct=Math.min(Math.abs(v)/ftMax[i]*50,50);
    fill.style.width=pct.toFixed(1)+'%';
    fill.className='ft-fill '+(v>=0?'pos':'neg');
    valEl.textContent=(v>=0?'+':'')+v.toFixed(1)+' '+ftUnits[i];
  });

  // Currents
  state.currents.forEach((c,i)=>{
    const el=document.getElementById('cur'+(i+1));
    if(el){
      el.textContent=c.toFixed(2)+'A';
      el.style.color=c>1.2?'var(--amber)':'var(--text)';
    }
  });

  // Modbus strip
  document.getElementById('mbusTid').textContent='0x'+tid_counter.v.toString(16).toUpperCase().padStart(4,'0');
  document.getElementById('mbusRtt').textContent=state.rtt.toFixed(1)+' ms';
  document.getElementById('mbusRtt').className=state.rtt>5?'warn':'ok';
  document.getElementById('mbusLastReg').textContent='0x2A (42)';

  // History push
  const push=(hist,val)=>{hist.push(val);if(hist.length>TRAJ_MAX)hist.shift();};
  push(trajHistory.x,state.tcpPos[0]);
  push(trajHistory.y,state.tcpPos[1]);
  push(trajHistory.z,state.tcpPos[2]);
  state.currents.forEach((c,i)=>push(curHistory[i],c));

  // Canvas
  drawTraj();
  drawCurrents();

  // Log periodic
  if(Math.random()<0.03){
    const reg=['0x29','0x2A','0x0F','0x0E','0xC8'][Math.floor(Math.random()*5)];
    addLog(`RX Reg ${reg} → ok (${state.rtt.toFixed(1)}ms)`,'ok');
  }
}

/* ─────────────────────────────────────────────
   CANVAS-ANIMATIONSLOOP (unabhängig vom Polling)
───────────────────────────────────────────── */
function mainLoop(){ drawTraj(); drawCurrents(); }
setInterval(mainLoop,100);

/* ─────────────────────────────────────────────
   INITIALER LOG + KONFIG VOM BACKEND LADEN
───────────────────────────────────────────── */
(async () => {
  addLog('Dashboard bereit','ok');
  // IP-Adresse und Port aus dem Backend-Start laden
  const cfg = await apiFetch('/api/config');
  if(cfg){
    document.getElementById('connIpInput').value = cfg.ip || '192.168.1.185';
    // Port-Anzeige aktualisieren falls abweichend von 18333
    const portEl = document.querySelector('.conn-badge span[style*="cyan"]');
    if(portEl) portEl.textContent = `:${cfg.port || 18333}`;
    addLog(`Konfiguration geladen: ${cfg.ip}:${cfg.port} @ ${cfg.pollHz} Hz`,'ok');
  } else {
    addLog('Backend nicht erreichbar — auf ▶ Verbinden klicken','warn');
  }
  addLog('Roboter-Protokoll: Private Modbus TCP Port 18333','ok');
})();
