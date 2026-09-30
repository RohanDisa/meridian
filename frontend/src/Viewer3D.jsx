import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

export default function Viewer3D({ drawingId, label, disclaimer, dimensions = [], theme = "light" }) {
  const host = useRef(null);

  useEffect(() => {
    const el = host.current;
    if (!el || !drawingId) {
      return undefined;
    }
    let cancelled = false;
    const width = Math.max(el.clientWidth, 1);
    const height = Math.max(el.clientHeight, 1);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(theme === "dark" ? 0x1a1916 : 0xf5f4f0);
    const camera = new THREE.PerspectiveCamera(40, width / height, 0.1, 5000);
    camera.position.set(180, 140, 220);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    el.replaceChildren(renderer.domElement);
    scene.add(new THREE.AmbientLight(0xffffff, 0.8));
    const key = new THREE.DirectionalLight(0xffffff, 0.7);
    key.position.set(80, 120, 60);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xffffff, 0.25);
    fill.position.set(-60, 40, -80);
    scene.add(fill);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    const loader = new GLTFLoader();
    loader.load(
      `/api/reconstructions/${drawingId}/file`,
      (gltf) => {
        if (cancelled) {
          return;
        }
        gltf.scene.traverse((child) => {
          if (child.isMesh) {
            child.material = new THREE.MeshStandardMaterial({
              color: 0x7d838c,
              metalness: 0.35,
              roughness: 0.45,
            });
          }
        });
        scene.add(gltf.scene);
        const box = new THREE.Box3().setFromObject(gltf.scene);
        const size = box.getSize(new THREE.Vector3());
        const span = Math.max(size.x, size.y, size.z, 1);
        const center = box.getCenter(new THREE.Vector3());
        controls.target.copy(center);
        camera.near = Math.max(0.01, span / 200);
        camera.far = span * 20;
        camera.position.copy(
          center.clone().add(new THREE.Vector3(span * 0.75, span * 0.55, span * 0.85))
        );
        camera.updateProjectionMatrix();
      },
      undefined,
      () => undefined
    );
    let frame = 0;
    const tick = () => {
      frame = requestAnimationFrame(tick);
      controls.update();
      renderer.render(scene, camera);
    };
    tick();
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      controls.dispose();
      renderer.dispose();
      el.replaceChildren();
    };
  }, [drawingId, theme]);

  return (
    <div className="viewer3d">
      <p className="eyebrow">
        3D · {drawingId}
        {label ? ` · ${label}` : ""}
      </p>
      <div className="viewer3d-canvas" ref={host} />
      <p className="disclaimer">{disclaimer}</p>
      {dimensions.length > 0 && (
        <div className="dim-panel">
          <p className="eyebrow">Dimensions used</p>
          <ul>
            {dimensions.map((item) => (
              <li key={`${item.value}-${item.claim_id || "none"}`}>
                {item.value} · {item.drawing_id} p.{item.page || 1}
                {item.claim_id ? ` · claim ${item.claim_id}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
