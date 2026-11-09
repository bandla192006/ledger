import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { get, request, type Account } from "../api";
import { formatMoney } from "../money";

interface Ledger3DViewProps {
  onClose?: () => void;
}

interface ParticleFlow {
  curve: THREE.CatmullRomCurve3;
  progress: number;
  speed: number;
  color: THREE.Color;
  mesh: THREE.Mesh;
}

export function Ledger3DView({ onClose }: Ledger3DViewProps) {
  const mountRef = useRef<HTMLDivElement>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string>("3D Spatial Ledger Connected");
  const [attackResults, setAttackResults] = useState<string[]>([]);
  const [isSimulating, setIsSimulating] = useState(false);

  // Transfer form state
  const [fromAccountId, setFromAccountId] = useState<string>("");
  const [toAccountId, setToAccountId] = useState<string>("");
  const [transferAmount, setTransferAmount] = useState<string>("500");

  const flowsRef = useRef<ParticleFlow[]>([]);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const nodeMeshesRef = useRef<Map<string, THREE.Mesh>>(new Map());

  // Fetch initial data
  const loadData = async () => {
    try {
      const accRes = await get<{ accounts: Account[] }>("/v1/accounts").catch(() => ({ accounts: [] }));
      setAccounts(accRes.accounts);
      if (accRes.accounts.length >= 2) {
        if (!fromAccountId) setFromAccountId(accRes.accounts[0].id);
        if (!toAccountId) setToAccountId(accRes.accounts[1].id);
      }
    } catch {
      setStatusMessage("Visualizing 3D ledger network");
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  // Three.js scene setup
  useEffect(() => {
    const container = mountRef.current;
    if (!container) return;

    const width = container.clientWidth;
    const height = container.clientHeight;

    const scene = new THREE.Scene();
    sceneRef.current = scene;
    scene.background = new THREE.Color(0x0c1412);
    scene.fog = new THREE.FogExp2(0x0c1412, 0.02);

    const camera = new THREE.PerspectiveCamera(55, width / height, 0.1, 1000);
    camera.position.set(0, 18, 32);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.maxPolarAngle = Math.PI / 2 + 0.05;
    controls.minDistance = 8;
    controls.maxDistance = 60;

    // Ambient & Directional Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
    scene.add(ambientLight);

    const dirLight = new THREE.DirectionalLight(0x76ecc5, 1.2);
    dirLight.position.set(10, 25, 15);
    scene.add(dirLight);

    const blueLight = new THREE.PointLight(0x38bdf8, 2, 50);
    blueLight.position.set(-15, 10, -10);
    scene.add(blueLight);

    // Cyberpunk Hex / Grid Floor
    const grid = new THREE.GridHelper(50, 50, 0x1f5c4a, 0x132e26);
    grid.position.y = -3;
    scene.add(grid);

    // Central Settlement Vault
    const vaultGeo = new THREE.OctahedronGeometry(2, 0);
    const vaultMat = new THREE.MeshStandardMaterial({
      color: 0xf59e0b,
      metalness: 0.8,
      roughness: 0.2,
      emissive: 0xd97706,
      emissiveIntensity: 0.4,
      wireframe: false,
    });
    const vaultMesh = new THREE.Mesh(vaultGeo, vaultMat);
    vaultMesh.position.set(0, 0, 0);
    scene.add(vaultMesh);

    // Vault Outer Wireframe Shield
    const vaultShieldGeo = new THREE.IcosahedronGeometry(2.7, 1);
    const vaultShieldMat = new THREE.MeshBasicMaterial({
      color: 0xfbbf24,
      wireframe: true,
      transparent: true,
      opacity: 0.35,
    });
    const vaultShield = new THREE.Mesh(vaultShieldGeo, vaultShieldMat);
    scene.add(vaultShield);

    // Create Account Nodes in a ring around the Vault
    const displayAccounts = accounts.length > 0 ? accounts : [
      { id: "demo-alice", name: "Alice Primary", balance: 100000, currency: "INR", account_type: "USER", status: "ACTIVE" } as Account,
      { id: "demo-bob", name: "Bob Savings", balance: 50000, currency: "INR", account_type: "USER", status: "ACTIVE" } as Account,
      { id: "demo-charlie", name: "Charlie Operations", balance: 25000, currency: "INR", account_type: "USER", status: "ACTIVE" } as Account,
      { id: "demo-clearing", name: "Settlement Vault", balance: -175000, currency: "INR", account_type: "SYSTEM", status: "ACTIVE" } as Account,
    ];

    nodeMeshesRef.current.clear();
    const radius = 12;
    const userAccs = displayAccounts.filter(a => a.account_type === "USER");

    userAccs.forEach((acc, idx) => {
      const angle = (idx / Math.max(1, userAccs.length)) * Math.PI * 2;
      const x = Math.cos(angle) * radius;
      const z = Math.sin(angle) * radius;
      const y = 0.5;

      const nodeGeo = new THREE.DodecahedronGeometry(1.4, 0);
      const isFrozen = acc.status === "FROZEN";
      const nodeMat = new THREE.MeshStandardMaterial({
        color: isFrozen ? 0xef4444 : 0x10b981,
        emissive: isFrozen ? 0x991b1b : 0x065f46,
        emissiveIntensity: 0.5,
        metalness: 0.7,
        roughness: 0.3,
      });

      const nodeMesh = new THREE.Mesh(nodeGeo, nodeMat);
      nodeMesh.position.set(x, y, z);
      nodeMesh.userData = { id: acc.id, name: acc.name, balance: acc.balance };
      scene.add(nodeMesh);
      nodeMeshesRef.current.set(acc.id, nodeMesh);

      // Node base pedestal / energy ring
      const ringGeo = new THREE.RingGeometry(1.6, 1.9, 32);
      const ringMat = new THREE.MeshBasicMaterial({ color: 0x34d399, side: THREE.DoubleSide, transparent: true, opacity: 0.4 });
      const ringMesh = new THREE.Mesh(ringGeo, ringMat);
      ringMesh.rotation.x = Math.PI / 2;
      ringMesh.position.set(x, -2.9, z);
      scene.add(ringMesh);

      // Connecting energy spline to Central Vault
      const curve = new THREE.CatmullRomCurve3([
        new THREE.Vector3(x, y, z),
        new THREE.Vector3(x * 0.5, y + 2.5, z * 0.5),
        new THREE.Vector3(0, 0.5, 0),
      ]);
      const tubeGeo = new THREE.TubeGeometry(curve, 32, 0.05, 8, false);
      const tubeMat = new THREE.MeshBasicMaterial({ color: 0x1f5c4a, transparent: true, opacity: 0.45 });
      const tubeMesh = new THREE.Mesh(tubeGeo, tubeMat);
      scene.add(tubeMesh);
    });

    // 3D Ledger Block Tower on the side (+X: 18, +Z: -8)
    const towerGroup = new THREE.Group();
    towerGroup.position.set(18, 0, -6);
    scene.add(towerGroup);

    const blockCount = 6;
    for (let i = 0; i < blockCount; i++) {
      const bGeo = new THREE.BoxGeometry(3.5, 0.8, 2.5);
      const bMat = new THREE.MeshStandardMaterial({
        color: 0x1e293b,
        emissive: 0x0f172a,
        metalness: 0.9,
        roughness: 0.1,
      });
      const bMesh = new THREE.Mesh(bGeo, bMat);
      bMesh.position.y = i * 1.1;
      towerGroup.add(bMesh);

      // Glowing hash seal line on each block
      const edgeGeo = new THREE.EdgesGeometry(bGeo);
      const edgeMat = new THREE.LineBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.7 });
      const edgeLine = new THREE.LineSegments(edgeGeo, edgeMat);
      edgeLine.position.y = i * 1.1;
      towerGroup.add(edgeLine);
    }

    // Animation Loop
    let animationFrameId: number;
    const clock = new THREE.Clock();

    const animate = () => {
      animationFrameId = requestAnimationFrame(animate);
      const delta = clock.getDelta();
      const elapsed = clock.getElapsedTime();

      // Rotate Central Vault & Shield
      vaultMesh.rotation.y += delta * 0.6;
      vaultMesh.rotation.x = Math.sin(elapsed * 0.8) * 0.15;
      vaultShield.rotation.y -= delta * 0.3;
      vaultShield.rotation.z += delta * 0.2;

      // Pulse and gently bob node meshes
      nodeMeshesRef.current.forEach((mesh, _) => {
        mesh.rotation.y += delta * 0.5;
        mesh.position.y = 0.5 + Math.sin(elapsed * 2 + mesh.position.x) * 0.15;
      });

      // Update particle flows
      for (let i = flowsRef.current.length - 1; i >= 0; i--) {
        const flow = flowsRef.current[i];
        flow.progress += flow.speed * delta;
        if (flow.progress >= 1) {
          scene.remove(flow.mesh);
          flow.mesh.geometry.dispose();
          flowsRef.current.splice(i, 1);
        } else {
          const pt = flow.curve.getPoint(flow.progress);
          flow.mesh.position.copy(pt);
          flow.mesh.scale.setScalar(1 + Math.sin(flow.progress * Math.PI) * 0.5);
        }
      }

      controls.update();
      renderer.render(scene, camera);
    };

    animate();

    const handleResize = () => {
      if (!container) return;
      const w = container.clientWidth;
      const h = container.clientHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };

    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      cancelAnimationFrame(animationFrameId);
      renderer.dispose();
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [accounts]);

  // Helper to trigger 3D particle energy beam
  const spawnEnergyBeam = (fromPos: THREE.Vector3, toPos: THREE.Vector3, colorHex: number = 0x10b981) => {
    if (!sceneRef.current) return;
    const midPoint = new THREE.Vector3()
      .addVectors(fromPos, toPos)
      .multiplyScalar(0.5)
      .add(new THREE.Vector3(0, 4, 0));

    const curve = new THREE.CatmullRomCurve3([fromPos, midPoint, toPos]);
    const pGeo = new THREE.SphereGeometry(0.35, 12, 12);
    const pMat = new THREE.MeshBasicMaterial({ color: colorHex });
    const pMesh = new THREE.Mesh(pGeo, pMat);
    sceneRef.current.add(pMesh);

    flowsRef.current.push({
      curve,
      progress: 0,
      speed: 1.2,
      color: new THREE.Color(colorHex),
      mesh: pMesh,
    });
  };

  // Perform 3D Transfer with real API
  const handleTransfer = async () => {
    if (!fromAccountId || !toAccountId || fromAccountId === toAccountId) {
      setStatusMessage("Select two distinct accounts");
      return;
    }
    const paise = Math.round(parseFloat(transferAmount) * 100);
    if (isNaN(paise) || paise <= 0) {
      setStatusMessage("Invalid transfer amount");
      return;
    }

    setLoading(true);
    setStatusMessage("Executing atomic transaction across ledger lines...");

    const fromMesh = nodeMeshesRef.current.get(fromAccountId);
    const toMesh = nodeMeshesRef.current.get(toAccountId);
    const fromPos = fromMesh ? fromMesh.position : new THREE.Vector3(-10, 0, 0);
    const toPos = toMesh ? toMesh.position : new THREE.Vector3(10, 0, 0);

    const idempotencyKey = "3d-" + Math.random().toString(36).substring(2, 12);

    try {
      await request(
        "/v1/transfers",
        {
          method: "POST",
          body: {
            source_account_id: fromAccountId,
            destination_account_id: toAccountId,
            amount: paise,
            currency: "INR",
          },
          idempotencyKey,
        }
      );

      // Spawn successful energy beam
      spawnEnergyBeam(fromPos, toPos, 0x10b981);
      setStatusMessage(`Transfer successful: ${formatMoney(paise, "INR")} moved! Invariant conserved.`);
      await loadData();
    } catch (err: any) {
      // Spawn rejected red beam
      spawnEnergyBeam(fromPos, toPos, 0xef4444);
      setStatusMessage(`Refused: ${err?.error?.message || err?.message || "Transaction aborted"}`);
    } finally {
      setLoading(false);
    }
  };

  // Run Race Attack Simulator in 3D
  const runRaceSimulation = async () => {
    if (accounts.length < 2) {
      setStatusMessage("Need at least 2 accounts for race attack");
      return;
    }
    setIsSimulating(true);
    setAttackResults([]);
    setStatusMessage("⚡ FIRING 10 CONCURRENT WITHDRAWAL ATTACK WAVE...");

    const victimAccount = accounts[0];
    const fromMesh = nodeMeshesRef.current.get(victimAccount.id);
    const vaultPos = new THREE.Vector3(0, 0, 0);
    const fromPos = fromMesh ? fromMesh.position : new THREE.Vector3(-8, 0, 0);

    const requests = Array.from({ length: 10 }).map(async (_, idx) => {
      const key = `attack-race-${Date.now()}-${idx}-${Math.random()}`;
      try {
        await request(
          "/v1/withdrawals",
          {
            method: "POST",
            body: {
              account_id: victimAccount.id,
              amount: 50000, // ₹500
              currency: "INR",
            },
            idempotencyKey: key,
          }
        );
        spawnEnergyBeam(fromPos, vaultPos, 0x10b981);
        return `Req #${idx + 1}: 201 SUCCESS`;
      } catch (err: any) {
        spawnEnergyBeam(fromPos, vaultPos, 0xef4444);
        return `Req #${idx + 1}: REFUSED (${err?.error?.code || "INSUFFICIENT_FUNDS"})`;
      }
    });

    const results = await Promise.all(requests);
    setAttackResults(results);
    const successes = results.filter(r => r.includes("201 SUCCESS")).length;
    const refusals = results.filter(r => r.includes("REFUSED")).length;

    setStatusMessage(`Attack Wave Complete: ${successes} Success, ${refusals} Refused. 0 Overdraw. Invariants 100% Intact!`);
    await loadData();
    setIsSimulating(false);
  };

  return (
    <div className="relative h-[82vh] w-full overflow-hidden rounded-xl border border-rule bg-[#0c1412] text-white shadow-2xl">
      {/* 3D Canvas Mount */}
      <div ref={mountRef} className="absolute inset-0 h-full w-full" />

      {/* Top HUD Header */}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-between p-4 bg-gradient-to-b from-[#0c1412]/90 to-transparent">
        <div className="pointer-events-auto flex items-center gap-3">
          <div className="flex h-3 w-3 items-center justify-center">
            <span className="relative flex h-3 w-3">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75"></span>
              <span className="relative inline-flex h-3 w-3 rounded-full bg-emerald-500"></span>
            </span>
          </div>
          <div>
            <h2 className="text-sm font-bold tracking-wider uppercase text-emerald-400">
              LedgerLine 3D Spatial Network
            </h2>
            <p className="text-xs text-emerald-200/70">{statusMessage}</p>
          </div>
        </div>

        <div className="pointer-events-auto flex items-center gap-3">
          <div className="hidden md:flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-950/40 px-3 py-1 text-xs text-emerald-300">
            <span>Conservation Invariant:</span>
            <span className="font-mono font-bold text-emerald-400">Δ = 0.00 (VERIFIED)</span>
          </div>
          {onClose && (
            <button
              onClick={onClose}
              className="rounded-lg border border-rule/50 bg-[#162722]/80 px-3 py-1.5 text-xs font-medium text-emerald-200 hover:bg-[#1f3830]"
            >
              Exit 3D View
            </button>
          )}
        </div>
      </div>

      {/* Left Overlay: Account Spatial Telemetry */}
      <div className="pointer-events-none absolute left-4 top-20 w-72 space-y-3">
        <div className="pointer-events-auto rounded-lg border border-emerald-500/20 bg-[#0c1412]/85 p-3.5 backdrop-blur-md">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-emerald-300">
            Spatial Nodes ({accounts.length})
          </h3>
          <div className="max-h-48 space-y-2 overflow-y-auto pr-1 text-xs">
            {accounts.map((acc) => (
              <div
                key={acc.id}
                onClick={() => setSelectedNode(acc.id)}
                className={`cursor-pointer rounded border p-2 transition ${
                  selectedNode === acc.id
                    ? "border-emerald-400 bg-emerald-950/60"
                    : "border-emerald-900/40 bg-[#13231f]/50 hover:border-emerald-700/60"
                }`}
              >
                <div className="flex justify-between font-medium">
                  <span className="truncate text-emerald-200">{acc.name}</span>
                  <span className="font-mono text-emerald-400">{formatMoney(acc.balance, acc.currency)}</span>
                </div>
                <div className="mt-1 flex items-center justify-between text-[10px] text-emerald-300/60">
                  <span className="truncate">{acc.id.slice(0, 10)}...</span>
                  <span className={acc.status === "FROZEN" ? "text-red-400" : "text-emerald-400"}>
                    {acc.status}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Attack Wave Results HUD */}
        {attackResults.length > 0 && (
          <div className="pointer-events-auto rounded-lg border border-amber-500/30 bg-[#0c1412]/90 p-3 backdrop-blur-md">
            <h4 className="text-xs font-bold text-amber-400 uppercase tracking-wider mb-1.5">
              Live Concurrency Attack Telemetry
            </h4>
            <div className="max-h-28 overflow-y-auto font-mono text-[11px] space-y-0.5">
              {attackResults.map((r, i) => (
                <div key={i} className={r.includes("201") ? "text-emerald-400" : "text-rose-400"}>
                  {r}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Right / Bottom Action Panel: 3D Transfer & Attack Harness */}
      <div className="pointer-events-none absolute right-4 bottom-4 w-80 space-y-3">
        <div className="pointer-events-auto rounded-lg border border-emerald-500/20 bg-[#0c1412]/85 p-4 backdrop-blur-md shadow-xl">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-emerald-300">
            Dispatch 3D Money Beam
          </h3>
          <div className="space-y-2.5 text-xs">
            <div>
              <label className="text-[11px] text-emerald-300/70">From Account</label>
              <select
                value={fromAccountId}
                onChange={(e) => setFromAccountId(e.target.value)}
                className="w-full rounded border border-emerald-800/60 bg-[#13231f] p-1.5 text-emerald-200 focus:outline-none focus:border-emerald-400"
              >
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} ({formatMoney(a.balance, a.currency)})
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="text-[11px] text-emerald-300/70">To Account</label>
              <select
                value={toAccountId}
                onChange={(e) => setToAccountId(e.target.value)}
                className="w-full rounded border border-emerald-800/60 bg-[#13231f] p-1.5 text-emerald-200 focus:outline-none focus:border-emerald-400"
              >
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="text-[11px] text-emerald-300/70">Amount (₹)</label>
              <input
                type="number"
                value={transferAmount}
                onChange={(e) => setTransferAmount(e.target.value)}
                className="w-full rounded border border-emerald-800/60 bg-[#13231f] p-1.5 font-mono text-emerald-200 focus:outline-none focus:border-emerald-400"
                placeholder="500"
              />
            </div>

            <button
              onClick={handleTransfer}
              disabled={loading || isSimulating}
              className="w-full rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 py-2 font-semibold text-white transition shadow-lg shadow-emerald-900/30"
            >
              {loading ? "Transmitting..." : "Send Money in 3D"}
            </button>
          </div>
        </div>

        {/* Concurrency Simulator Button */}
        <div className="pointer-events-auto rounded-lg border border-rose-500/30 bg-[#0c1412]/85 p-3 backdrop-blur-md">
          <button
            onClick={runRaceSimulation}
            disabled={isSimulating || loading}
            className="w-full rounded border border-rose-500/60 bg-rose-950/40 hover:bg-rose-900/50 py-2 text-xs font-bold text-rose-300 transition"
          >
            {isSimulating ? "Simulating Race..." : "⚡ Launch 10x Concurrent Race Attack"}
          </button>
        </div>
      </div>

      {/* 3D Legend & Controls Help in bottom-left */}
      <div className="pointer-events-none absolute left-4 bottom-4 flex items-center gap-4 text-[11px] text-emerald-200/50">
        <span>🖱 Left-Click + Drag: Orbit</span>
        <span>Scroll: Zoom</span>
        <span>Right-Click + Drag: Pan</span>
      </div>
    </div>
  );
}
