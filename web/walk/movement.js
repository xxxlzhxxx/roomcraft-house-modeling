import * as THREE from 'three';
import {Octree} from 'three/addons/math/Octree.js';
import {Capsule} from 'three/addons/math/Capsule.js';

// Collision uses exported geometry; moving door panels are tested separately.
export function createMovement(model, doors) {
  const staticRoot = new THREE.Group();
  model.updateMatrixWorld(true);
  let meshes = 0;const wallBoxes=[];
  model.traverse(o => {
    if (!o.isMesh) return;
    let dynamic = false;
    for (let p=o; p; p=p.parent) if(p.userData.door_id || p.userData.realtime_mirror) dynamic=true;
    if(dynamic || /curtain|drape|foliage|plant|leaf|light|lamp|fixture/i.test(o.name)) return;
    const box=new THREE.Box3().setFromObject(o);
    if(box.min.y>1.75 || box.max.y<-.2) return;
    const size=box.getSize(new THREE.Vector3());
    // Solid thin architectural pieces need an interior test as triangle tests
    // alone only detect their outward-facing surfaces.
    if(size.y>.7&&Math.min(size.x,size.z)<.3&&Math.max(size.x,size.z)<12&&(o.geometry.index?.count||o.geometry.attributes.position.count)<600){const b=box.clone();b.min.x-=.15;b.max.x+=.15;b.min.z-=.15;b.max.z+=.15;wallBoxes.push(b);}
    const mesh=new THREE.Mesh(o.geometry); mesh.matrixAutoUpdate=false; mesh.matrix.copy(o.matrixWorld);staticRoot.add(mesh);meshes++;
  });
  const octree=new Octree().fromGraphNode(staticRoot);
  const radius=.16, capsule=new Capsule(), downward=new THREE.Ray(new THREE.Vector3(),new THREE.Vector3(0,-1,0));
  const point=new THREE.Vector3(), doorBox=new THREE.Box3();
  function valid(pos) {
    if(pos.x<-.2||pos.x>11.2||pos.z<-1.2||pos.z>12.3)return false;
    if(wallBoxes.some(b=>b.min.y<1.42&&b.max.y>.27&&pos.x>b.min.x&&pos.x<b.max.x&&pos.z>b.min.z&&pos.z<b.max.z))return false;
    capsule.start.set(pos.x,.27,pos.z);capsule.end.set(pos.x,1.42,pos.z);capsule.radius=radius;
    const hit=octree.capsuleIntersect(capsule);
    if(hit && hit.depth>.015 && Math.abs(hit.normal.y)<.85)return false;
    for(const d of doors){doorBox.setFromObject(d.object).expandByScalar(radius);point.set(pos.x,1.2,pos.z);if(doorBox.containsPoint(point))return false;}
    downward.origin.set(pos.x,.24,pos.z);const floor=octree.rayIntersect(downward);
    return !!floor && floor.distance<.45;
  }
  function safePosition(pos) {
    const p=new THREE.Vector3().fromArray(pos);p.y=1.58;
    if(valid(p))return p;
    for(let r=.15;r<=.9;r+=.15)for(let a=0;a<Math.PI*2;a+=Math.PI/6){const q=p.clone().add(new THREE.Vector3(Math.cos(a)*r,0,Math.sin(a)*r));if(valid(q))return q;}
    return p;
  }
  function move(camera, delta) {
    let blocked=false;
    // Axis-separated small steps permit sliding along walls without tunneling.
    const steps=Math.max(1,Math.ceil(delta.length()/.045));
    for(let s=0;s<steps;s++)for(const axis of ['x','z']){const v=delta[axis]/steps;if(!v)continue;const p=camera.position.clone();p[axis]+=v;if(valid(p))camera.position.copy(p);else blocked=true;}
    return blocked;
  }
  return {move,safePosition,valid,meshCount:meshes};
}
