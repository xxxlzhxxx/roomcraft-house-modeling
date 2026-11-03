"""Publish a whitelist of local demo assets. Never copy provider/CLI logs to web."""
import argparse
import hashlib
import json
import math
import shutil
import struct
from pathlib import Path


def compare(reference, detected):
    def positions(scene, legacy=False):
        walls={w["id"]:w for w in scene["walls"]};items=[]
        for o in scene["openings"]:
            w=walls[o["wall"]]
            a=[w["x1"]*.0254,w["y1"]*.0254] if legacy else w["a"]
            b=[w["x2"]*.0254,w["y2"]*.0254] if legacy else w["b"]
            unit=.0254 if legacy else 1;length=math.dist(a,b);t=(o["offset"]+o["width"]/2)*unit/length
            items.append({"id":o["id"],"center":[a[i]+(b[i]-a[i])*t for i in range(2)],"width":o["width"]*unit})
        return items
    target=positions(reference,True); unmatched=positions(detected);rows=[]
    for t in target:
        nearest=min(unmatched,key=lambda d:math.dist(d["center"],t["center"]));unmatched.remove(nearest)
        rows.append({"reference_id":t["id"],"detected_id":nearest["id"],"center_error_m":math.dist(t["center"],nearest["center"]),"width_error_m":abs(t["width"]-nearest["width"])})
    max_width=max(r["width_error_m"] for r in rows);max_center=max(r["center_error_m"] for r in rows)
    return {"method":"Nearest-center one-to-one matching on this synthetic sample only; reference JSON read only during evaluation, not recognition.","matches":rows,"unmatched_detected":len(unmatched),"max_width_error_m":max_width,"max_center_error_m":max_center,"summary":f"合成样例的 {len(rows)} 处门窗均匹配；开口中心最大偏差 {max_center*100:.1f} cm、净宽最大偏差 {max_width*100:.1f} cm。仅代表本例，不代表真实户型图准确率。"}


def main():
    root=Path(__file__).resolve().parents[2]
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,default=root/'runs/open-source-demo');p.add_argument('--sample-dir',type=Path,default=root/'inputs/research-samples');a=p.parse_args();run=a.run_dir.resolve();samples=a.sample_dir.resolve();out=root/'web/demo/data';out.mkdir(parents=True,exist_ok=True)
    comparison=compare(json.loads((samples/'floorplan-c.json').read_text()),json.loads((run/'reviewed.json').read_text()))
    glb_checks={}
    for variant in ('baseline','edited'):
        data=(run/variant/'model.glb').read_bytes()
        if data[:4]!=b'glTF':raise ValueError('Invalid GLB header')
        length=struct.unpack_from('<I',data,12)[0];gltf=json.loads(data[20:20+length])
        cutaway=sum(bool(n.get('extras',{}).get('cutaway_hide')) for n in gltf.get('nodes',[]))
        external=[i['uri']for i in gltf.get('images',[])if i.get('uri') and not i['uri'].startswith('data:')]
        if not cutaway:raise ValueError(variant+': overview cutaway tags are missing')
        if external:raise ValueError(variant+': model references external images')
        verification=json.loads((run/variant/'verification-report.json').read_text())
        if verification['status']!='passed':raise ValueError(variant+': Blender verification failed')
        glb_checks[variant]={'cutaway_nodes':cutaway,'meshes':len(gltf.get('meshes',[])),'embedded_images':len(gltf.get('images',[])),'external_image_dependencies':len(external)}
    files={'source.png':samples/'floorplan-c.png','overlay.png':run/'reviewed.overlay.png','request.txt':run/'request.txt','detected.json':run/'detected.json','reviewed.json':run/'reviewed.json','edit.json':run/'edit.json','edited.json':run/'edited.json','validation.json':run/'validation.json','editor-plan.json':run/'editor/plan.json'}
    for variant in ('baseline','edited'):
        for filename in ('model.glb',) if variant=='baseline' else ('model.glb','scene.blend','overview.png','living.png'):
            files[variant+'/'+filename]=run/variant/filename
        for filename in ('build-report.json','verification-report.json'):
            files[variant+'/'+filename]=run/variant/filename
    for filename in ('layout-validation.json','editor-validation.json','model-config-check.json','browser-validation.json','kimi-summary.json'):
        if (run/filename).exists():files[filename]=run/filename
    for dest,source in files.items():
        if not source.is_file():raise FileNotFoundError('Required output missing: '+str(source))
        target=out/dest;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    evidence=sorted(run.glob('*execution.provider.json'))
    report={'case':'Synthetic raster floorplan proof of concept','stages':['opensource trace','candidate normalization','scale and opening review','natural language to operations via Codex CLI configured Kimi K3.1','validated scene edit','Blender scene/render/export through same Kimi CLI','Three.js viewing'],'source':'https://github.com/armedad/floorplan-editor/tree/0c833f84507904cb0ab7587980a7a71a902a26d0/examples/sample','licenses':{'floorplan-editor':'GPL-3.0-only, run as separately cloned local program','Three.js':'MIT','sample':'Synthetic example from floorplan-editor; code license does not by itself attest all content redistribution rights'},'comparison':comparison,'checks':json.loads((run/'validation.json').read_text()),'provider_configuration_evidence':[json.loads(f.read_text()) for f in evidence],'limitations':['Orthogonal solid black wall raster only; no universal floorplan recognizer','Scale from companion calibration, wall height assumed','Opening classification and approximate zones reviewed manually','Parametric furniture; no brand models or Seed3D in this run','No Seedream enhancement','Local GLB realtime lighting differs from Cycles images','External editor roundtrip imports walls/openings only; saving requires import/review and another Kimi/Blender run','No certification of construction or structural safety'],'artifacts':{name:{'bytes':source.stat().st_size,'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}for name,source in files.items()}}
    report['blender_verification']={variant:json.loads((run/variant/'verification-report.json').read_text())for variant in ('baseline','edited')}
    report['glb_package_checks']=glb_checks
    for filename,key in [('layout-validation.json','layout_checks'),('editor-validation.json','editor_checks'),('model-config-check.json','private_inventory_mapping_check'),('browser-validation.json','browser_checks')]:
        if (run/filename).exists():report[key]=json.loads((run/filename).read_text())
    report['limitations'].append('No ceiling; demo navigation points, lighting layout, material zones and default route probes need adaptation for other floorplans')
    report['source_code_sha256']={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ('scripts/spatial/floorplan_pipeline.py','scripts/spatial/build_raster_demo.py','scripts/spatial/check_layout.py','scripts/spatial/run_floorplan_demo.py','requirements-demo.lock.txt','web/demo/app.js')}
    (out/'run-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');(run/'comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2)+'\n')
    vendor=root/'web/vendor/three';vendor.parent.mkdir(exist_ok=True)
    if not vendor.exists():vendor.symlink_to(root/'node_modules/three',target_is_directory=True)
    print(out);print(comparison['summary'])


if __name__=='__main__':main()
