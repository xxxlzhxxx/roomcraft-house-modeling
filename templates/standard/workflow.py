import argparse
import hashlib
import json
import math
import random
import shutil
import struct
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

RUN = Path(__file__).resolve().parent
ROOT = RUN.parent.parent
CONFIG = json.loads((RUN / 'config.json').read_text())
SOURCE = (RUN / CONFIG['source']).resolve()
REFINED = RUN / 'refined.blend'
STATE_PATH = RUN / 'state.json'
PROGRESS_PATH = RUN / 'progress.json'
ARGS = None
RENDER_COUNT = 0


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def input_provenance():
    materials = {}
    for key, relative in CONFIG['materialSources'].items():
        path = RUN / relative
        materials[key] = {'path': relative, 'sha256': sha(path), 'bytes': path.stat().st_size}
    return {'sourceSha256': sha(SOURCE), 'configSha256': sha(RUN / 'config.json'), 'workflowSha256': sha(__file__), 'materials': materials}


INPUTS = input_provenance()
SIGNATURE = hashlib.sha256(json.dumps(INPUTS, sort_keys=True).encode()).hexdigest()
EVIDENCE = {'interface': 'Codex CLI direct Blender workflow', 'model': 'Kimi K3.1', 'provider': 'ark-kimi-experiment', 'blenderVersion': bpy.app.version_string}


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2))


def load_json(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def progress(phase, status, **extra):
    data = load_json(PROGRESS_PATH, {})
    for stale in ['error', 'current', 'cached']:
        data.pop(stale, None)
    data.update({'phase': phase, 'status': status, 'updatedAt': now(), 'sourceSha256': sha(SOURCE), 'configSignature': SIGNATURE, 'execution': EVIDENCE})
    data.update(extra)
    data.setdefault('history', []).append({'phase': phase, 'status': status, 'at': now(), **({'error': extra['error']} if 'error' in extra else {})})
    write_json(PROGRESS_PATH, data)
    print('PROGRESS', phase, status, flush=True)


def state():
    return load_json(STATE_PATH, {'signature': SIGNATURE, 'outputs': {}})


def record(key, path, seconds=0, **metadata):
    path = Path(path)
    data = state()
    data['signature'] = SIGNATURE
    data['outputs'][key] = {'path': str(path.relative_to(RUN)), 'sha256': sha(path), 'bytes': path.stat().st_size, 'seconds': round(seconds, 3), 'success': True, 'sourceSha256': sha(SOURCE), 'configSignature': SIGNATURE, 'recordedAt': now(), **metadata}
    write_json(STATE_PATH, data)


def cached(key, path, required=()):
    item = state().get('outputs', {}).get(key)
    valid = not ARGS.force and item and item.get('success') and item.get('configSignature') == SIGNATURE and Path(path).exists() and item.get('sha256') == sha(path)
    return bool(valid and all((RUN / relative).is_file() for relative in required))


def require_current_blend():
    if not cached('refined-blend', REFINED):
        raise RuntimeError('refined.blend is missing or stale for the current source/config/workflow/material fingerprint; run prepare')
    baseline = load_json(RUN / 'source-baseline.json', {})
    if baseline.get('sourceSha256') != sha(SOURCE):
        raise RuntimeError('source-baseline.json is stale; run inspect')


def preflight(scene=None):
    nodes = CONFIG['panoramaNodes']
    errors = []
    if len(nodes) != 9:
        errors.append(f'Expected nine panorama nodes, found {len(nodes)}')
    reached = set()
    pending = [next(iter(nodes))]
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        pending.extend(nodes[name]['neighbors'])
    if reached != set(nodes):
        errors.append(f'Panorama graph is not fully connected: {sorted(set(nodes)-reached)}')
    for name, spec in nodes.items():
        if len(spec['neighbors']) != len(set(spec['neighbors'])):
            errors.append(f'Duplicate neighbors at {name}')
        if name not in CONFIG['suggestedViewTargets']:
            errors.append(f'Missing suggested view target for {name}')
        for neighbor in spec['neighbors']:
            if neighbor not in nodes:
                errors.append(f'Unknown neighbor {name}>{neighbor}')
                continue
            if name not in nodes[neighbor]['neighbors']:
                errors.append(f'Missing reciprocal navigation {neighbor}>{name}')
            for edge in (f'{name}>{neighbor}', f'{neighbor}>{name}'):
                if edge not in CONFIG['navigationPortals']:
                    errors.append(f'Missing portal {edge}')
    portal_checks = {}
    if scene is not None and not errors:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for name, spec in nodes.items():
            position, _ = safe_position(scene, spec)
            origin = xyz(position)
            for neighbor in spec['neighbors']:
                edge = f'{name}>{neighbor}'
                target = xyz(CONFIG['navigationPortals'][edge])
                delta = target-origin
                hit, location, _, _, hit_object, _ = scene.ray_cast(depsgraph, origin, delta.normalized(), distance=delta.length-.04)
                portal_checks[edge] = {'clear': not hit, 'hitObject': hit_object.name if hit else None, 'hitPosition': list(location) if hit else None}
                if hit:
                    errors.append(f'Navigation portal obstructed for {edge} by {hit_object.name}')
    report = {'pass': not errors, 'signature': SIGNATURE, 'nodeCount': len(nodes), 'connectedBothWays': not any('reciprocal' in error or 'connected' in error for error in errors), 'portalRays': portal_checks, 'errors': errors, 'execution': EVIDENCE, 'checkedAt': now()}
    write_json(RUN/'preflight.json', report)
    if errors:
        raise RuntimeError('Preflight failed: '+'; '.join(errors))
    return report


def srgb_channel(value):
    return value/12.92 if value <= .04045 else ((value+.055)/1.055)**2.4


def add_local_fixtures(scene):
    design = CONFIG['localFixtureDesign']
    hex_color = design['color'].lstrip('#')
    color = tuple(srgb_channel(int(hex_color[index:index+2],16)/255) for index in (0,2,4))
    material = bpy.data.materials.new('Workflow neutral flush diffuser')
    material.diffuse_color = (.82,.80,.75,1)
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = (.82,.80,.75,1)
    bsdf.inputs['Roughness'].default_value = .48
    bsdf.inputs['Emission Color'].default_value = (*color,1)
    bsdf.inputs['Emission Strength'].default_value = .35
    for spec in CONFIG['localFixtures']:
        position = xyz(spec['position'])
        bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=spec['diameterM']/2, depth=.035, location=(position.x,position.y,2.705))
        fixture = bpy.context.object
        fixture.name = spec['label']+' diffuser'
        fixture.data.materials.append(material)
        fixture['cutaway_hide'] = True
        fixture['workflow_local_fixture'] = spec['id']
        data = bpy.data.lights.new(spec['label'], 'AREA')
        data.shape = 'DISK'
        data.size = spec['diameterM']
        data.energy = spec['powerW']
        data.color = color
        light = bpy.data.objects.new(spec['label'], data)
        scene.collection.objects.link(light)
        light.location = position
        light.rotation_euler = (0,0,0)
        light['workflow_local_fixture'] = spec['id']
        light['lightingRole'] = 'interior-fixture'
        light['colorTemperatureK'] = design['colorTemperatureK']
        light['nominalEnergyW'] = spec['powerW']
        light['webIntensityCandela'] = spec['webIntensityCandela']


def xyz(position):
    x, plan_z, height = position
    return Vector((x, -plan_z, height))


def bounds(objects):
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = []
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        points.extend(evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box)
    low = [min(point[i] for point in points) for i in range(3)]
    high = [max(point[i] for point in points) for i in range(3)]
    return {'min': low, 'max': high}


