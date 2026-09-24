// Minimal STL preview built on three.js (vendored).
import * as THREE from 'three';
import { STLLoader } from '/vendor/three/STLLoader.js';
import { OrbitControls } from '/vendor/three/OrbitControls.js';

export class ModelViewer {
  constructor(container) {
    this.container = container;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.1, 5000);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.1;
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x334455, 1.1));
    const key = new THREE.DirectionalLight(0xffffff, 1.4); key.position.set(1, 2, 3); this.scene.add(key);
    const fill = new THREE.DirectionalLight(0xffb080, 0.5); fill.position.set(-2, -1, -1); this.scene.add(fill);
    this.mesh = null;
    this.dimsEl = document.createElement('div');
    this.dimsEl.className = 'dims';
    container.appendChild(this.dimsEl);
    this._resize = () => this.resize();
    window.addEventListener('resize', this._resize);
    this.resize();
    this._raf = 0;
    this._loop();
  }

  resize() {
    const w = this.container.clientWidth || 300, h = this.container.clientHeight || 240;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  _loop() {
    this._raf = requestAnimationFrame(() => this._loop());
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  async loadUrl(url) {
    const buf = await fetch(url, { credentials: 'same-origin' }).then((r) => {
      if (!r.ok) throw new Error(`Preview failed (${r.status})`);
      return r.arrayBuffer();
    });
    this.loadBuffer(buf);
  }

  loadBuffer(buffer) {
    const geometry = new STLLoader().parse(buffer);
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    const bb = geometry.boundingBox;
    const size = new THREE.Vector3(); bb.getSize(size);
    const center = new THREE.Vector3(); bb.getCenter(center);
    geometry.translate(-center.x, -center.y, -bb.min.z);
    if (this.mesh) { this.scene.remove(this.mesh); this.mesh.geometry.dispose(); }
    const mat = new THREE.MeshStandardMaterial({ color: 0xff7a2f, roughness: 0.55, metalness: 0.05 });
    this.mesh = new THREE.Mesh(geometry, mat);
    this.mesh.rotation.x = -Math.PI / 2; // Z-up (STL) -> Y-up (three)
    this.scene.add(this.mesh);

    // bed plane sized to the model
    if (this.grid) this.scene.remove(this.grid);
    const span = Math.max(size.x, size.y, size.z, 10) * 1.6;
    this.grid = new THREE.GridHelper(span, 10, 0x3a4150, 0x262b35);
    this.scene.add(this.grid);

    const radius = Math.max(size.x, size.y, size.z) || 10;
    this.camera.position.set(radius * 1.4, radius * 1.1, radius * 1.6);
    this.controls.target.set(0, size.z / 2, 0);
    this.camera.near = radius / 100; this.camera.far = radius * 50; this.camera.updateProjectionMatrix();
    this.controls.update();
    this.dims = { x: size.x, y: size.y, z: size.z, triangles: geometry.attributes.position.count / 3 };
    this.dimsEl.textContent = `${size.x.toFixed(1)} × ${size.y.toFixed(1)} × ${size.z.toFixed(1)} mm`;
    return this.dims;
  }

  destroy() {
    cancelAnimationFrame(this._raf);
    window.removeEventListener('resize', this._resize);
    this.controls.dispose();
    this.renderer.dispose();
    if (this.mesh) this.mesh.geometry.dispose();
    this.container.innerHTML = '';
  }
}
