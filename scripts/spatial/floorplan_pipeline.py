"""Raster walls -> reviewed metric scene. No Blender operations in this module.

Detector is deliberately scoped to orthogonal, dark, solid-wall drawings.
Scale and opening classifications are explicit review inputs, not AI guesses.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image


def spans(values):
    indices = np.flatnonzero(values)
    if not len(indices):
        return []
    return [(int(g[0]), int(g[-1]) + 1) for g in np.split(indices, np.where(np.diff(indices) > 1)[0] + 1)]


def detect(image, calibration, trace=None):
    gray = np.asarray(Image.open(image).convert("L"))
    dark = gray < 65
    scale = calibration["meters_per_pixel"]
    ox, oy = calibration["origin_px"]
    walls, openings, columns = [], [], []
    for axis, mask in [("x", dark), ("y", dark.T)]:
        long = np.zeros_like(mask)
        for y, row in enumerate(mask):
            for start, end in spans(row):
                if end - start >= 22:
                    long[y, start:end] = True
        for lo, hi in spans(long.any(axis=1)):
            if not 8 <= hi - lo <= 36:
                continue
            center = (lo + hi - 1) / 2
            runs = spans(mask[round(center)])
            runs = [(a, b) for a, b in runs if b - a >= 18]
            if not runs:
                continue
            groups = [[runs[0]]]
            for run in runs[1:]:
                if run[0] - groups[-1][-1][1] <= calibration.get("max_gap_px", 145):
                    groups[-1].append(run)
                else:
                    groups.append([run])
            for group in groups:
                start, end = group[0][0], group[-1][1]
                thickness = hi - lo
                if end - start < 2 * thickness:
                    if axis == "x":
                        columns.append({"id": f"column-{len(columns)+1}", "center": [((start+end)/2-ox)*scale, (center-oy)*scale], "size": [(end-start)*scale, thickness*scale]})
                    continue
                candidates = []
                if trace:
                    ppi=trace["canvas"]["px_per_inch"]; tx,ty=trace["canvas"].get("origin_px",[0,0])
                    for candidate in trace["walls"]:
                        horizontal=abs(candidate["y2"]-candidate["y1"])<.01
                        coordinate=(candidate["y1"]*ppi+ty) if axis=="x" else (candidate["x1"]*ppi+tx)
                        if horizontal==(axis=="x") and abs(coordinate-center)<max(4,thickness/2):
                            candidates.append(candidate["id"])
                    if not candidates:
                        continue
                start += thickness / 2
                end -= thickness / 2
                wid = f"wall-{len(walls)+1}"
                p1 = [(start-ox)*scale, (center-oy)*scale] if axis == "x" else [(center-ox)*scale, (start-oy)*scale]
                p2 = [(end-ox)*scale, (center-oy)*scale] if axis == "x" else [(center-ox)*scale, (end-oy)*scale]
                wall = {"id": wid, "a": p1, "b": p2, "thickness": thickness*scale, "height": calibration["wall_height_m"], "axis": axis, "pixel_line": [start, center, end]}
                if trace: wall["upstream_candidates"]=candidates
                walls.append(wall)
                for (_, left), (right, _) in zip(group, group[1:]):
                    openings.append({"id": f"opening-{len(openings)+1}", "wall": wid, "offset": (left-start)*scale, "width": (right-left)*scale, "kind": "unknown", "review_status": "required"})
    return {"schema": "roomcraft.scene.v1", "units": "m", "coordinate_system": "x=east,y=south,z=up; wall openings offset from a", "image_size": [int(gray.shape[1]), int(gray.shape[0])], "calibration": calibration, "source_sha256": hashlib.sha256(Path(image).read_bytes()).hexdigest(), "upstream": {"tool":"armedad/floorplan-editor trace", "raw_wall_count": len(trace["walls"])} if trace else None, "walls": walls, "openings": openings, "columns": columns, "furniture": [], "zones": calibration.get("zones", [])}


def review(scene, annotations):
    result = copy.deepcopy(scene)
    changes = []
    for opening in result["openings"]:
        if opening["id"] not in annotations["openings"]:
            raise ValueError("Every opening needs explicit review: " + opening["id"])
        before = copy.deepcopy(opening)
        opening.update(annotations["openings"][opening["id"]])
        opening["review_status"] = "reviewed_from_source_image"
        changes.append({"id": opening["id"], "before": before, "after": copy.deepcopy(opening)})
    result["review"] = {"method": "human visual review of source raster; geometry remains approximate", "changes": changes}
    validate(result)
    return result


def validate(scene):
    if scene.get("units") != "m":
        raise ValueError("Scene must use meters")
    ids = [x["id"] for key in ("walls", "openings", "columns", "furniture") for x in scene.get(key, [])]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate object ID")
    walls = {w["id"]: w for w in scene["walls"]}
    for w in walls.values():
        if math.dist(w["a"], w["b"]) <= 0 or w["height"] <= 0 or w["thickness"] <= 0:
            raise ValueError("Invalid wall")
    for o in scene["openings"]:
        w = walls[o["wall"]]
        if o["offset"] < 0 or o["width"] <= 0 or o["offset"] + o["width"] > math.dist(w["a"], w["b"]) + .02:
            raise ValueError("Opening outside wall: " + o["id"])
        if o["kind"] not in ("door", "window"):
            raise ValueError("Unreviewed opening")
    bounds = scene["calibration"]["interior_bounds_m"]
    for f in scene.get("furniture", []):
        x,y = f["center"]; width,depth,height=f["size"]
        if min(width,depth,height) <= 0:
            raise ValueError("Invalid furniture size")
        angle=math.radians(f.get("rotation_deg",0)); ex=abs(math.cos(angle))*width/2+abs(math.sin(angle))*depth/2; ey=abs(math.sin(angle))*width/2+abs(math.cos(angle))*depth/2
        if not (bounds[0] <= x-ex and x+ex <= bounds[2] and bounds[1] <= y-ey and y+ey <= bounds[3]):
            raise ValueError("Furniture outside building: "+f["id"])
        # Oriented box vs wall center line: projected polygon SAT, accounting for wall thickness.
        ux,uy=math.cos(angle),math.sin(angle); vx,vy=-uy,ux
        for w in scene["walls"]:
            ax,ay=w["a"]; bx,by=w["b"]; dx,dy=bx-ax,by-ay; length=math.hypot(dx,dy); wx,wy=dx/length,dy/length; nx,ny=-wy,wx; cx,cy=(ax+bx)/2,(ay+by)/2
            overlap=True
            for tx,ty in [(ux,uy),(vx,vy),(wx,wy),(nx,ny)]:
                fr=abs(tx*ux+ty*uy)*width/2+abs(tx*vx+ty*vy)*depth/2
                wr=abs(tx*wx+ty*wy)*length/2+abs(tx*nx+ty*ny)*w["thickness"]/2
                if abs(tx*(x-cx)+ty*(y-cy)) >= fr+wr-.005: overlap=False; break
            if overlap:
                raise ValueError("Furniture intersects wall: "+f["id"]+" / "+w["id"])
    return {"status": "passed", "wall_count": len(walls), "opening_count": len(scene["openings"]), "furniture_count": len(scene.get("furniture", [])), "checks": ["unique_ids", "metric_units", "positive_dimensions", "openings_on_wall", "furniture_in_bounds", "furniture_wall_collision"], "not_checked": ["furniture_to_furniture_collision", "door_swing_clearance", "structural_safety", "construction_accuracy"]}


def apply_edit(scene, edit):
    result = copy.deepcopy(scene)
    for op in edit["operations"]:
        if op["op"] == "add_furniture":
            result["furniture"].append(op["value"])
        elif op["op"] == "update_furniture":
            f=next(f for f in result["furniture"] if f["id"]==op["id"])
            f.update(op["changes"])
        else:
            raise ValueError("Unsupported operation: " + op["op"])
    result["edit"] = edit
    validate(result)
    return result


def overlay(scene, image, output):
    from PIL import ImageDraw
    im = Image.open(image).convert("RGB"); draw = ImageDraw.Draw(im)
    c=scene["calibration"]; scale=c["meters_per_pixel"]; ox,oy=c["origin_px"]
    px=lambda p:(p[0]/scale+ox,p[1]/scale+oy)
    for w in scene["walls"]:
        draw.line([px(w["a"]),px(w["b"])],fill="#ee6c40",width=3)
        draw.text(px(w["a"]), w["id"], fill="#c33b17")
    for o in scene["openings"]:
        w=next(w for w in scene["walls"] if w["id"]==o["wall"]); length=math.dist(w["a"],w["b"])
        points=[[w["a"][i]+(w["b"][i]-w["a"][i])*t/length for i in range(2)] for t in [o["offset"],o["offset"]+o["width"]]]
        draw.line([px(p) for p in points],fill="#06a6b0",width=6)
        draw.text(px(points[0]),o["id"],fill="#007379")
    im.save(output)


def editor_plan(scene):
    """Adapter: RoomCraft meters -> external editor inches; retain IDs."""
    inch=.0254; c=scene["calibration"]
    p={"name":"Raster-derived reviewed demo", "units":"inches", "compass":{"up":"north","right":"east","down":"south","left":"west"}, "canvas":{"width_px":scene["image_size"][0],"height_px":scene["image_size"][1],"px_per_inch":inch/c["meters_per_pixel"],"origin_px":c["origin_px"]}, "walls":[],"openings":[],"fixtures":[],"counters":[],"labels":[]}
    for w in scene["walls"]:
        p["walls"].append({"id":w["id"],"x1":w["a"][0]/inch,"y1":w["a"][1]/inch,"x2":w["b"][0]/inch,"y2":w["b"][1]/inch,"thickness":w["thickness"]/inch})
    for o in scene["openings"]:
        entry={"id":o["id"],"wall":o["wall"],"type":o["kind"],"offset":o["offset"]/inch,"width":o["width"]/inch}
        if o["kind"]=="door": entry.update(hinge="start",swing="cw")
        p["openings"].append(entry)
    return p


def import_editor(plan, previous):
    if plan.get("units")!="inches": raise ValueError("External adapter requires inches")
    scene=copy.deepcopy(previous); old={w["id"]:w for w in scene["walls"]}; old_o={o["id"]:o for o in scene["openings"]}
    scene["walls"]=[];scene["openings"]=[]
    for w in plan["walls"]:
        item={"id":w["id"],"a":[w["x1"]*.0254,w["y1"]*.0254],"b":[w["x2"]*.0254,w["y2"]*.0254],"thickness":w["thickness"]*.0254,"height":old.get(w["id"],{}).get("height",scene["calibration"]["wall_height_m"])}
        scene["walls"].append(item)
    for o in plan.get("openings",[]):
        item=copy.deepcopy(old_o.get(o["id"],{}));item.update(id=o["id"],wall=o["wall"],kind=o["type"],offset=o["offset"]*.0254,width=o["width"]*.0254,review_status="imported_from_editor_requires_review")
        item.setdefault("sill",.9 if o["type"]=="window" else 0);item.setdefault("height",1.5 if o["type"]=="window" else 2.15);scene["openings"].append(item)
    scene["editor_import"]={"geometry":"wall and opening edits imported", "not_imported":["fixtures","counters","labels"], "review_required":True}
    validate(scene)
    return scene


def main():
    p=argparse.ArgumentParser(); p.add_argument("stage",choices=["detect","review","edit","validate","export-editor","import-editor"]); p.add_argument("--input",type=Path,required=True); p.add_argument("--config",type=Path); p.add_argument("--output",type=Path,required=True); p.add_argument("--image",type=Path); p.add_argument("--trace",type=Path)
    a=p.parse_args(); a.output.parent.mkdir(parents=True,exist_ok=True)
    config=json.loads(a.config.read_text()) if a.config else None
    if a.stage=="detect": result=detect(a.input,config,json.loads(a.trace.read_text()) if a.trace else None)
    elif a.stage=="review": result=review(json.loads(a.input.read_text()),config)
    elif a.stage=="edit": result=apply_edit(json.loads(a.input.read_text()),config)
    elif a.stage=="export-editor": result=editor_plan(json.loads(a.input.read_text()))
    elif a.stage=="import-editor": result=import_editor(json.loads(a.input.read_text()),config)
    else: result=validate(json.loads(a.input.read_text()))
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    if a.image: overlay(result,a.image,a.output.with_suffix(".overlay.png"))
    print(a.output)


if __name__=="__main__": main()