def inventory(scene):
    return {
        'objects': [{'name': o.name, 'type': o.type, 'parent': o.parent.name if o.parent else None, 'properties': {k: o[k] for k in o.keys() if isinstance(o[k], (str, int, float, bool))}, 'bounds': bounds([o]) if o.type == 'MESH' else None} for o in scene.objects],
        'materials': sorted(m.name for m in bpy.data.materials if m.users),
        'cameras': [{'name': o.name, 'position': list(o.location), 'rotation': list(o.rotation_euler), 'lensMm': o.data.lens, 'type': o.data.type} for o in scene.objects if o.type == 'CAMERA'],
    }


def inspect():
    progress('inspect', 'running')
    bpy.ops.wm.open_mainfile(filepath=str(SOURCE))
    scene = bpy.context.scene
    inv = inventory(scene)
    furniture = [o for o in scene.objects if o.get('furniture_asset')]
    report = {'source': str(SOURCE.relative_to(ROOT)), 'sourceSha256': sha(SOURCE), 'renderer': {'engine': scene.render.engine, 'device': scene.cycles.device, 'threads': scene.render.threads, 'samples': scene.cycles.samples, 'viewTransform': scene.view_settings.view_transform}, 'inventory': inv, 'execution': EVIDENCE}
    write_json(RUN / 'source-inspection.json', report)
    baseline = {'sourceSha256': sha(SOURCE), 'furniture': {o.name: bounds([o]) for o in furniture}, 'doors': {o['door_id']: o['door_slide_x'] for o in scene.objects if o.get('door_id')}, 'netHeightM': bounds([scene.objects['Ceiling / 室内天花']])['min'][2] - bounds([scene.objects['Living marble floor']])['max'][2]}
    write_json(RUN / 'source-baseline.json', baseline)
    manifest = {'case': CONFIG['case'], 'source': str(SOURCE.relative_to(ROOT)), 'sourceSha256': sha(SOURCE), 'refinedBlend': 'refined.blend', 'seed': CONFIG['seed'], 'sourceInventory': inv, 'requiredFurnitureGroups': len(furniture), 'requiredDoors': baseline['doors'], 'netHeightM': baseline['netHeightM'], 'outputs': {'stills': ['living-refined.png', 'dining.png', 'study.png', 'master.png'], 'panoramas': [f'panoramas/{name}.jpg' for name in CONFIG['panoramaNodes']], 'model': 'model.glb'}, 'execution': EVIDENCE}
    write_json(RUN / 'scene-manifest.json', manifest)
    progress('inspect', 'completed', objects=len(inv['objects']), furnitureGroups=len(furniture))


def texture(name, size, kind):
    path = RUN / 'textures' / f'{name}.png'
    path.parent.mkdir(exist_ok=True)
    w, h = size
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = x / w, y / h
    rng = np.random.default_rng(CONFIG['seed'] + sum(map(ord, name)))
    if kind == 'marble':
        warp = .32 * np.sin(5*u + 3*v) + .17 * np.sin(13*u - 7*v) + .08 * np.sin(31*u + 17*v)
        field = np.abs(np.sin(8*u + 5*v + warp))
        vein = np.exp(-field * 65) * (.45 + .55 * np.sin(17*u - 11*v)**2)
        branch = np.exp(-np.abs(np.sin(19*u - 13*v + .6*np.sin(9*v))) * 100) * .18
        tone = .91 - .26*vein - .12*branch + .018*np.sin(43*u + 29*v) + rng.normal(0, .003, (h, w))
        rgb = np.stack([tone*1.02, tone, tone*.955], axis=-1)
        joints = (x < 2) | (y < 2)
        rgb[joints] = [.57, .55, .51]
    elif kind == 'wood':
        rows = 20
        row = np.floor(v*rows).astype(int)
        plank = np.floor(u*3 + (row % 3)/3).astype(int)
        variation = .94 + .08 * ((np.sin(row*91.7 + plank*37.3)*43758.5) % 1)
        grain = .012*np.sin(u*650 + 3*np.sin(v*35)) + .006*np.sin(u*1400 + v*18) + rng.normal(0, .003, (h, w))
        tone = variation + grain
        rgb = np.stack([.57*tone, .39*tone, .235*tone], axis=-1)
        joints = ((u*3 + (row % 3)/3) % 1 < .004) | ((v*rows) % 1 < .012)
        rgb[joints] = [.40, .28, .18]
    elif kind == 'burr':
        knots = np.zeros((h,w), dtype=np.float32)
        for cx, cy, aspect in [(.24,.28,1.4),(.72,.63,.8),(.43,.86,1.2),(.86,.18,1.0),(.58,.42,1.6)]:
            dx, dy = (u-cx)*aspect, v-cy
            radius = np.sqrt(dx*dx+dy*dy)
            angle = np.arctan2(dy,dx)
            knots += np.exp(-radius*11) * np.sin(radius*210 + 3*np.sin(angle*4)) * .065
        fine = .012*np.sin(u*520 + 5*np.sin(v*29)) + rng.normal(0,.003,(h,w))
        tone = .94 + knots + fine + .012*np.sin(17*u+13*v)
        rgb = np.stack([.47*tone, .355*tone, .26*tone], axis=-1)
    else:
        pattern = .160*np.sin(8*u + 1.8*np.sin(5*v)) * np.sin(7*v + 1.2*np.sin(4*u)) + .065*np.sin(15*u - 9*v + np.sin(6*v))
        weave = .009*np.sin(x*math.pi) * np.sin(y*math.pi) + rng.normal(0, .003, (h, w))
        tone = .66 + pattern + weave
        rgb = np.stack([tone*1.03, tone, tone*.94], axis=-1)
    rgba = np.ones((h, w, 4), dtype=np.float32)
    rgba[:, :, :3] = np.clip(rgb, 0, 1)
    image = bpy.data.images.new(name, width=w, height=h, alpha=True)
    image.pixels.foreach_set(rgba.ravel())
    image.filepath_raw = str(path)
    image.file_format = 'PNG'
    image.save()
    image.pack()
    return image


def image_material(name, image, roughness, bump_distance, noise_scale=160):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = (*image.pixels[:3], 1)
    nodes = mat.node_tree.nodes
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.inputs['Roughness'].default_value = roughness
    bsdf.inputs['Specular IOR Level'].default_value = .3
    tex = nodes.new('ShaderNodeTexImage')
    tex.image = image
    mat.node_tree.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    noise = nodes.new('ShaderNodeTexNoise')
    noise.inputs['Scale'].default_value = noise_scale
    noise.inputs['Detail'].default_value = 2
    bump = nodes.new('ShaderNodeBump')
    bump.inputs['Strength'].default_value = .22
    bump.inputs['Distance'].default_value = bump_distance
    mat.node_tree.links.new(noise.outputs['Fac'], bump.inputs['Height'])
    mat.node_tree.links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])
    mat.node_tree.links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
    return mat


def source_image_material(name, config_key, roughness, bump_distance, noise_scale=160):
    relative = CONFIG['materialSources'][config_key]
    image = bpy.data.images.load(str(RUN / relative), check_existing=True)
    image.name = f'{name} | {Path(relative).name}'
    image.colorspace_settings.name = 'sRGB'
    image.pack()
    return image_material(name, image, roughness, bump_distance, noise_scale)


def physical_uv(obj, tile_x, tile_y):
    mesh = obj.data
    uv = mesh.uv_layers.active or mesh.uv_layers.new(name='Physical metres')
    for loop in mesh.loops:
        point = obj.matrix_world @ mesh.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (point.x / tile_x, -point.y / tile_y)


def cabinet_box_uv(obj, tile_u=1.4, tile_v=.7):
    mesh = obj.data
    uv = mesh.uv_layers.active or mesh.uv_layers.new(name='Cabinet physical box metres')
    for polygon in mesh.polygons:
        normal = obj.matrix_world.to_3x3() @ polygon.normal
        axis = max(range(3), key=lambda i: abs(normal[i]))
        for loop_index in polygon.loop_indices:
            point = obj.matrix_world @ mesh.vertices[mesh.loops[loop_index].vertex_index].co
            if axis == 2:
                u, v = point.x, point.y
            elif axis == 1:
                u, v = point.x, point.z
            else:
                u, v = point.y, point.z
            uv.data[loop_index].uv = (u/tile_u, v/tile_v)


