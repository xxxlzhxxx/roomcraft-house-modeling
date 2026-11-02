import * as THREE from 'three';
export function createLights(scene, renderer, config) {
  renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
  const hemi=new THREE.HemisphereLight('#fff7e8','#8a8f87',.8);scene.add(hemi);
  const sun=new THREE.DirectionalLight('#fff6e9',2.1);
  sun.position.set(4,9,16);sun.target.position.set(5,0,5);scene.add(sun,sun.target);
  sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);sun.shadow.radius=3;sun.shadow.blurSamples=8;Object.assign(sun.shadow.camera,{left:-9,right:9,top:9,bottom:-9,near:.1,far:35});sun.shadow.bias=-.00015;sun.shadow.normalBias=.012;
  const lights=config.fixtures.map(f=>{const light=new THREE.SpotLight(f.color||'#ffe6cc',0,f.distanceM||6,THREE.MathUtils.degToRad(Math.min(f.angleDeg||70,50)),.85,2);light.position.fromArray(f.position);light.target.position.fromArray(f.target);light.shadow.mapSize.set(2048,2048);light.shadow.radius=3;light.shadow.blurSamples=8;light.shadow.camera.near=.06;light.shadow.bias=-.00015;light.shadow.normalBias=.012;scene.add(light,light.target);return {light,base:f.webIntensityCandela||100,id:f.id};});
  let mode='day',level=1,position=new THREE.Vector3(5.2,1.58,8),quality='balanced';
  function apply(){
    const ranked=[...lights].sort((a,b)=>a.light.position.distanceToSquared(position)-b.light.position.distanceToSquared(position));
    const count=quality==='high'?3:2;
    for(const entry of lights){const i=ranked.indexOf(entry);entry.light.castShadow=i<count;entry.light.intensity=i<count?entry.base*(mode==='day'?.34:.65)*level:0;}
    sun.intensity=mode==='day'?1.9:.035;hemi.intensity=mode==='day'?.65:.15+.30*level;hemi.color.set(mode==='day'?'#fff9ed':'#f9e6ce');scene.environmentIntensity=mode==='day'?.6:.18+.13*level;scene.background.set(mode==='day'?'#deded5':'#252a2b');renderer.toneMappingExposure=mode==='day'?1.04:1.12;
  }
  apply();
  return {setMode(v){mode=v;apply()},setLevel(v){level=v;apply()},setPosition(v){position.copy(v);apply()},setQuality(v){quality=v;apply()},get mode(){return mode}};
}
