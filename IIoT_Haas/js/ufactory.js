/**
 * ufactory.js  –  UFactory xArm Robot Tab
 * =========================================
 * Communicates with backend/ufactory_robot.py (default port 8081).
 * Uses Three.js (loaded via CDN in index.html) for a live 3D
 * visualisation of the robot pose, updated via forward kinematics.
 *
 * Public entry point called from index.html:
 *   initUFactory()   – called once when the tab first becomes visible
 */

(function (global) {
  'use strict';

  // ── Constants ──────────────────────────────────────────────────────────────
  const POLL_MS   = 150;    // frontend poll interval for robot state
  const ROBOT_API = () => (localStorage.getItem('ufactoryBackendUrl') || 'http://localhost:8081');

  // UFactory xArm 850 (6-DOF) DH parameters  [theta_offset_rad, d_m, a_m, alpha_rad]
  // Distances converted to metres (÷1000) so they match Three.js scene units.
  const DH = [
    [0,            0.267,   0.000,  Math.PI / 2],   // J1
    [-Math.PI / 2, 0.000,   0.289,  0            ],   // J2
    [ Math.PI / 2, 0.000,   0.0775, Math.PI / 2],   // J3
    [0,            0.3425,  0.000, -Math.PI / 2],   // J4
    [0,            0.000,   0.000,  Math.PI / 2],   // J5
    [0,            0.097,   0.000,  0            ],   // J6
  ];

  // Joint display colours (hex)
  const JOINT_COLORS = [0x0dcaf0, 0x20c997, 0x0dcaf0, 0x20c997, 0x0dcaf0, 0x20c997];
  const LINK_COLOR   = 0x1a4a6a;
  const BASE_COLOR   = 0x2a3a4a;
  const EE_COLOR     = 0xffd700;

  // Joint limits for xArm 850 (degrees)
  const JOINT_LIMITS = [
    [-360, 360],   // J1
    [-118, 120],   // J2
    [-225,  11],   // J3
    [-360, 360],   // J4
    [-97,  180],   // J5
    [-360, 360],   // J6
  ];

  // ── State ──────────────────────────────────────────────────────────────────
  let robotState    = null;
  let pollTimer     = null;
  let threeReady    = false;
  let tabInited     = false;

  // Three.js objects
  let renderer, scene, camera, orbitControls;
  let jointSpheres  = [];   // 7 spheres: base origin + J1..J6
  let linkMeshes    = [];   // 6 cylinder meshes between consecutive joints
  let eeMesh        = null; // end-effector cone
  let trailPoints   = [];   // last N TCP positions for trajectory trail
  let trailLine     = null;

  // ── Utility ────────────────────────────────────────────────────────────────
  function fetchJSON(url, opts = {}) {
    return fetch(url, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
    }).then(r => r.json());
  }

  // ── Forward Kinematics ─────────────────────────────────────────────────────
  /**
   * Returns an array of 7 THREE.Vector3:
   *   [base_origin, p_after_J1, p_after_J2, ..., p_after_J6 (TCP)]
   * all in Three.js world units (metres).
   */
  function computeFK(angles_deg) {
    const THREE = global.THREE;
    if (!THREE) return null;

    // 4×4 homogeneous transform (row-major, like THREE.Matrix4)
    function dh4x4(theta, d, a, alpha) {
      const ct = Math.cos(theta), st = Math.sin(theta);
      const ca = Math.cos(alpha), sa = Math.sin(alpha);
      const m = new THREE.Matrix4();
      m.set(
        ct, -st * ca,  st * sa,  a * ct,
        st,  ct * ca, -ct * sa,  a * st,
         0,  sa,       ca,       d,
         0,  0,        0,        1
      );
      return m;
    }

    const positions = [new THREE.Vector3(0, 0, 0)];
    let T = new THREE.Matrix4(); // identity

    for (let i = 0; i < 6; i++) {
      const theta = angles_deg[i] * (Math.PI / 180) + DH[i][0];
      const d     = DH[i][1];
      const a     = DH[i][2];
      const alpha = DH[i][3];
      const Ti    = dh4x4(theta, d, a, alpha);
      T = new THREE.Matrix4().multiplyMatrices(T, Ti);
      const pos = new THREE.Vector3().setFromMatrixPosition(T);
      positions.push(pos);
    }
    return positions;   // length 7
  }

  // ── Three.js Scene ─────────────────────────────────────────────────────────
  function initThreeJS() {
    const THREE = global.THREE;
    if (!THREE || !THREE.OrbitControls) { return; }

    const canvas = document.getElementById('robotCanvas');
    if (!canvas) return;

    renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    renderer.shadowMap.enabled = true;

    scene  = new THREE.Scene();
    scene.background = new THREE.Color(0x0b0f18);

    const w = canvas.parentElement.clientWidth  || 600;
    const h = Math.min(Math.max(w * 0.65, 380), 520);
    renderer.setSize(w, h, false);

    camera = new THREE.PerspectiveCamera(45, w / h, 0.01, 50);
    camera.position.set(1.4, 1.0, 1.4);
    camera.lookAt(0, 0.35, 0);

    orbitControls = new THREE.OrbitControls(camera, renderer.domElement);
    orbitControls.target.set(0, 0.35, 0);
    orbitControls.enableDamping = true;
    orbitControls.dampingFactor = 0.06;
    orbitControls.minDistance   = 0.2;
    orbitControls.maxDistance   = 4.0;

    // Lights
    scene.add(new THREE.AmbientLight(0x405060, 0.6));
    const sun = new THREE.DirectionalLight(0xffffff, 1.2);
    sun.position.set(2, 4, 3);
    sun.castShadow = true;
    scene.add(sun);
    const fill = new THREE.DirectionalLight(0x0dcaf0, 0.25);
    fill.position.set(-2, 1, -2);
    scene.add(fill);

    // Floor grid
    const grid = new THREE.GridHelper(4, 40, 0x1e2d40, 0x131d28);
    scene.add(grid);

    // World-axis indicator (tiny arrows at origin)
    const axLen = 0.12;
    [
      [axLen,0,0, 0xff4444],
      [0,axLen,0, 0x44ff44],
      [0,0,axLen, 0x4488ff],
    ].forEach(([x,y,z,c]) => {
      const geo = new THREE.CylinderGeometry(0.003, 0.003, axLen, 6);
      const mat = new THREE.MeshBasicMaterial({ color: c });
      const m   = new THREE.Mesh(geo, mat);
      m.position.set(x/2, y/2, z/2);
      if (x) m.rotation.z = -Math.PI/2;
      if (z) m.rotation.x =  Math.PI/2;
      scene.add(m);
    });

    // Robot base platform
    const baseGeo = new THREE.CylinderGeometry(0.075, 0.085, 0.03, 32);
    const baseMat = new THREE.MeshPhongMaterial({ color: BASE_COLOR, shininess: 60 });
    const baseMesh = new THREE.Mesh(baseGeo, baseMat);
    baseMesh.position.y = 0.015;
    baseMesh.receiveShadow = true;
    scene.add(baseMesh);

    // Joint spheres (7: origin + 6 joints)
    jointSpheres = [];
    for (let i = 0; i <= 6; i++) {
      const r   = i === 0 ? 0.030 : (i === 6 ? 0.022 : 0.028);
      const col = i === 0 ? BASE_COLOR : (i === 6 ? EE_COLOR : JOINT_COLORS[i - 1]);
      const geo = new THREE.SphereGeometry(r, 18, 12);
      const mat = new THREE.MeshPhongMaterial({
        color:    col,
        emissive: new THREE.Color(col).multiplyScalar(0.2),
        shininess: 120,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.castShadow = true;
      scene.add(mesh);
      jointSpheres.push(mesh);
    }

    // Link cylinders (6: connecting consecutive joint spheres)
    linkMeshes = [];
    for (let i = 0; i < 6; i++) {
      const geo = new THREE.CylinderGeometry(0.020, 0.024, 1, 12);
      const mat = new THREE.MeshPhongMaterial({ color: LINK_COLOR, shininess: 80 });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.castShadow = true;
      scene.add(mesh);
      linkMeshes.push(mesh);
    }

    // End-effector cone
    const eeGeo = new THREE.ConeGeometry(0.018, 0.06, 8);
    const eeMat = new THREE.MeshPhongMaterial({
      color: EE_COLOR, emissive: 0x443300, shininess: 150,
    });
    eeMesh = new THREE.Mesh(eeGeo, eeMat);
    scene.add(eeMesh);

    // Trajectory trail (line)
    const trailGeo = new THREE.BufferGeometry();
    trailGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(300 * 3), 3));
    const trailMat = new THREE.LineBasicMaterial({ color: 0x0dcaf0, opacity: 0.5, transparent: true });
    trailLine = new THREE.Line(trailGeo, trailMat);
    scene.add(trailLine);

    window.addEventListener('resize', onResize);
    threeReady = true;
    animate();
    log('Three.js scene ready');
  }

  function onResize() {
    if (!renderer) return;
    const canvas = document.getElementById('robotCanvas');
    if (!canvas) return;
    const w = canvas.parentElement.clientWidth || 600;
    const h = Math.min(Math.max(w * 0.65, 380), 520);
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  function animate() {
    requestAnimationFrame(animate);
    if (orbitControls) orbitControls.update();
    if (renderer && scene && camera) renderer.render(scene, camera);
  }

  // ── Update Scene from Joint Angles ─────────────────────────────────────────
  function updateRobotPose(angles_deg) {
    if (!threeReady) return;
    const THREE = global.THREE;
    const positions = computeFK(angles_deg);
    if (!positions) return;

    // Place joint spheres
    for (let i = 0; i <= 6; i++) {
      if (jointSpheres[i]) jointSpheres[i].position.copy(positions[i]);
    }

    // Position + orient link cylinders between consecutive joints
    for (let i = 0; i < 6; i++) {
      const p0  = positions[i];
      const p1  = positions[i + 1];
      const dir = new THREE.Vector3().subVectors(p1, p0);
      const len = dir.length();
      if (len < 0.001) { linkMeshes[i].visible = false; continue; }

      linkMeshes[i].visible = true;
      linkMeshes[i].position.copy(p0).addScaledVector(dir, 0.5);
      linkMeshes[i].scale.y = len;
      linkMeshes[i].quaternion.setFromUnitVectors(
        new THREE.Vector3(0, 1, 0),
        dir.clone().normalize()
      );
    }

    // End-effector cone at TCP, pointing along last link direction
    const tcp = positions[6];
    eeMesh.position.copy(tcp);
    const lastDir = new THREE.Vector3().subVectors(tcp, positions[5]);
    if (lastDir.length() > 0.001) {
      eeMesh.quaternion.setFromUnitVectors(
        new THREE.Vector3(0, 1, 0),
        lastDir.normalize()
      );
    }

    // Trajectory trail
    trailPoints.push(tcp.clone());
    if (trailPoints.length > 300) trailPoints.shift();
    const posArr = trailLine.geometry.attributes.position;
    const n = trailPoints.length;
    for (let i = 0; i < n; i++) {
      posArr.setXYZ(i, trailPoints[i].x, trailPoints[i].y, trailPoints[i].z);
    }
    posArr.needsUpdate = true;
    trailLine.geometry.setDrawRange(0, n);
  }

  // ── API helpers ────────────────────────────────────────────────────────────
  function apiBase() { return ROBOT_API(); }

  async function getRobotState() {
    try {
      return await fetchJSON(apiBase() + '/api/robot/state');
    } catch {
      return null;
    }
  }

  async function connectRobot(ip, port, axis) {
    return fetchJSON(apiBase() + '/api/robot/connect', {
      method: 'POST',
      body: JSON.stringify({ ip, port: parseInt(port), axis: parseInt(axis) }),
    });
  }

  async function disconnectRobot() {
    return fetchJSON(apiBase() + '/api/robot/disconnect', { method: 'POST' });
  }

  async function moveJoint(angles, speed) {
    return fetchJSON(apiBase() + '/api/robot/move/joint', {
      method: 'POST',
      body: JSON.stringify({ angles, speed: parseFloat(speed) }),
    });
  }

  async function moveCartesian(x, y, z, roll, pitch, yaw, speed) {
    return fetchJSON(apiBase() + '/api/robot/move/cartesian', {
      method: 'POST',
      body: JSON.stringify({ x, y, z, roll, pitch, yaw, speed: parseFloat(speed) }),
    });
  }

  async function jogAxis(type, axis, step) {
    return fetchJSON(apiBase() + '/api/robot/jog', {
      method: 'POST',
      body: JSON.stringify({ type, axis, step }),
    });
  }

  async function stopRobot() {
    return fetchJSON(apiBase() + '/api/robot/stop', { method: 'POST' });
  }

  async function enableMotion(flag) {
    return fetchJSON(apiBase() + '/api/robot/enable', {
      method: 'POST',
      body: JSON.stringify({ enable: flag }),
    });
  }

  async function resetErrors() {
    return fetchJSON(apiBase() + '/api/robot/reset', { method: 'POST' });
  }

  async function goHome() {
    return fetchJSON(apiBase() + '/api/robot/home', { method: 'POST' });
  }

  // ── Polling ────────────────────────────────────────────────────────────────
  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      const st = await getRobotState();
      if (st) applyState(st);
    }, POLL_MS);
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  // ── Apply State to UI ──────────────────────────────────────────────────────
  const STATE_LABELS = { 0: 'READY', 1: 'MOVING', 2: 'SLEEP', 3: 'PAUSED', 4: 'STOPPED' };

  function applyState(st) {
    robotState = st;
    const connected = st.connected;

    // Connection badge
    const badge = document.getElementById('uf-conn-badge');
    if (badge) {
      badge.className = 'badge ' + (connected ? 'bg-success' : 'bg-secondary');
      badge.textContent = connected ? '● CONNECTED' : '○ DISCONNECTED';
    }

    // Robot state badge
    const stBadge = document.getElementById('uf-state-badge');
    if (stBadge) {
      const label = STATE_LABELS[st.state] || `STATE ${st.state}`;
      stBadge.textContent = label;
      stBadge.className = 'badge ' + (st.is_moving ? 'bg-warning text-dark' : 'bg-info text-dark');
    }

    // Error / warn
    const errDiv = document.getElementById('uf-error-bar');
    if (errDiv) {
      if (st.error_code) {
        errDiv.className = 'alert alert-danger py-1 mb-2';
        errDiv.textContent = `Error code ${st.error_code}  |  Warn ${st.warn_code}`;
        errDiv.style.display = '';
      } else {
        errDiv.style.display = 'none';
      }
    }

    if (!connected) return;

    // Joint angle readout + sliders
    const angles = st.joint_angles || [];
    for (let i = 0; i < 6; i++) {
      const val = angles[i] != null ? angles[i] : 0;

      const disp = document.getElementById(`uf-j${i + 1}-val`);
      if (disp) disp.textContent = val.toFixed(1) + '°';

      // Only sync slider if user is NOT dragging it
      const slider = document.getElementById(`uf-j${i + 1}-slider`);
      if (slider && !slider.dataset.dragging) slider.value = val;
      const inp = document.getElementById(`uf-j${i + 1}-input`);
      if (inp && document.activeElement !== inp) inp.value = val.toFixed(1);
    }

    // TCP position readout
    const pos   = st.position || [];
    const pKeys = ['x','y','z','roll','pitch','yaw'];
    pKeys.forEach((k, i) => {
      const el = document.getElementById(`uf-pos-${k}`);
      if (el) el.textContent = (pos[i] != null ? pos[i] : 0).toFixed(2);
      const inp = document.getElementById(`uf-cart-${k}`);
      if (inp && document.activeElement !== inp)
        inp.value = (pos[i] != null ? pos[i] : 0).toFixed(1);
    });

    // Version / SN (one-time)
    const verEl = document.getElementById('uf-version');
    if (verEl && st.version) verEl.textContent = `FW ${st.version}` + (st.sn ? `  SN ${st.sn}` : '');

    // 3D visualisation
    if (angles.length >= 6) updateRobotPose(angles);
  }

  // ── Logging helper ─────────────────────────────────────────────────────────
  function log(msg) {
    const el = document.getElementById('uf-log');
    if (!el) return;
    const ts = new Date().toLocaleTimeString();
    el.textContent = `[${ts}] ${msg}\n` + el.textContent.slice(0, 1200);
  }

  // ── Event wiring ───────────────────────────────────────────────────────────
  function wireEvents() {
    // Backend URL save
    const urlInp = document.getElementById('uf-backend-url');
    if (urlInp) {
      urlInp.value = ROBOT_API();
      urlInp.addEventListener('change', () => {
        localStorage.setItem('ufactoryBackendUrl', urlInp.value.trim());
        log('Backend URL updated → ' + urlInp.value.trim());
      });
    }

    // Connect button
    document.getElementById('uf-btn-connect')?.addEventListener('click', async () => {
      const ip   = document.getElementById('uf-ip')?.value.trim() || '';
      const port = document.getElementById('uf-port')?.value.trim() || '502';
      const axis = document.getElementById('uf-axis')?.value || '6';
      if (!ip) { log('Enter robot IP first'); return; }
      log(`Connecting to ${ip}:${port} …`);
      try {
        const res = await connectRobot(ip, port, axis);
        log(res.success ? `Connected  FW=${res.version}` : `Error: ${res.error}`);
      } catch (e) {
        log('Backend unreachable — is ufactory_robot.py running?');
      }
    });

    // Disconnect
    document.getElementById('uf-btn-disconnect')?.addEventListener('click', async () => {
      await disconnectRobot();
      log('Disconnected');
    });

    // Stop (emergency)
    document.getElementById('uf-btn-stop')?.addEventListener('click', async () => {
      await stopRobot();
      log('STOP sent');
    });

    // Enable / Home / Reset
    document.getElementById('uf-btn-enable')?.addEventListener('click', async () => {
      await enableMotion(true); log('Motion enabled');
    });
    document.getElementById('uf-btn-home')?.addEventListener('click', async () => {
      await goHome(); log('Go home sent');
    });
    document.getElementById('uf-btn-reset')?.addEventListener('click', async () => {
      await resetErrors(); log('Errors cleared');
    });

    // Clear trail
    document.getElementById('uf-btn-clear-trail')?.addEventListener('click', () => {
      trailPoints = [];
      log('Trajectory trail cleared');
    });

    // ── Joint sliders ────────────────────────────────────────────────────────
    for (let i = 1; i <= 6; i++) {
      const slider = document.getElementById(`uf-j${i}-slider`);
      const input  = document.getElementById(`uf-j${i}-input`);
      if (!slider || !input) continue;

      slider.addEventListener('mousedown', () => { slider.dataset.dragging = '1'; });
      slider.addEventListener('touchstart', () => { slider.dataset.dragging = '1'; });
      slider.addEventListener('mouseup', () => { delete slider.dataset.dragging; });
      slider.addEventListener('touchend', () => { delete slider.dataset.dragging; });

      slider.addEventListener('input', () => {
        input.value = parseFloat(slider.value).toFixed(1);
        // Live preview in 3D without sending command
        if (robotState) {
          const a = (robotState.joint_angles || [0,0,0,0,0,0]).map(Number);
          a[i - 1] = parseFloat(slider.value);
          updateRobotPose(a);
        }
      });
      input.addEventListener('input', () => {
        slider.value = input.value;
      });
    }

    // Send all joints button
    document.getElementById('uf-btn-send-joints')?.addEventListener('click', async () => {
      const angles = [];
      for (let i = 1; i <= 6; i++) {
        const v = parseFloat(document.getElementById(`uf-j${i}-input`)?.value || 0);
        angles.push(v);
      }
      const speed = parseFloat(document.getElementById('uf-joint-speed')?.value || 50);
      log(`Move joints → [${angles.map(a => a.toFixed(1)).join(', ')}]`);
      const res = await moveJoint(angles, speed);
      log(res.success ? 'Joint move sent' : `Error: ${res.error}`);
    });

    // ── Cartesian send ────────────────────────────────────────────────────────
    document.getElementById('uf-btn-send-cart')?.addEventListener('click', async () => {
      const get = id => parseFloat(document.getElementById(id)?.value || 0);
      const speed = parseFloat(document.getElementById('uf-cart-speed')?.value || 100);
      log(`Move cartesian → x=${get('uf-cart-x')} y=${get('uf-cart-y')} z=${get('uf-cart-z')}`);
      const res = await moveCartesian(
        get('uf-cart-x'), get('uf-cart-y'), get('uf-cart-z'),
        get('uf-cart-roll'), get('uf-cart-pitch'), get('uf-cart-yaw'),
        speed
      );
      log(res.success ? 'Cartesian move sent' : `Error: ${res.error}`);
    });

    // ── Jog buttons ───────────────────────────────────────────────────────────
    document.querySelectorAll('[data-jog]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const [type, axis, sign] = btn.dataset.jog.split(',');
        const stepEl = document.getElementById(type === 'joint' ? 'uf-jog-step-j' : 'uf-jog-step-c');
        const step   = parseFloat(stepEl?.value || 5) * parseFloat(sign);
        const res = await jogAxis(type, parseInt(axis), step);
        if (!res.success) log(`Jog error: ${res.error}`);
      });
    });
  }

  // ── Public init ────────────────────────────────────────────────────────────
  function initUFactory() {
    if (tabInited) return;
    tabInited = true;

    wireEvents();
    initThreeJS();
    startPolling();
    log('UFactory tab initialised — enter robot IP and click Connect');

    // Initial 3D preview at zero angles
    setTimeout(() => updateRobotPose([0, 0, 0, 0, 0, 0]), 100);
  }

  // Expose to global scope
  global.initUFactory = initUFactory;

}(window));