def refine_foliage(obj, seed):
    old = bounds([obj])
    center = Vector([(old['min'][i] + old['max'][i])/2 for i in range(3)])
    half = Vector([(old['max'][i] - old['min'][i])/2 for i in range(3)])
    rng = random.Random(seed)
    vertices, faces, indices = [], [], []
    inverse = obj.matrix_world.inverted()

    def stem(start, end, radius):
        direction = (end-start).normalized()
        tangent = direction.cross(Vector((0, 1, 0))).normalized()
        bitangent = direction.cross(tangent).normalized()
        base = len(vertices)
        for point in [start, end]:
            for side in range(6):
                vertices.append(inverse @ (point + radius*(tangent*math.cos(side*math.tau/6)+bitangent*math.sin(side*math.tau/6))))
        for side in range(6):
            faces.append((base+side, base+(side+1)%6, base+6+(side+1)%6, base+6+side)); indices.append(3)

    def leaf(world_pos, direction):
        normal = direction.normalized()
        tangent = normal.cross(Vector((0, 0, 1)))
        if tangent.length < .01:
            tangent = Vector((1, 0, 0))
        tangent.normalize()
        bitangent = normal.cross(tangent).normalized()
        length, width = rng.uniform(.055, .09), rng.uniform(.025, .042)
        base = len(vertices)
        vertices.extend([inverse @ (world_pos-tangent*length/2), inverse @ (world_pos+bitangent*width/2), inverse @ (world_pos+tangent*length/2), inverse @ (world_pos-bitangent*width/2), inverse @ (world_pos+normal*.008)])
        faces.extend([(base,base+1,base+4),(base+1,base+2,base+4),(base+2,base+3,base+4),(base+3,base,base+4)])
        indices.extend([rng.randrange(3)]*4)

    base_center = Vector((center.x, center.y, old['min'][2]+half.z*.12))
    for branch in range(7):
        angle = branch*math.tau/7 + rng.uniform(-.18,.18)
        tip = center + Vector((math.cos(angle)*half.x*.55, math.sin(angle)*half.y*.55, half.z*rng.uniform(.25,.8)))
        stem(base_center, tip, .007)
        for node in range(3):
            t = .48 + node*.22
            joint = base_center.lerp(tip, t)
            for side in [-1, 1]:
                branch_angle = angle + side*rng.uniform(.55,1.0)
                end = joint + Vector((math.cos(branch_angle)*half.x*.24, math.sin(branch_angle)*half.y*.24, half.z*.12))
                stem(joint, end, .003)
                for small in range(4):
                    position = joint.lerp(end, .35+small*.2)
                    leaf(position, Vector((math.cos(branch_angle+(-1)**small*.6), math.sin(branch_angle+(-1)**small*.6), rng.uniform(-.15,.55))))
    mesh = bpy.data.meshes.new(obj.name + ' leafy mesh')
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    old_mesh = obj.data
    obj.data = mesh
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)
    for mat in mesh.materials:
        mesh.materials.pop()
    for index, color in enumerate([(.12,.20,.075,1),(.17,.25,.095,1),(.23,.30,.12,1),(.105,.065,.035,1)]):
        name = f'Leaf olive {index+1}'
        mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        mat.diffuse_color = color
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get('Principled BSDF')
        bsdf.inputs['Base Color'].default_value = color
        bsdf.inputs['Roughness'].default_value = .72
        mesh.materials.append(mat)
    for polygon, material_index in zip(mesh.polygons, indices):
        polygon.material_index = material_index
        polygon.use_smooth = True
    obj['refinement'] = '168 folded leaves attached to seven branching stems inside original foliage bounding volume'
    obj['exclude_from_web'] = True


def add_exterior_backdrop(scene, name, y):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(-10,y,-1),(20,y,-1),(20,y,9),(-10,y,9)], [], [(0,1,2,3)])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    mat = bpy.data.materials.get('Distant neutral exterior') or bpy.data.materials.new('Distant neutral exterior')
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Roughness'].default_value = 1
    bsdf.inputs['Emission Strength'].default_value = .22
    coordinates = mat.node_tree.nodes.new('ShaderNodeTexCoord')
    separate = mat.node_tree.nodes.new('ShaderNodeSeparateXYZ')
    ramp = mat.node_tree.nodes.new('ShaderNodeValToRGB')
    ramp.color_ramp.elements[0].color = (.30,.35,.32,1)
    ramp.color_ramp.elements[1].color = (.68,.72,.74,1)
    mat.node_tree.links.new(coordinates.outputs['Generated'],separate.inputs[0])
    mat.node_tree.links.new(separate.outputs['Z'],ramp.inputs[0])
    mat.node_tree.links.new(ramp.outputs['Color'],bsdf.inputs['Base Color'])
    mat.node_tree.links.new(ramp.outputs['Color'],bsdf.inputs['Emission Color'])
    mesh.materials.append(mat)
    obj['exterior'] = True
    obj['exclude_from_web'] = True


