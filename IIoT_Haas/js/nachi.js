/**
 * nachi.js — Nachi MZ01 Robot Tab
 * ================================
 * Three.js 3-D visualisation + REST backend communication for the Nachi MZ01
 * 6-DOF robot arm.  Loaded lazily when the NACHI tab is first opened.
 *
 * DH parameters (approximate – verify against STEP CAD files):
 *   Standard DH convention: T = Rz(θ) · Tz(d) · Tx(a) · Rx(α)
 *   Units: metres for d/a, radians for α
 *
 *   Joint | θ_offset | d      | a      | α
 *   ------+----------+--------+--------+-------
 *     1   |   0      | 0.337  | 0.000  | +π/2
 *     2   |  -π/2    | 0.000  | 0.250  |  0
 *     3   |   0      | 0.000  | 0.025  | +π/2
 *     4   |   0      | 0.280  | 0.000  | -π/2
 *     5   |   0      | 0.000  | 0.000  | +π/2
 *     6   |   0      | 0.090  | 0.000  |  0
 */

(function () {
    'use strict';

    // ── DH table ────────────────────────────────────────────────────────────
    const π = Math.PI;
    const DH = [
        // [theta_offset, d_m, a_m, alpha_rad]
        [0,      0.337, 0.000,  π / 2],
        [-π / 2, 0.000, 0.250,  0    ],
        [0,      0.000, 0.025,  π / 2],
        [0,      0.280, 0.000, -π / 2],
        [0,      0.000, 0.000,  π / 2],
        [0,      0.090, 0.000,  0    ],
    ];

    // Joint limits [min, max] in degrees
    const JOINT_LIMITS = [
        [-170, 170],
        [-90,  85 ],
        [-50,  90 ],
        [-145, 145],
        [-125, 125],
        [-360, 360],
    ];

    // ── State ────────────────────────────────────────────────────────────────
    let _initialized = false;
    let _scene, _camera, _renderer, _controls;
    let _jointSpheres = [];
    let _linkMeshes   = [];
    let _tcpMesh      = null;
    let _trailPoints  = [];
    let _trailLine    = null;
    let _animFrame    = null;
    let _pollInterval = null;

    let _backendUrl   = 'http://localhost:8082';
    let _sliderDragging = false;
    let _lastAngles   = [0, 0, 0, 0, 0, 0];
    let _isConnected  = false;

    // ── DH forward kinematics ────────────────────────────────────────────────

    function dh4x4(theta, d, a, alpha) {
        const ct = Math.cos(theta), st = Math.sin(theta);
        const ca = Math.cos(alpha), sa = Math.sin(alpha);
        const m = new THREE.Matrix4();
        m.set(
            ct, -st * ca,  st * sa, a * ct,
            st,  ct * ca, -ct * sa, a * st,
             0,       sa,       ca,      d,
             0,        0,        0,      1
        );
        return m;
    }

    /** Returns array of 7 THREE.Vector3: base + J1..J6 origins (in metres). */
    function computeFK(angles_deg) {
        const T = new THREE.Matrix4(); // identity = world frame
        const pts = [new THREE.Vector3(0, 0, 0)];
        for (let i = 0; i < 6; i++) {
            const [toff, d, a, alpha] = DH[i];
            const theta = toff + angles_deg[i] * Math.PI / 180;
            const Ti = dh4x4(theta, d, a, alpha);
            T.multiply(Ti);
            const pos = new THREE.Vector3();
            pos.setFromMatrixPosition(T);
            pts.push(pos.clone());
        }
        return pts;
    }

    // ── Three.js scene ───────────────────────────────────────────────────────

    function initScene() {
        const canvas = document.getElementById('nachiCanvas');
        if (!canvas) return;

        _renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
        _renderer.setPixelRatio(window.devicePixelRatio);
        _renderer.setClearColor(0x07090f);

        _scene = new THREE.Scene();

        _camera = new THREE.PerspectiveCamera(45, 1, 0.01, 10);
        _camera.position.set(1.2, 0.8, 1.2);
        _camera.lookAt(0, 0.4, 0);

        // Lights
        _scene.add(new THREE.AmbientLight(0x334455, 1.5));
        const dir = new THREE.DirectionalLight(0xffffff, 1.2);
        dir.position.set(2, 3, 2);
        _scene.add(dir);

        // Grid
        const grid = new THREE.GridHelper(2, 20, 0x1e2d40, 0x1e2d40);
        _scene.add(grid);

        // Base platform
        const baseMat = new THREE.MeshStandardMaterial({ color: 0x1a2535 });
        const base = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.10, 0.04, 32), baseMat);
        base.position.set(0, 0.02, 0);
        _scene.add(base);

        // Joint spheres
        const jMat = new THREE.MeshStandardMaterial({ color: 0x0dcaf0, emissive: 0x063040 });
        for (let i = 0; i < 7; i++) {
            const s = new THREE.Mesh(new THREE.SphereGeometry(0.022, 12, 12), jMat.clone());
            _scene.add(s);
            _jointSpheres.push(s);
        }

        // Link cylinders (6 links, connecting adjacent spheres)
        const lMat = new THREE.MeshStandardMaterial({ color: 0x2a4a6a });
        for (let i = 0; i < 6; i++) {
            const c = new THREE.Mesh(
                new THREE.CylinderGeometry(0.014, 0.014, 1, 8),
                lMat.clone()
            );
            _scene.add(c);
            _linkMeshes.push(c);
        }

        // TCP indicator (small sphere, bright colour)
        _tcpMesh = new THREE.Mesh(
            new THREE.SphereGeometry(0.018, 12, 12),
            new THREE.MeshStandardMaterial({ color: 0xffd700, emissive: 0x443300 })
        );
        _scene.add(_tcpMesh);

        // Trail (LineSegments, built lazily)
        _trailLine = new THREE.Line(
            new THREE.BufferGeometry(),
            new THREE.LineBasicMaterial({ color: 0x00ffa0, opacity: 0.5, transparent: true })
        );
        _scene.add(_trailLine);

        // OrbitControls
        _controls = new THREE.OrbitControls(_camera, _renderer.domElement);
        _controls.enableDamping = true;
        _controls.target.set(0, 0.4, 0);

        resizeRenderer();
        window.addEventListener('resize', resizeRenderer);

        animate();
        updateRobotPose(_lastAngles);
    }

    function resizeRenderer() {
        const canvas = document.getElementById('nachiCanvas');
        if (!canvas) return;
        const w = canvas.clientWidth;
        const h = canvas.clientHeight || 320;
        _renderer.setSize(w, h, false);
        _camera.aspect = w / h;
        _camera.updateProjectionMatrix();
    }

    function animate() {
        _animFrame = requestAnimationFrame(animate);
        _controls.update();
        _renderer.render(_scene, _camera);
    }

    function updateRobotPose(angles_deg) {
        const pts = computeFK(angles_deg);

        // Position joint spheres
        for (let i = 0; i < 7; i++) {
            _jointSpheres[i].position.copy(pts[i]);
        }

        // Position & orient link cylinders
        for (let i = 0; i < 6; i++) {
            const a = pts[i], b = pts[i + 1];
            const mid = new THREE.Vector3().addVectors(a, b).multiplyScalar(0.5);
            const len = a.distanceTo(b);
            _linkMeshes[i].position.copy(mid);
            if (len > 0.001) {
                const dir = new THREE.Vector3().subVectors(b, a).normalize();
                const quat = new THREE.Quaternion();
                quat.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
                _linkMeshes[i].quaternion.copy(quat);
                _linkMeshes[i].scale.set(1, len, 1);
            }
        }

        // TCP
        const tcp = pts[pts.length - 1];
        _tcpMesh.position.copy(tcp);

        // Trail
        _trailPoints.push(tcp.clone());
        if (_trailPoints.length > 300) _trailPoints.shift();
        const positions = new Float32Array(_trailPoints.length * 3);
        _trailPoints.forEach((p, i) => {
            positions[i * 3]     = p.x;
            positions[i * 3 + 1] = p.y;
            positions[i * 3 + 2] = p.z;
        });
        _trailLine.geometry.setAttribute('position',
            new THREE.BufferAttribute(positions, 3));
        _trailLine.geometry.setDrawRange(0, _trailPoints.length);
    }

    // ── UI helpers ───────────────────────────────────────────────────────────

    function nachiLog(msg) {
        const el = document.getElementById('nachi-log');
        if (!el) return;
        const ts = new Date().toLocaleTimeString();
        el.textContent = `[${ts}] ${msg}\n` + el.textContent;
    }

    function setConnStatus(connected) {
        _isConnected = connected;
        const dot  = document.getElementById('nachi-conn-dot');
        const txt  = document.getElementById('nachi-conn-txt');
        if (dot) dot.className = connected ? 'nachi-dot connected' : 'nachi-dot';
        if (txt) txt.textContent = connected ? 'CONNECTED' : 'DISCONNECTED';
    }

    function updateReadouts(state) {
        const pos = state.position || [0,0,0,0,0,0];
        ['x','y','z','roll','pitch','yaw'].forEach((k, i) => {
            const el = document.getElementById(`nachi-pos-${k}`);
            if (el) el.textContent = pos[i].toFixed(2);
        });

        const ang = state.joint_angles || [0,0,0,0,0,0];
        ang.forEach((v, i) => {
            const el = document.getElementById(`nachi-j${i + 1}-val`);
            if (el) el.textContent = v.toFixed(1) + '°';
            if (!_sliderDragging) {
                const sl = document.getElementById(`nachi-sl-j${i + 1}`);
                if (sl) sl.value = v;
            }
        });
    }

    // ── Backend communication ────────────────────────────────────────────────

    async function apiFetch(path, method = 'GET', body = null) {
        const opts = {
            method,
            headers: { 'Content-Type': 'application/json' },
        };
        if (body) opts.body = JSON.stringify(body);
        const r = await fetch(_backendUrl + path, opts);
        return r.json();
    }

    async function pollState() {
        if (!_isConnected) return;
        try {
            const s = await apiFetch('/api/robot/state');
            if (!s.connected) { setConnStatus(false); return; }
            updateReadouts(s);
            if (!_sliderDragging) {
                _lastAngles = s.joint_angles || _lastAngles;
                updateRobotPose(_lastAngles);
            }
        } catch (_) { /* network hiccup, ignore */ }
    }

    // ── Public UI action handlers ────────────────────────────────────────────

    window.nachiConnect = async function () {
        const url = document.getElementById('nachi-backend-url')?.value?.trim() || _backendUrl;
        const ip  = document.getElementById('nachi-robot-ip')?.value?.trim()  || '';
        const port = parseInt(document.getElementById('nachi-robot-port')?.value) || 0;
        _backendUrl = url;
        localStorage.setItem('nachiBackendUrl', url);

        nachiLog(`Connecting to ${ip}:${port} via ${url} …`);
        try {
            const r = await apiFetch('/api/robot/connect', 'POST', { ip, port });
            if (r.success) {
                setConnStatus(true);
                nachiLog(`Connected  connID=${r.conn_id}`);
            } else {
                nachiLog(`ERROR: ${r.error}`);
            }
        } catch (e) {
            nachiLog(`Network error: ${e.message}`);
        }
    };

    window.nachiDisconnect = async function () {
        try {
            await apiFetch('/api/robot/disconnect', 'POST');
        } catch (_) {}
        setConnStatus(false);
        nachiLog('Disconnected');
    };

    window.nachiStop = async function () {
        try {
            await apiFetch('/api/robot/stop', 'POST');
            nachiLog('STOP sent');
        } catch (e) {
            nachiLog(`Stop error: ${e.message}`);
        }
    };

    window.nachiOpPrep = async function (enable) {
        try {
            const r = await apiFetch('/api/robot/opprep', 'POST', { enable });
            nachiLog(`OpPrep ${enable ? 'ON' : 'OFF'}: ${r.success ? 'OK' : r.error}`);
        } catch (e) {
            nachiLog(`OpPrep error: ${e.message}`);
        }
    };

    window.nachiMoveJoint = async function () {
        const angles = [];
        for (let i = 1; i <= 6; i++) {
            angles.push(parseFloat(document.getElementById(`nachi-jt-j${i}`)?.value || 0));
        }
        try {
            const r = await apiFetch('/api/robot/move/joint', 'POST', { angles });
            nachiLog(`MoveJA: ${r.success ? 'OK' : r.error}`);
        } catch (e) {
            nachiLog(`MoveJA error: ${e.message}`);
        }
    };

    window.nachiMoveCartesian = async function () {
        const fields = ['x','y','z','roll','pitch','yaw'];
        const body = {};
        fields.forEach(f => {
            body[f] = parseFloat(document.getElementById(`nachi-cart-${f}`)?.value || 0);
        });
        body.interp = document.getElementById('nachi-cart-interp')?.value || 'joint';
        try {
            const r = await apiFetch('/api/robot/move/cartesian', 'POST', body);
            nachiLog(`MoveXR: ${r.success ? 'OK' : r.error}`);
        } catch (e) {
            nachiLog(`MoveXR error: ${e.message}`);
        }
    };

    window.nachiJog = async function (type, axis, sign) {
        const step = parseFloat(document.getElementById('nachi-jog-step')?.value || 5) * sign;
        try {
            const r = await apiFetch('/api/robot/jog', 'POST', { type, axis, step });
            if (!r.success) nachiLog(`Jog error: ${r.error}`);
        } catch (e) {
            nachiLog(`Jog error: ${e.message}`);
        }
    };

    window.nachiSliderChanged = async function (idx, val) {
        document.getElementById(`nachi-j${idx}-val`).textContent = parseFloat(val).toFixed(1) + '°';
        const angles = [..._lastAngles];
        angles[idx - 1] = parseFloat(val);
        _lastAngles = angles;
        updateRobotPose(angles);
        try {
            await apiFetch('/api/robot/move/joint', 'POST', { angles });
        } catch (_) {}
    };

    // ── Initialization (called when tab is first opened) ─────────────────────

    window.initNachi = function () {
        if (_initialized) return;
        _initialized = true;

        // Restore saved backend URL
        const saved = localStorage.getItem('nachiBackendUrl');
        if (saved) {
            _backendUrl = saved;
            const el = document.getElementById('nachi-backend-url');
            if (el) el.value = saved;
        }

        // Slider drag guards
        for (let i = 1; i <= 6; i++) {
            const sl = document.getElementById(`nachi-sl-j${i}`);
            if (!sl) continue;
            sl.addEventListener('mousedown',  () => { _sliderDragging = true; });
            sl.addEventListener('touchstart', () => { _sliderDragging = true; });
            sl.addEventListener('mouseup',    () => { _sliderDragging = false; });
            sl.addEventListener('touchend',   () => { _sliderDragging = false; });
        }

        initScene();
        _pollInterval = setInterval(pollState, 150);
        nachiLog('Nachi tab ready. Enter backend URL and robot IP, then click Connect.');
    };
})();