def prepare():
    progress('prepare', 'running')
    if load_json(RUN / 'source-baseline.json', {}).get('sourceSha256') != sha(SOURCE):
        inspect()
    if cached('refined-blend', REFINED):
        progress('prepare', 'completed', cached=True)
        return
    bpy.ops.wm.open_mainfile(filepath=str(SOURCE))
    scene = bpy.context.scene
    marble = source_image_material('Calacatta ivory generated reference - 1.8x0.9m slabs', 'marble', .28, .00018, 260)
    wood = image_material('Oak / 浅橡木', texture('oak-staggered-3.6m', (2048,2048), 'wood'), .43, .00055, 190)
    burr = source_image_material('Champagne burl generated reference - physical box UV', 'burl', .35, .00022, 210)
    rug = image_material('Greige rug - low contrast abstract organic weave', texture('understated-greige-rug', (1024,1280), 'rug'), .9, .0008, 240)
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        if obj.name == 'Living marble floor':
            obj.data.materials.clear(); obj.data.materials.append(marble); physical_uv(obj, 1.8, .9)
        elif obj.name.startswith('Finished oak floor'):
            obj.data.materials.clear(); obj.data.materials.append(wood); physical_uv(obj, 3.6, 3.6)
        elif obj.name == 'Plain light greige rug 240x300cm':
            obj.data.materials.clear(); obj.data.materials.append(rug)
        elif obj.get('furniture_asset') in {'cabinet', 'media-console'} or any(word in obj.name.lower() for word in ['wardrobe', 'kitchen west base', 'kitchen north base', 'kitchen upper west', 'entrance storage']):
            if any(word in obj.name.lower() for word in ['pull', 'handle', 'brass', 'reveal', 'hardware', 'greige door']):
                continue
            obj.data.materials.clear(); obj.data.materials.append(burr); cabinet_box_uv(obj)
        if obj.get('furniture_asset') == 'sofa':
            for polygon in obj.data.polygons:
                polygon.use_smooth = True
        if 'foliage' in obj.name.lower():
            refine_foliage(obj, CONFIG['seed'] + int(obj.name.rsplit(' ',1)[-1]))
    for name in ['Light luxury warm gray linen', 'Linen / 米色织物', 'Norma warm greige leather', 'Material_0']:
        mat = bpy.data.materials.get(name)
        if not mat or not mat.use_nodes:
            continue
        bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf:
            bsdf.inputs['Roughness'].default_value = .68
            bsdf.inputs['Sheen Weight'].default_value = .12
        bump = next((n for n in mat.node_tree.nodes if n.type == 'BUMP'), None)
        if bump:
            bump.inputs['Strength'].default_value = .18
            bump.inputs['Distance'].default_value = .00055
    glass = bpy.data.materials.get('Window glass / 玻璃')
    if glass:
        bsdf = next(n for n in glass.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
        bsdf.inputs['Base Color'].default_value = (.995,.995,.995,1)
        bsdf.inputs['Roughness'].default_value = .018
        bsdf.inputs['Transmission Weight'].default_value = 1
        bsdf.inputs['IOR'].default_value = 1.45
        bsdf.inputs['Alpha'].default_value = 1
    for obj in scene.objects:
        if obj.type == 'MESH' and obj.name.lower().startswith(('north fixed glass', 'north sliding glass', 'south fixed glass', 'south sliding glass')):
            obj.data.materials.clear(); obj.data.materials.append(glass)
    add_exterior_backdrop(scene, 'Distant south exterior only', -15)
    add_exterior_backdrop(scene, 'Distant north exterior only', 4)
    for light in scene.objects:
        if light.type != 'LIGHT':
            continue
        if light.name in {'South daylight', 'North daylight', 'West daylight'}:
            light.data.color = (1.0,.96,.90)
        if light.name == 'South daylight': light.data.energy = 650
        if light.name == 'North daylight': light.data.energy = 450
        if light.name == 'West daylight': light.data.energy = 250
        if light.name == 'Dollhouse softbox': light.data.energy = 350
    scene.world.node_tree.nodes.get('Background').inputs['Color'].default_value = (.72,.76,.78,1)
    scene.world.node_tree.nodes.get('Background').inputs['Strength'].default_value = .22
    scene.render.engine = 'CYCLES'
    scene.cycles.device = CONFIG['renderer']['device']
    scene.render.threads_mode = 'FIXED'
    scene.render.threads = CONFIG['threads']
    scene.cycles.samples = CONFIG['stills']['samples']
    scene.cycles.use_denoising = True
    scene.cycles.adaptive_threshold = CONFIG['renderer']['adaptiveThreshold']
    for key, prop in [('maxBounces','max_bounces'),('diffuseBounces','diffuse_bounces'),('glossyBounces','glossy_bounces'),('transmissionBounces','transmission_bounces'),('transparentBounces','transparent_max_bounces')]:
        setattr(scene.cycles, prop, CONFIG['renderer'][key])
    scene.view_settings.view_transform = 'AgX'
    scene.view_settings.look = 'AgX - Medium High Contrast'
    scene.view_settings.exposure = -.35
    scene.render.resolution_x = CONFIG['stills']['width']; scene.render.resolution_y = CONFIG['stills']['height']; scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'; scene.render.image_settings.color_mode = 'RGB'
    scene.render.film_transparent = False
    add_local_fixtures(scene)
    preflight(scene)
    scene['workflow_signature'] = SIGNATURE
    scene['material_refinements'] = 'Explicit hashed marble/burl references; physical-scale slab and cabinet box UV; faint oak; patterned greige rug; clear neutral glazing; attached foliage; no layout changes'
    scene['workflow_inputs'] = json.dumps(INPUTS, sort_keys=True)
    set_camera(scene, 'living')
    bpy.ops.wm.save_as_mainfile(filepath=str(REFINED))
    record('refined-blend', REFINED, settings=CONFIG['renderer'], materials=[marble.name,wood.name,burr.name,rug.name])
    started = time.monotonic()
    render_file(scene, RUN / 'preview-living.png', CONFIG['preview']['width'], CONFIG['preview']['height'], CONFIG['preview']['samples'])
    record('preview-living', RUN / 'preview-living.png', time.monotonic()-started, camera=camera_metadata(scene), settings=render_metadata(scene))
    update_manifest()
    progress('prepare', 'completed', preview='preview-living.png')


def set_camera(scene, name):
    spec = CONFIG['cameras'][name]
    camera = bpy.data.objects.get('Workflow still camera')
    if not camera:
        camera = bpy.data.objects.new('Workflow still camera', bpy.data.cameras.new('Workflow still camera'))
        scene.collection.objects.link(camera)
    camera.data.type = 'PERSP'
    camera.location = xyz(spec['position'])
    camera.rotation_euler = (xyz(spec['target'])-camera.location).to_track_quat('-Z','Y').to_euler()
    camera.data.lens = spec['lensMm']
    camera.data.sensor_width = 36
    camera.data.clip_start = .05; camera.data.clip_end = 100
    scene.camera = camera
    bpy.context.view_layer.update()


def camera_metadata(scene):
    camera = scene.camera
    return {'name': camera.name, 'type': camera.data.type, 'positionBlender': list(camera.location), 'rotationEuler': list(camera.rotation_euler), 'lensMm': camera.data.lens, 'sensorWidthMm': camera.data.sensor_width}


def render_metadata(scene):
    return {'engine': scene.render.engine, 'device': scene.cycles.device, 'threads': scene.render.threads, 'samples': scene.cycles.samples, 'denoised': scene.cycles.use_denoising, 'adaptiveThreshold': scene.cycles.adaptive_threshold, 'colorManagement': scene.view_settings.view_transform}


def render_file(scene, path, width, height, samples, image_format='PNG', quality=95):
    global RENDER_COUNT
    RENDER_COUNT += 1
    scene.render.resolution_x = width; scene.render.resolution_y = height; scene.render.resolution_percentage = 100
    scene.cycles.samples = samples
    if image_format == 'OPEN_EXR_MULTILAYER' and hasattr(scene.render.image_settings, 'media_type'):
        scene.render.image_settings.media_type = 'MULTI_LAYER_IMAGE'
        image_format = 'OPEN_EXR'
    else:
        if hasattr(scene.render.image_settings, 'media_type'):
            scene.render.image_settings.media_type = 'IMAGE'
        scene.render.image_settings.file_format = image_format
    scene.render.image_settings.color_mode = 'RGB'
    if image_format == 'JPEG':
        scene.render.image_settings.quality = quality
    if image_format.startswith('OPEN_EXR'):
        scene.render.image_settings.color_depth = '32'; scene.render.image_settings.exr_codec = 'ZIP'
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def benchmark(scene):
    results = []
    for device in ['CPU', 'METAL']:
        try:
            scene.cycles.device = 'GPU' if device == 'METAL' else 'CPU'
            if device == 'METAL':
                prefs = bpy.context.preferences.addons['cycles'].preferences
                prefs.compute_device_type = 'METAL'; prefs.get_devices()
                for item in prefs.devices:
                    item.use = item.type == 'METAL'
            started = time.monotonic()
            render_file(scene, RUN / f'benchmark-{device.lower()}.png', 360, 240, 8)
            results.append({'device': device, 'seconds': round(time.monotonic()-started,3), 'success': True})
        except Exception as exc:
            results.append({'device': device, 'success': False, 'error': str(exc)})
    scene.cycles.device = CONFIG['renderer']['device']
    write_json(RUN / 'renderer-benchmark.json', {'results': results, 'selected': CONFIG['renderer']['device'], 'threads': CONFIG['threads'], 'note': 'Single low-resolution benchmark; selected known stable CPU backend, not an exhaustive experiment.'})


def assign_indices(scene):
    groups = {'sofa': 1, 'TV': 2, 'windows': 3, 'floor': 4, 'cabinet': 5, 'rug': 6}
    for obj in scene.objects:
        obj.pass_index = 0
        if obj.type != 'MESH': continue
        name = obj.name.lower()
        if obj.get('furniture_asset') == 'sofa': obj.pass_index = 1
        elif name.startswith('hisense'): obj.pass_index = 2
        elif 'glazing' in name or (name.startswith(('north fixed glass','north sliding glass','south fixed glass','south sliding glass'))): obj.pass_index = 3
        elif name == 'living marble floor' or name.startswith('finished oak floor'): obj.pass_index = 4
        elif obj.get('furniture_asset') in {'cabinet','media-console'} or any(word in name for word in ['wardrobe','kitchen west base','kitchen north base','kitchen upper west','entrance storage']): obj.pass_index = 5
        elif 'rug' in name: obj.pass_index = 6
    return groups


def output_node(tree, directory, image_format):
    node = tree.nodes.new('CompositorNodeOutputFile')
    node.directory = str(directory)
    node.file_name = ''
    node.format.media_type = 'IMAGE'
    node.format.file_format = image_format
    node.format.color_mode = 'RGBA'
    node.format.color_depth = '32' if image_format == 'OPEN_EXR' else '8'
    if image_format == 'OPEN_EXR': node.format.exr_codec = 'ZIP'
    return node


def add_item(node, socket_type, name, source, tree):
    item = node.file_output_items.new(socket_type, name)
    tree.links.new(source, node.inputs[name])
    return item


def compositor(scene, groups):
    layer = scene.view_layers[0]
    layer.use_pass_z = True; layer.use_pass_normal = True; layer.use_pass_object_index = True
    layer.use_pass_cryptomatte_object = True; layer.pass_cryptomatte_depth = 6
    tree = bpy.data.node_groups.new('Living delivery passes', 'CompositorNodeTree')
    scene.compositing_node_group = tree
    tree.interface.new_socket(name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
    output = tree.nodes.new('NodeGroupOutput')
    rl = tree.nodes.new('CompositorNodeRLayers'); rl.scene = scene; rl.layer = layer.name
    tree.links.new(rl.outputs['Image'], output.inputs['Image'])
    exr = output_node(tree, RUN / 'passes', 'OPEN_EXR')
    png = output_node(tree, RUN / 'passes', 'PNG')
    add_item(exr, 'FLOAT', 'living-depth', rl.outputs['Depth'], tree)
    add_item(exr, 'RGBA', 'living-normal-world', rl.outputs['Normal'], tree)
    for name, index in groups.items():
        compare = tree.nodes.new('ShaderNodeMath'); compare.operation = 'COMPARE'; compare.inputs[1].default_value = index; compare.inputs[2].default_value = .1
        tree.links.new(rl.outputs.get('IndexOB') or rl.outputs['Object Index'], compare.inputs[0])
        add_item(png, 'FLOAT', f'mask-{name.lower()}', compare.outputs[0], tree)
    return tree


def normalize_pass_names():
    for path in list((RUN / 'passes').glob('*0001.*')):
        target = path.with_name(path.name.replace('0001',''))
        if target.exists(): target.unlink()
        path.rename(target)


def validate_masks(groups):
    result = {}
    for name in groups:
        path = RUN / 'passes' / f'mask-{name.lower()}.png'
        image = bpy.data.images.load(str(path), check_existing=False)
        pixels = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(pixels)
        rgb = pixels.reshape(-1,4)[:,:3]
        count = int(np.count_nonzero(np.max(rgb,axis=1) > .5))
        result[name] = {'path': str(path.relative_to(RUN)), 'nonzeroPixels': count, 'nonempty': count > 0}
        bpy.data.images.remove(image)
    write_json(RUN / 'passes' / 'mask-validation.json', result)
    return result


def image_array(path):
    image = bpy.data.images.load(str(path), check_existing=False)
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    width, height = image.size
    bpy.data.images.remove(image)
    return pixels.reshape(height, width, 4)


def write_linear_png(path, values):
    values = np.clip(values, 0, 1)
    data = np.rint(values*255).astype(np.uint8)
    raw = b''.join(b'\x00'+row.tobytes() for row in data)
    def chunk(kind, payload):
        return struct.pack('>I', len(payload))+kind+payload+struct.pack('>I', zlib.crc32(kind+payload)&0xffffffff)
    header = struct.pack('>2I5B', data.shape[1], data.shape[0], 8, 0, 0, 0, 0)
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',header)+chunk(b'IDAT',zlib.compress(raw,9))+chunk(b'IEND',b''))


def validate_data_passes(scene):
    depth = image_array(RUN/'passes/living-depth.exr')
    normal = image_array(RUN/'passes/living-normal-world.exr')
    floor = image_array(RUN/'passes/mask-floor.png')[:,:,0] > .5
    normalized = np.clip((depth[:,:,0]-CONFIG['depth']['nearM'])/(CONFIG['depth']['farM']-CONFIG['depth']['nearM']),0,1)
    write_linear_png(RUN/'passes/living-depth-normalized.png', normalized)
    floor_normals = normal[floor][:,:3]
    normal_median = np.median(floor_normals,axis=0).tolist()
    if not (abs(normal_median[0]) < .08 and abs(normal_median[1]) < .08 and normal_median[2] > .9):
        raise RuntimeError(f'Normal pass is not the expected raw world-space floor normal: {normal_median}')
    camera = scene.camera
    height, width = floor.shape
    ys, xs = np.nonzero(floor)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    distance_errors, axial_errors = [], []
    for sample in np.linspace(0,len(xs)-1,min(128,len(xs)),dtype=int):
        x, y = int(xs[sample]), int(ys[sample])
        local = Vector(((x+.5)/width-.5, ((y+.5)/height-.5)*height/width, -1))
        local.x *= camera.data.sensor_width/camera.data.lens
        local.y *= camera.data.sensor_width/camera.data.lens
        direction = camera.matrix_world.to_quaternion() @ local.normalized()
        hit, location, _, _, _, _ = scene.ray_cast(depsgraph,camera.location,direction,distance=100)
        if not hit:
            continue
        measured = float(depth[y,x,0])
        ray_distance = (location-camera.location).length
        axial = (location-camera.location).dot(camera.matrix_world.to_quaternion() @ Vector((0,0,-1)))
        if np.isfinite(measured):
            distance_errors.append(abs(measured-ray_distance)); axial_errors.append(abs(measured-axial))
    distance_max = max(distance_errors,default=999)
    axial_max = max(axial_errors,default=999)
    if not axial_errors or axial_max > .03 or axial_max > distance_max:
        raise RuntimeError(f'Depth is not the measured camera-forward axial Z: ray error {distance_max}, axial error {axial_max}')
    return {
        'normal': {'file':'passes/living-normal-world.exr','storage':'32-bit float RGB unit vectors, raw linear EXR','convention':'Blender world space; +Z is world up','numericFloorCheck':{'medianVector':normal_median,'expected':[0,0,1],'sampleCount':len(floor_normals)}},
        'depth': {'file':'passes/living-depth.exr','storage':'32-bit float metres, raw linear EXR','definition':'Camera-forward axial Z distance measured in this Blender/Cycles scene; not Euclidean ray distance','numericRayCheck':{'sampleCount':len(distance_errors),'maxRayDistanceInterpretationErrorM':distance_max,'maxAxialDistanceErrorM':axial_max},'nearM':CONFIG['depth']['nearM'],'farM':CONFIG['depth']['farM'],'normalizedFile':'passes/living-depth-normalized.png','normalizedStorage':'8-bit linear grayscale PNG written directly from data; no AgX, exposure, gamma, or sRGB transform','normalizedFormula':'clamp((axialDepth-near)/(far-near),0,1)'}
    }


def living_pass_files():
    return ['living-layers.exr','passes/living-depth.exr','passes/living-depth-normalized.png','passes/living-normal-world.exr','passes/living-passes.json','passes/mask-validation.json']+[f'passes/mask-{name}.png' for name in ['sofa','tv','windows','floor','cabinet','rug']]


def render_living(scene):
    stale = RUN/'passes/living-normal-camera.exr'
    if stale.exists(): stale.unlink()
    groups = assign_indices(scene)
    compositor(scene, groups)
    started = time.monotonic()
    render_file(scene, RUN / 'living-layers.exr', CONFIG['stills']['width'], CONFIG['stills']['height'], CONFIG['stills']['samples'], 'OPEN_EXR_MULTILAYER')
    normalize_pass_names()
    if hasattr(scene.render.image_settings, 'media_type'): scene.render.image_settings.media_type = 'IMAGE'
    scene.render.image_settings.file_format = 'PNG'; scene.render.image_settings.color_mode = 'RGB'; scene.render.image_settings.color_depth = '8'
    bpy.data.images['Render Result'].save_render(str(RUN / 'living-refined.png'), scene=scene)
    seconds = time.monotonic()-started
    record('living-refined', RUN / 'living-refined.png', seconds, camera=camera_metadata(scene), settings=render_metadata(scene))
    record('living-layers', RUN / 'living-layers.exr', passes=['Combined','Depth','Normal','Object Index','CryptoObject00-02'])
    conventions = validate_data_passes(scene)
    for name in ['living-depth.exr','living-depth-normalized.png','living-normal-world.exr']:
        record(name, RUN / 'passes' / name, camera=camera_metadata(scene))
    masks = validate_masks(groups)
    for name in groups:
        record(f'mask-{name.lower()}', RUN / 'passes' / f'mask-{name.lower()}.png', nonempty=masks[name]['nonempty'], nonzeroPixels=masks[name]['nonzeroPixels'])
    metadata = {**conventions, 'mask': {'type':'hard binary ObjectIndex threshold at object ID; no coverage antialiasing and not Cryptomatte','files':{name:f'passes/mask-{name.lower()}.png' for name in groups}}, 'objectIndex': groups, 'multilayer': {'file':'living-layers.exr','passes':['Combined','Depth','Normal (world space)','Object Index','CryptoObject00-02']}, 'camera': camera_metadata(scene), 'maskValidation': masks, 'signature':SIGNATURE, 'inputHashes':INPUTS}
    write_json(RUN / 'passes' / 'living-passes.json', metadata)
    record('mask-validation',RUN/'passes/mask-validation.json')
    record('living-passes',RUN/'passes/living-passes.json')
    scene.compositing_node_group = None


def render_stills(only=None):
    for name in ['living','dining','study','master']:
        if only and name != only: continue
        filename = 'living-refined.png' if name == 'living' else f'{name}.png'
        path = RUN / filename
        key = 'living-refined' if name == 'living' else f'still-{name}'
        required = living_pass_files() if name == 'living' else ()
        if cached(key, path, required):
            print('CACHED', key, flush=True); continue
        progress('render', 'running', current=name)
        set_camera(bpy.context.scene, name)
        if name == 'living':
            render_living(bpy.context.scene)
        else:
            started = time.monotonic()
            render_file(bpy.context.scene, path, CONFIG['stills']['width'], CONFIG['stills']['height'], CONFIG['stills']['samples'])
            record(key, path, time.monotonic()-started, camera=camera_metadata(bpy.context.scene), settings=render_metadata(bpy.context.scene))


def camera_clearance(scene, position):
    origin = xyz(position)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    distances = []
    for index in range(16):
        angle = index*math.tau/16
        hit, loc, normal, face, obj, matrix = scene.ray_cast(depsgraph, origin, Vector((math.cos(angle),math.sin(angle),0)), distance=5)
        if hit: distances.append((loc-origin).length)
    down = scene.ray_cast(depsgraph, origin, Vector((0,0,-1)), distance=3)
    up = scene.ray_cast(depsgraph, origin, Vector((0,0,1)), distance=3)
    return min(distances or [5]), down[0], up[0]


def safe_position(scene, spec):
    clearance, floor, ceiling = camera_clearance(scene, spec['position'])
    if clearance >= .23 and floor and ceiling:
        return spec['position'], False
    for radius in [.25,.5,.75,1.0]:
        for index in range(16):
            angle = index*math.tau/16
            candidate = [spec['position'][0]+radius*math.cos(angle), spec['position'][1]+radius*math.sin(angle), spec['position'][2]]
            clearance, floor, ceiling = camera_clearance(scene, candidate)
            if clearance >= .23 and floor and ceiling:
                return candidate, True
    raise RuntimeError(f"No safe panorama position near {spec['room']}")


def render_panoramas():
    scene = bpy.context.scene
    camera = bpy.data.objects.get('Workflow panorama camera')
    if not camera:
        camera = bpy.data.objects.new('Workflow panorama camera', bpy.data.cameras.new('Workflow panorama camera'))
        scene.collection.objects.link(camera)
    camera.data.type = 'PANO'; camera.data.panorama_type = 'EQUIRECTANGULAR'
    camera.data.clip_start = .05; camera.data.clip_end = 100
    scene.camera = camera
    report = load_json(RUN / 'pano-report.json', {'nodes': {}})
    if report.get('signature') != SIGNATURE:
        report = {'nodes':{}}
    report['signature'] = SIGNATURE
    (RUN / 'panoramas').mkdir(exist_ok=True)
    for name, spec in CONFIG['panoramaNodes'].items():
        path = RUN / 'panoramas' / f'{name}.jpg'
        if cached(f'pano-{name}', path):
            print('CACHED', f'pano-{name}', flush=True)
            continue
        progress('render', 'running', current=f'panorama:{name}')
        position, repaired = safe_position(scene, spec)
        camera.location = xyz(position)
        yaw = math.radians(spec['yaw'])
        direction = Vector((math.sin(yaw),math.cos(yaw),0))
        camera.rotation_euler = direction.to_track_quat('-Z','Y').to_euler()
        small = spec.get('small', False)
        width = CONFIG['panorama']['smallWidth'] if small else CONFIG['panorama']['width']
        height = CONFIG['panorama']['smallHeight'] if small else CONFIG['panorama']['height']
        started = time.monotonic()
        render_file(scene, path, width, height, CONFIG['panorama']['samples'], 'JPEG', CONFIG['panorama']['jpegQuality'])
        seconds = time.monotonic()-started
        record(f'pano-{name}', path, seconds, camera=camera_metadata(scene), settings=render_metadata(scene), projection='equirectangular', positionWeb=position, repairedPosition=repaired)
        clearance = camera_clearance(scene, position)[0]
        report['nodes'][name] = {'position': position, 'repairedPosition': repaired, 'minimumHorizontalClearanceM': clearance, 'width': width, 'height': height, 'projection': 'equirectangular', 'cameraMatrixBlender': [list(row) for row in camera.matrix_world]}
        write_json(RUN / 'pano-report.json', report)


def lighting_previews():
    progress('lighting-previews', 'running')
    require_current_blend()
    bpy.ops.wm.open_mainfile(filepath=str(REFINED))
    scene = bpy.context.scene
    preflight(scene)
    camera = bpy.data.objects.get('Workflow panorama camera')
    if not camera:
        camera = bpy.data.objects.new('Workflow panorama camera', bpy.data.cameras.new('Workflow panorama camera'))
        scene.collection.objects.link(camera)
    camera.data.type = 'PANO'; camera.data.panorama_type = 'EQUIRECTANGULAR'
    camera.data.clip_start = .05; camera.data.clip_end = 100
    scene.camera = camera
    output = RUN/'qa-lighting'; output.mkdir(exist_ok=True)
    for spec in CONFIG['localFixtures']:
        room = spec['id'].removesuffix('-flush')
        node = CONFIG['panoramaNodes'][room]
        position, _ = safe_position(scene, node)
        camera.location = xyz(position)
        yaw = math.radians(node['yaw'])
        camera.rotation_euler = Vector((math.sin(yaw),math.cos(yaw),0)).to_track_quat('-Z','Y').to_euler()
        path = output/f'{room}.png'
        started = time.monotonic()
        render_file(scene,path,1024,512,CONFIG['preview']['samples'])
        record(f'qa-lighting-{room}',path,time.monotonic()-started,camera=camera_metadata(scene),settings=render_metadata(scene))
    progress('lighting-previews', 'completed', rooms=len(CONFIG['localFixtures']))


def render():
    progress('render', 'running')
    require_current_blend()
    bpy.ops.wm.open_mainfile(filepath=str(REFINED))
    if bpy.context.scene.get('workflow_signature') != SIGNATURE:
        raise RuntimeError('refined.blend internal workflow signature is stale; run prepare')
    resume_paths = ['living-refined.png','dining.png','study.png','master.png']+living_pass_files()+[f'panoramas/{name}.jpg' for name in CONFIG['panoramaNodes']]
    resume_before = {path:sha(RUN/path) for path in resume_paths} if ARGS.resume_check else None
    render_stills(ARGS.only)
    if not ARGS.only:
        render_panoramas()
    update_manifest()
    if ARGS.resume_check:
        resume_after = {path:sha(RUN/path) for path in resume_paths}
        report = {'pass':RENDER_COUNT == 0 and resume_before == resume_after,'renderInvocations':RENDER_COUNT,'allRequiredHashesUnchanged':resume_before == resume_after,'signature':SIGNATURE,'inputHashes':INPUTS,'files':{path:{'before':resume_before[path],'after':resume_after[path]} for path in resume_paths},'execution':EVIDENCE,'checkedAt':now()}
        write_json(RUN/'resume-check.json',report)
        if not report['pass']: raise RuntimeError('Resume check rendered outputs or changed required hashes')
    progress('render', 'completed')


def parse_glb(path):
    data = Path(path).read_bytes()
    length = struct.unpack_from('<I', data, 12)[0]
    return json.loads(data[20:20+length].decode('utf-8'))


def export():
    progress('export', 'running')
    require_current_blend()
    if cached('glb', RUN / 'model.glb') and cached('panoramas-json', RUN / 'panoramas.json'):
        progress('export', 'completed', cached=True); return
    bpy.ops.wm.open_mainfile(filepath=str(REFINED))
    if bpy.context.scene.get('workflow_signature') != SIGNATURE:
        raise RuntimeError('refined.blend internal workflow signature is stale; run prepare')
    scene = bpy.context.scene
    targets = {'sofa':16000,'coffee-table':12000,'round-table':14000,'dining-chair':12000,'cabinet':14000,'media-console':10000,'bed':16000}
    for obj in scene.objects:
        if obj.type == 'MESH' and obj.get('furniture_asset'):
            triangles = sum(len(p.vertices)-2 for p in obj.data.polygons)
            target = targets.get(obj['furniture_asset'],12000)
            if triangles > target:
                modifier = obj.modifiers.new('Web only reduction','DECIMATE'); modifier.ratio = target/triangles; modifier.use_collapse_triangulate = True
    for image in bpy.data.images:
        if image.source == 'FILE' and image.has_data:
            if max(image.size) > 1024:
                factor = 1024/max(image.size); image.scale(round(image.size[0]*factor),round(image.size[1]*factor))
            image.file_format = 'JPEG'
            if not image.packed_file: image.pack()
    bpy.ops.object.select_all(action='DESELECT')
    for obj in scene.objects:
        if (obj.type == 'MESH' or obj.get('door_id')) and not obj.get('exclude_from_web'): obj.select_set(True)
    started = time.monotonic()
    bpy.ops.export_scene.gltf(filepath=str(RUN/'model.glb'), export_format='GLB', use_selection=True, export_cameras=False, export_lights=False, export_yup=True, export_apply=True, export_extras=True, export_image_format='JPEG', export_jpeg_quality=88)
    record('glb', RUN/'model.glb', time.monotonic()-started, settings={'embeddedTextures':True,'draco':False,'webMaterialApproximation':'PBR base color and roughness; procedural micro-bump omitted; glazing requires viewer environment lighting'})
    lighting = load_json(SOURCE.parent/'lighting.json')
    design = CONFIG['localFixtureDesign']
    for light in scene.objects:
        fixture_id = light.get('workflow_local_fixture') if light.type == 'LIGHT' else None
        if not fixture_id:
            continue
        spec = next(item for item in CONFIG['localFixtures'] if item['id'] == fixture_id)
        lighting['fixtures'].append({'id':fixture_id,'label':light.name,'position':[light.location.x,light.location.z,-light.location.y],'target':[light.location.x,light.location.z-1,-light.location.y],'color':design['color'],'colorTemperatureK':design['colorTemperatureK'],'blenderType':light.data.type,'blenderEnergyW':light.data.energy,'areaSizeM':[light.data.size,light.data.size],'webType':'spot','webIntensityCandela':light['webIntensityCandela'],'angleDeg':design['angleDeg'],'penumbra':design['penumbra'],'distanceM':design['distanceM'],'role':'interior'})
    lighting['provenance'] = {'derivedFrom': 'authoritative case lighting.json plus measured final-scene local fixtures', 'refinedWorkflow': SIGNATURE, 'execution': EVIDENCE}
    lighting['notes'].append('Five added 3900K local fixtures use the existing interior role: 12 percent output in day and 65 percent in evening; web spot candela values are calibrated approximations, not photometric conversions.')
    lighting['notes'].append('Refined web materials are realtime approximations of the full-quality Cycles scene; no AI enhancement or external assets.')
    write_json(RUN/'lighting.json', lighting); record('lighting', RUN/'lighting.json')
    pano_report = load_json(RUN/'pano-report.json', {'nodes':{}})['nodes']
    nodes = {}
    for name, spec in CONFIG['panoramaNodes'].items():
        actual = pano_report[name]
        position = actual['position']
        neighbors = []
        for neighbor in spec['neighbors']:
            portal = CONFIG['navigationPortals'][f'{name}>{neighbor}']
            origin = xyz(position)
            target = xyz(portal)
            delta = target-origin
            hit, location, _, _, hit_object, _ = scene.ray_cast(bpy.context.evaluated_depsgraph_get(),origin,delta.normalized(),distance=delta.length-.04)
            if hit:
                raise RuntimeError(f'Navigation portal obstructed for {name}>{neighbor} by {hit_object.name} at {list(location)}')
            dx, dz = portal[0]-position[0], portal[1]-position[1]
            neighbors.append({'id':neighbor,'bearingDegrees':round((math.degrees(math.atan2(dx,-dz))+360)%360,3),'portalPosition':{'x':portal[0],'y':portal[2],'z':portal[1]},'navigationTargetPosition':{'x':portal[0],'y':portal[2],'z':portal[1]},'navigationSemantics':'Discrete room link aimed at the verified doorway/open aisle, not a claim of continuous camera travel','directPortalRayClear':True})
        target = CONFIG['suggestedViewTargets'][name]
        dx, dz = target[0]-position[0], target[1]-position[1]
        suggested_yaw = round((math.degrees(math.atan2(dx,-dz))+360)%360,3)
        nodes[name] = {'room':spec['room'],'asset':f'panoramas/{name}.jpg','width':actual['width'],'height':actual['height'],'projection':'equirectangular','horizontalFovDegrees':360,'verticalFovDegrees':180,'position':{'x':position[0],'y':position[2],'z':position[1]},'initialYawDegrees':spec['yaw'],'suggestedViewTarget':{'x':target[0],'y':target[2],'z':target[1]},'suggestedWorldYawDegrees':suggested_yaw,'suggestedViewSemantics':'World bearing to suggestedViewTarget; viewer computes local yaw as suggestedWorldYawDegrees minus the rendered heading initialYawDegrees','yawConvention':'World and Three.js navigation yaw: 0 looks -Z; positive turns toward +X','cameraTransformBlender':actual['cameraMatrixBlender'],'neighbors':neighbors,'minimumHorizontalClearanceM':actual['minimumHorizontalClearanceM'],'repairedPosition':actual['repairedPosition']}
    write_json(RUN/'panoramas.json', {'version':2,'coordinateSystem':'Three.js x,y-up,z; source config x,planZ,height; Blender x,-planZ,height','renderHeadingConvention':'Config yaw is the Blender render heading: direction (sin(yaw),cos(yaw),0) in Blender XY','standingEyeHeightM':CONFIG['panorama']['heightM'],'signature':SIGNATURE,'nodes':nodes,'execution':EVIDENCE})
    record('panoramas-json', RUN/'panoramas.json')
    glb = parse_glb(RUN/'model.glb')
    triangles = sum(glb['accessors'][primitive['indices']]['count']//3 for mesh in glb.get('meshes',[]) for primitive in mesh.get('primitives',[]) if 'indices' in primitive)
    report = {'bytes':(RUN/'model.glb').stat().st_size,'sizeMiB':(RUN/'model.glb').stat().st_size/1048576,'triangles':triangles,'imageCount':len(glb.get('images',[])),'allImagesEmbedded':all('bufferView' in im and 'uri' not in im for im in glb.get('images',[])),'draco':'KHR_draco_mesh_compression' in glb.get('extensionsUsed',[]),'execution':EVIDENCE}
    write_json(RUN/'export-report.json',report)
    update_manifest()
    progress('export','completed',glbBytes=report['bytes'])


def verify():
    progress('verify','running')
    require_current_blend()
    baseline = load_json(RUN/'source-baseline.json')
    bpy.ops.wm.open_mainfile(filepath=str(REFINED))
    scene = bpy.context.scene
    furniture = {o.name:bounds([o]) for o in scene.objects if o.get('furniture_asset')}
    furniture_checks = {}
    for name, original in baseline['furniture'].items():
        actual = furniture.get(name)
        error = max(abs(actual[k][i]-original[k][i]) for k in ['min','max'] for i in range(3)) if actual else 999
        furniture_checks[name] = {'maxBoundsErrorM':error,'pass':error <= .001}
    doors = {o['door_id']:o['door_slide_x'] for o in scene.objects if o.get('door_id')}
    net = bounds([scene.objects['Ceiling / 室内天花']])['min'][2]-bounds([scene.objects['Living marble floor']])['max'][2]
    glb = parse_glb(RUN/'model.glb')
    extras = [n.get('extras',{}) for n in glb.get('nodes',[])]
    glb_furniture = {(e.get('furniture_asset'),e.get('instance')) for e in extras if e.get('furniture_asset')}
    glb_doors = {e.get('door_id'):e.get('door_slide_x') for e in extras if e.get('door_id')}
    expected = ['living-refined.png','dining.png','study.png','master.png','model.glb','lighting.json','panoramas.json']+living_pass_files()+[f'panoramas/{n}.jpg' for n in CONFIG['panoramaNodes']]
    mask_validation = load_json(RUN/'passes/mask-validation.json',{})
    current_outputs = {item.get('path'):item for item in state().get('outputs',{}).values()}
    outputs_current = all(path in current_outputs and current_outputs[path].get('configSignature') == SIGNATURE and current_outputs[path].get('sha256') == sha(RUN/path) for path in expected)
    pano_meta = load_json(RUN/'panoramas.json',{})
    reciprocal = all(any(link['id']==name for link in pano_meta['nodes'][neighbor['id']]['neighbors']) for name,node in pano_meta.get('nodes',{}).items() for neighbor in node['neighbors'])
    checks = {'sourceBaselineCurrent':baseline.get('sourceSha256')==sha(SOURCE),'refinedBlendCurrent':scene.get('workflow_signature')==SIGNATURE and cached('refined-blend',REFINED),'allOutputsCurrentSignature':outputs_current,'fourteenFurnitureGroups':len(furniture)==14 and len(glb_furniture)==14,'furnitureBoundsWithin1mm':all(v['pass'] for v in furniture_checks.values()),'netHeight2p75':abs(net-2.75)<=.001,'twoDoorParentsTravelPreserved':len(doors)==2 and doors==baseline['doors'] and glb_doors==baseline['doors'],'cutawayTagsPresent':any('cutaway_hide' in e for e in extras) and any(e.get('keep_in_cutaway') for e in extras),'allImagesEmbedded':bool(glb.get('images')) and all('bufferView' in im and 'uri' not in im for im in glb['images']),'noExrTextures':all(im.get('mimeType')!='image/exr' for im in glb.get('images',[])),'noDraco':'KHR_draco_mesh_compression' not in glb.get('extensionsUsed',[]),'under25MiB':(RUN/'model.glb').stat().st_size<=25*1048576,'targetUnder23MiB':(RUN/'model.glb').stat().st_size<=23*1048576,'allRequiredFiles':all((RUN/p).is_file() for p in expected),'ninePanoramas':len(list((RUN/'panoramas').glob('*.jpg')))==9,'objectMasksNonempty':len(mask_validation)==6 and all(v['nonempty'] for v in mask_validation.values()),'reciprocalPortalConnectivity':reciprocal and pano_meta.get('signature')==SIGNATURE,'suggestedViewTargets':all('suggestedViewTarget' in node for node in pano_meta.get('nodes',{}).values())}
    manifest = load_json(RUN/'scene-manifest.json')
    manifest['refinedInventory'] = inventory(scene)
    manifest['refinedBlendSha256'] = sha(REFINED)
    write_json(RUN/'scene-manifest.json',manifest)
    report = {'status':'geometry_and_asset_checks_passed_pending_parent_visual_review' if all(checks.values()) else 'validation_failed','pass':all(checks.values()),'signature':SIGNATURE,'inputHashes':INPUTS,'checks':checks,'furniture':furniture_checks,'doors':doors,'netHeightM':net,'materialChanges':['Explicit generated Calacatta ivory reference at 1.8x0.9m physical slab UV','Explicit generated champagne burl reference with physical box UV on cabinet meshes only','Faint real-scale oak grain','Low-contrast organic greige rug pattern and textile micro-normal','Clear neutral exterior glazing','Branch-attached exterior foliage'],'cameraChanges':CONFIG['cameras'],'panoramaNodes':len(CONFIG['panoramaNodes']),'visualReview':{'status':'not_performed_by_blender_workflow','owner':'parent'},'execution':EVIDENCE,'verifiedAt':now()}
    write_json(RUN/'verification.json',report)
    update_manifest()
    progress('verify','completed' if report['pass'] else 'failed',checks=checks)
    if not report['pass']:
        raise RuntimeError('Verification failed: '+', '.join(k for k,v in checks.items() if not v))


def update_manifest():
    path = RUN/'scene-manifest.json'
    data = load_json(path,{})
    data['configSha256'] = sha(RUN/'config.json'); data['workflowSha256'] = sha(__file__); data['sourceSha256'] = sha(SOURCE); data['signature'] = SIGNATURE; data['inputHashes'] = INPUTS; data['state'] = state(); data['updatedAt'] = now(); data['execution'] = EVIDENCE
    write_json(path,data)


def main():
    global ARGS
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage',choices=['inspect','prepare','lighting-previews','render','export','verify','all'],default='all')
    parser.add_argument('--only',choices=['living','dining','study','master'])
    parser.add_argument('--force',action='store_true')
    parser.add_argument('--resume-check',action='store_true')
    ARGS = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    started = time.monotonic()
    try:
        stages = ['inspect','prepare','render','export','verify'] if ARGS.stage == 'all' else [ARGS.stage]
        for stage_name in stages:
            globals()[stage_name.replace('-', '_')]()
        progress(ARGS.stage,'completed',totalSeconds=round(time.monotonic()-started,3),visualReview='pending parent')
    except Exception as exc:
        progress(ARGS.stage,'failed',error=str(exc))
        raise


if __name__ == '__main__':
    main()
