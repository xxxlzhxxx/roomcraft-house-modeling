"""Build a textured, editable Blender scene and GLB from roomcraft.scene.v1."""

import argparse
import json
import math
import random
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--render", action="store_true")
    return parser.parse_args(argv)


def srgb(hex_color):
    value = hex_color.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def collection(name):
    result = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(result)
    return result


def move_to_collection(obj, target):
    for current in list(obj.users_collection):
        current.objects.unlink(obj)
    target.objects.link(obj)


def material(name, color, roughness=0.5, metallic=0.0, texture=None):
    result = bpy.data.materials.new(name)
    result.use_nodes = True
    rgba = (*color, 1.0)
    result.diffuse_color = rgba
    shader = result.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = rgba
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    if texture:
        node = result.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = texture
        node.interpolation = "Linear"
        result.node_tree.links.new(node.outputs["Color"], shader.inputs["Base Color"])
    return result


def generated_texture(name, kind, output, size=256):
    rng = random.Random(1701 + sum(ord(char) for char in name))
    image = bpy.data.images.new(name, width=size, height=size, alpha=True)
    pixels = []
    for y in range(size):
        for x in range(size):
            u = x / size
            v = y / size
            noise = rng.uniform(-0.018, 0.018)
            if kind == "marble":
                field = u * 8.0 + v * 2.3 + 0.55 * math.sin(v * 15.0) + 0.22 * math.sin(u * 29.0 + v * 7.0)
                vein = math.exp(-((math.sin(field * math.pi) / 0.12) ** 2))
                fine = math.exp(-((math.sin((field * 2.7 + v) * math.pi) / 0.09) ** 2)) * 0.25
                shade = 0.82 - 0.27 * vein - 0.12 * fine + noise
                rgb = (shade * 1.02, shade, shade * 0.96)
            elif kind == "wood":
                grain = math.sin((u * 36.0 + 2.8 * math.sin(v * 5.0) + math.sin(u * 7.0 + v * 13.0)) * math.pi)
                band = 0.5 + 0.5 * grain
                shade = 0.28 + 0.12 * band + noise
                rgb = (shade * 1.35, shade * 0.93, shade * 0.62)
            elif kind == "burl":
                dx = u - 0.52
                dy = v - 0.47
                radius = math.sqrt(dx * dx * 1.4 + dy * dy)
                angle = math.atan2(dy, dx)
                grain = math.sin((radius * 42.0 + 2.2 * math.sin(angle * 3.0) + u * 5.0) * math.pi)
                shade = 0.25 + 0.09 * (0.5 + 0.5 * grain) + noise
                rgb = (shade * 1.28, shade * 0.94, shade * 0.76)
            elif kind == "tile":
                grout = x % 64 < 3 or y % 64 < 3
                shade = 0.43 if grout else 0.72 + noise
                rgb = (shade * 0.94, shade * 0.98, shade)
            else:
                line = (x + 2 * y) % 96 < 5 or (3 * x - y) % 160 < 3
                shade = 0.47 if line else 0.73 + noise
                rgb = (shade * 1.04, shade, shade * 0.91)
            pixels.extend((*rgb, 1.0))
    image.pixels.foreach_set(pixels)
    image.filepath_raw = str(output / f"{name}.png")
    image.file_format = "PNG"
    image.save()
    image.pack()
    return image


def assign_material(obj, mat):
    if mat:
        obj.data.materials.append(mat)


def finish_mesh(obj, name, target, mat=None, parent=None, bevel=0.0, smooth=False):
    obj.name = name
    move_to_collection(obj, target)
    if parent:
        obj.parent = parent
    assign_material(obj, mat)
    if bevel:
        modifier = obj.modifiers.new("Soft manufactured edges", "BEVEL")
        modifier.width = bevel
        modifier.segments = 3
        modifier.harden_normals = True
        normal = obj.modifiers.new("Weighted corner normals", "WEIGHTED_NORMAL")
        normal.keep_sharp = True
    if smooth and obj.type == "MESH":
        for polygon in obj.data.polygons:
            polygon.use_smooth = True
    return obj


def box(name, location, dimensions, target, mat=None, parent=None, bevel=0.0, rotation=0.0):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.rotation_euler.z = rotation
    return finish_mesh(obj, name, target, mat, parent, bevel)


def cylinder(name, location, radius, depth, target, mat=None, parent=None, bevel=0.0, vertices=48):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=location)
    return finish_mesh(bpy.context.object, name, target, mat, parent, bevel, True)


def sphere(name, location, scale, target, mat=None, parent=None):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=40, ring_count=20, radius=1.0, location=location)
    obj = bpy.context.object
    obj.scale = scale
    return finish_mesh(obj, name, target, mat, parent, 0.0, True)


def torus(name, location, major_radius, minor_radius, scale, target, mat=None, parent=None):
    bpy.ops.mesh.primitive_torus_add(major_segments=48, minor_segments=12, major_radius=major_radius, minor_radius=minor_radius, location=location)
    obj = bpy.context.object
    obj.scale = scale
    return finish_mesh(obj, name, target, mat, parent, 0.0, True)


def empty(name, location, target):
    obj = bpy.data.objects.new(name, None)
    target.objects.link(obj)
    obj.location = location
    obj.empty_display_size = 0.12
    return obj


def furniture_root(item, target, base_z=0.0):
    root = empty(item["id"], (item["center"][0], item["center"][1], base_z), target)
    root.rotation_euler.z = math.radians(item.get("rotation_deg", 0.0))
    root["semantic_id"] = item["id"]
    root["semantic_type"] = "furniture"
    root["furniture_type"] = item["type"]
    root["declared_size_m"] = list(item["size"])
    root["material_description"] = item.get("material", "")
    root["parametric"] = True
    return root


def child_metadata(obj, root):
    obj["semantic_id"] = root["semantic_id"]
    obj["semantic_type"] = "furniture_part"
    obj["furniture_type"] = root["furniture_type"]


def add_sofa(item, root, target, mats):
    w, d, h = item["size"]
    upholstery = mats["fabric_cream"]
    parts = [
        box("sofa plinth", (0, 0, 0.18), (w * 0.94, d * 0.82, 0.24), target, mats["dark_wood"], root, 0.035),
        box("sofa back", (0, -d * 0.36, h * 0.64), (w, d * 0.2, h * 0.66), target, upholstery, root, 0.075),
    ]
    for side in (-1, 1):
        parts.append(box("sofa arm", (side * (w / 2 - 0.075), 0.02, h * 0.52), (0.15, d * 0.9, h * 0.58), target, upholstery, root, 0.065))
        parts.append(box("sofa front foot", (side * (w / 2 - 0.13), d * 0.31, 0.055), (0.055, 0.055, 0.11), target, mats["metal_dark"], root, 0.012))
    cushion_width = (w - 0.34) / 2
    for side in (-1, 1):
        parts.append(box("sofa seat cushion", (side * cushion_width / 2, d * 0.08, h * 0.46), (cushion_width - 0.015, d * 0.64, h * 0.22), target, upholstery, root, 0.055))
        parts.append(box("sofa back cushion", (side * cushion_width / 2, -d * 0.22, h * 0.7), (cushion_width - 0.02, d * 0.16, h * 0.43), target, upholstery, root, 0.055))
    return parts


def add_tv(item, root, target, mats):
    w, d, h = item["size"]
    parts = [
        box("tv screen body", (0, 0, h * 0.66), (w, d * 0.65, h * 0.6), target, mats["metal_dark"], root, 0.018),
        box("tv display", (0, d * 0.34, h * 0.66), (w * 0.96, 0.008, h * 0.54), target, mats["screen"], root, 0.008),
        box("tv stand", (0, 0, h * 0.23), (0.08, 0.07, h * 0.28), target, mats["metal_dark"], root, 0.012),
    ]
    for side in (-1, 1):
        parts.append(box("tv foot", (side * w * 0.25, 0, 0.035), (w * 0.28, d, 0.07), target, mats["metal_dark"], root, 0.015))
    return parts


def add_coffee_table(item, root, target, mats):
    w, d, h = item["size"]
    parts = [
        box("stone coffee slab", (0, 0, h - 0.035), (w, d, 0.07), target, mats["marble"], root, 0.025),
        box("coffee lower shelf", (0, 0, 0.12), (w * 0.75, d * 0.72, 0.035), target, mats["dark_wood"], root, 0.012),
    ]
    for x in (-w * 0.36, w * 0.36):
        for y in (-d * 0.32, d * 0.32):
            parts.append(cylinder("coffee bronze leg", (x, y, h * 0.47), 0.018, h * 0.85, target, mats["bronze"], root, 0.004, 24))
    return parts


def add_rug(item, root, target, mats):
    w, d, h = item["size"]
    return [box("patterned low pile rug", (0, 0, h / 2), (w, d, h), target, mats["rug"], root, min(0.018, h / 3))]


def add_round_table(item, root, target, mats):
    w, _, h = item["size"]
    return [
        cylinder("round dining stone top", (0, 0, h - 0.035), w / 2, 0.07, target, mats["marble"], root, 0.018, 64),
        cylinder("dining pedestal", (0, 0, h * 0.46), 0.085, h * 0.78, target, mats["bronze"], root, 0.012),
        cylinder("dining pedestal foot", (0, 0, 0.035), w * 0.24, 0.07, target, mats["metal_dark"], root, 0.015),
    ]


def add_chair(item, root, target, mats):
    w, d, h = item["size"]
    parts = [
        box("chair seat", (0, 0, 0.46), (w, d, 0.09), target, mats["fabric_taupe"], root, 0.045),
        box("chair back", (0, -d / 2 + 0.045, h * 0.74), (w * 0.92, 0.085, h * 0.46), target, mats["fabric_taupe"], root, 0.04),
    ]
    for x in (-w * 0.36, w * 0.36):
        for y in (-d * 0.34, d * 0.34):
            parts.append(cylinder("chair leg", (x, y, 0.225), 0.018, 0.45, target, mats["dark_wood"], root, 0.004, 20))
    return parts


def add_bed(item, root, target, mats):
    w, d, h = item["size"]
    parts = [
        box("bed upholstered base", (0, 0, 0.23), (w, d, 0.38), target, mats["fabric_taupe"], root, 0.065),
        box("bed headboard", (0, -d / 2 + 0.07, h * 0.57), (w, 0.14, h * 0.86), target, mats["fabric_cream"], root, 0.065),
        box("bed mattress", (0, 0.02, 0.51), (w * 0.94, d * 0.9, 0.26), target, mats["linen_white"], root, 0.09),
        box("bed coverlet", (0, d * 0.18, 0.655), (w * 0.95, d * 0.52, 0.055), target, mats["fabric_sage"], root, 0.035),
    ]
    for side in (-1, 1):
        parts.append(box("bed pillow", (side * w * 0.23, -d * 0.28, 0.7), (w * 0.38, d * 0.22, 0.13), target, mats["linen_white"], root, 0.055))
    return parts


def add_cabinet(item, root, target, mats, wardrobe=False):
    w, d, h = item["size"]
    body_mat = mats["burl"]
    carcass_height = h if wardrobe else h - 0.06
    parts = [box("cabinet carcass", (0, -0.012, carcass_height / 2), (w, d - 0.024, carcass_height), target, body_mat, root, 0.018)]
    if wardrobe:
        parts.append(box("wardrobe front", (0, d / 2 - 0.012, h / 2), (w - 0.035, 0.024, h - 0.04), target, body_mat, root, 0.01))
        parts.append(box("wardrobe handle", (w * 0.3, d / 2 + 0.006, h * 0.53), (0.018, 0.025, 0.3), target, mats["bronze"], root, 0.005))
    else:
        parts.append(box("counter worktop", (0, 0, h - 0.025), (w + 0.015, d + 0.015, 0.05), target, mats["marble"], root, 0.012))
        count = max(1, round(w / 0.55))
        for index in range(count):
            panel_width = (w - 0.04) / count
            x = -w / 2 + 0.02 + panel_width * (index + 0.5)
            parts.append(box("cabinet front", (x, d / 2 - 0.012, h * 0.48), (panel_width - 0.012, 0.024, h * 0.82), target, body_mat, root, 0.008))
            parts.append(box("cabinet pull", (x, d / 2 + 0.006, h * 0.76), (min(0.16, panel_width * 0.4), 0.022, 0.014), target, mats["bronze"], root, 0.004))
    return parts


def add_sink(item, root, target, mats):
    w, d, h = item["size"]
    basin_mat = mats["porcelain"] if "bath" in item["id"] else mats["steel"]
    return [
        box("sink rim", (0, 0, 0.025), (w, d, 0.05), target, basin_mat, root, 0.045),
        box("sink basin shadow", (0, 0.015, 0.055), (w * 0.76, d * 0.68, 0.025), target, mats["basin"], root, 0.04),
        cylinder("faucet stem", (0, -d * 0.38, h * 0.65), 0.014, h * 0.9, target, mats["chrome"], root, 0.003, 24),
        box("faucet spout", (0, -d * 0.22, h * 1.05), (0.028, d * 0.32, 0.028), target, mats["chrome"], root, 0.01),
    ]


def add_stove(item, root, target, mats):
    w, d, h = item["size"]
    parts = [box("cooktop glass", (0, 0, 0.02), (w, d, 0.04), target, mats["screen"], root, 0.015)]
    for x in (-w * 0.23, w * 0.23):
        for y in (-d * 0.23, d * 0.23):
            parts.append(cylinder("cooktop burner", (x, y, 0.05), min(w, d) * 0.12, 0.018, target, mats["metal_dark"], root, 0.003, 32))
    return parts


def add_fridge(item, root, target, mats):
    w, d, h = item["size"]
    return [
        box("fridge cabinet", (0, 0, h / 2), (w, d, h), target, mats["steel"], root, 0.035),
        box("fridge upper door", (0, d / 2 - 0.012, h * 0.64), (w * 0.96, 0.035, h * 0.66), target, mats["steel"], root, 0.025),
        box("fridge freezer door", (0, d / 2 - 0.012, h * 0.16), (w * 0.96, 0.035, h * 0.27), target, mats["steel"], root, 0.02),
        box("fridge handle", (w * 0.32, d / 2 + 0.018, h * 0.6), (0.025, 0.035, h * 0.35), target, mats["chrome"], root, 0.008),
    ]


def add_toilet(item, root, target, mats):
    w, d, h = item["size"]
    return [
        sphere("toilet pedestal", (0, 0.03, 0.2), (w * 0.32, d * 0.3, 0.22), target, mats["porcelain"], root),
        sphere("toilet bowl", (0, d * 0.08, 0.39), (w * 0.5, d * 0.39, 0.19), target, mats["porcelain"], root),
        sphere("toilet bowl opening", (0, d * 0.12, 0.51), (w * 0.32, d * 0.26, 0.025), target, mats["basin"], root),
        torus("toilet seat", (0, d * 0.1, 0.535), 0.16, 0.025, (w / 0.42, d / 0.68, 0.55), target, mats["porcelain"], root),
        box("toilet cistern", (0, -d * 0.34, h * 0.67), (w * 0.9, d * 0.25, h * 0.55), target, mats["porcelain"], root, 0.045),
        cylinder("toilet flush", (0, -d * 0.34, h * 0.96), 0.018, 0.008, target, mats["chrome"], root, 0.002, 24),
    ]


def add_tub(item, root, target, mats):
    w, d, h = item["size"]
    parts = [
        box("tub body", (0, 0, h * 0.42), (w, d, h * 0.84), target, mats["porcelain"], root, 0.09),
        box("tub interior", (0, 0, h * 0.83), (w * 0.74, d * 0.82, 0.055), target, mats["basin"], root, 0.08),
    ]
    for side in (-1, 1):
        parts.append(box("tub side rim", (side * (w / 2 - 0.045), 0, h * 0.93), (0.09, d, 0.09), target, mats["porcelain"], root, 0.035))
        parts.append(box("tub end rim", (0, side * (d / 2 - 0.045), h * 0.93), (w, 0.09, 0.09), target, mats["porcelain"], root, 0.035))
    parts.append(cylinder("tub filler", (0, -d * 0.38, h * 1.12), 0.018, h * 0.45, target, mats["chrome"], root, 0.004, 24))
    return parts


def supporting_counter(item, furniture):
    if item["type"] not in {"sink", "stove"}:
        return None
    for candidate in furniture:
        if candidate["type"] != "counter":
            continue
        angle = math.radians(candidate.get("rotation_deg", 0.0))
        dx = item["center"][0] - candidate["center"][0]
        dy = item["center"][1] - candidate["center"][1]
        local_x = math.cos(angle) * dx + math.sin(angle) * dy
        local_y = -math.sin(angle) * dx + math.cos(angle) * dy
        if abs(local_x) <= candidate["size"][0] / 2 and abs(local_y) <= candidate["size"][1] / 2:
            return candidate
    return None


def build_furniture(scene, target, mats):
    builders = {
        "sofa": add_sofa,
        "tv": add_tv,
        "coffee_table": add_coffee_table,
        "rug": add_rug,
        "round_table": add_round_table,
        "chair": add_chair,
        "bed": add_bed,
        "counter": lambda item, root, coll, materials: add_cabinet(item, root, coll, materials),
        "sink": add_sink,
        "stove": add_stove,
        "fridge": add_fridge,
        "toilet": add_toilet,
        "tub": add_tub,
    }
    roots = []
    supports = []
    for item in scene.get("furniture", []):
        if item["type"] == "wardrobe":
            base_z = 0.0
            root = furniture_root(item, target, base_z)
            parts = add_cabinet(item, root, target, mats, True)
        else:
            support = supporting_counter(item, scene.get("furniture", []))
            base_z = support["size"][2] - (0.035 if item["type"] == "sink" else 0.0) if support else 0.0
            root = furniture_root(item, target, base_z)
            parts = builders[item["type"]](item, root, target, mats)
            if support:
                root["supported_by"] = support["id"]
                supports.append({"object": item["id"], "counter": support["id"], "base_z_m": base_z})
        for part in parts:
            child_metadata(part, root)
        roots.append(root)
    return roots, supports


def counter_separation_checks(furniture_roots):
    checks = []
    for root in furniture_roots:
        if root.get("furniture_type") != "counter":
            continue
        carcass = next(child for child in root.children if child.name.startswith("cabinet carcass"))
        worktop = next(child for child in root.children if child.name.startswith("counter worktop"))
        carcass_top = max((carcass.matrix_world @ Vector(corner)).z for corner in carcass.bound_box)
        worktop_bottom = min((worktop.matrix_world @ Vector(corner)).z for corner in worktop.bound_box)
        worktop_top = max((worktop.matrix_world @ Vector(corner)).z for corner in worktop.bound_box)
        declared_height = root["declared_size_m"][2]
        coplanar_top_faces = math.isclose(carcass_top, worktop_top, abs_tol=0.000001)
        checks.append({
            "counter_id": root["semantic_id"],
            "carcass_top_m": carcass_top,
            "worktop_bottom_m": worktop_bottom,
            "worktop_finished_top_m": worktop_top,
            "declared_finished_top_m": declared_height,
            "carcass_below_worktop_bottom": carcass_top < worktop_bottom - 0.000001,
            "coplanar_top_faces": coplanar_top_faces,
            "passed": carcass_top < worktop_bottom - 0.000001 and math.isclose(worktop_top, declared_height, abs_tol=0.000001) and not coplanar_top_faces,
        })
    return checks


def exterior_walls(scene):
    xs = [point[0] for wall in scene["walls"] for point in (wall["a"], wall["b"])]
    ys = [point[1] for wall in scene["walls"] for point in (wall["a"], wall["b"])]
    result = set()
    for wall in scene["walls"]:
        cx = (wall["a"][0] + wall["b"][0]) / 2
        cy = (wall["a"][1] + wall["b"][1]) / 2
        if min(abs(cx - min(xs)), abs(cx - max(xs)), abs(cy - min(ys)), abs(cy - max(ys))) < 0.05:
            result.add(wall["id"])
    return result


def build_walls(scene, target, mats):
    segments = []
    exterior = exterior_walls(scene)
    openings_by_wall = {}
    for opening in scene["openings"]:
        openings_by_wall.setdefault(opening["wall"], []).append(opening)
    for wall in scene["walls"]:
        ax, ay = wall["a"]
        bx, by = wall["b"]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        ux, uy = dx / length, dy / length
        angle = math.atan2(dy, dx)
        thickness = wall["thickness"]
        height = wall["height"]

        def segment(name, start, end, z_min, z_max, role, opening_id=None):
            if end - start < 0.0001 or z_max - z_min < 0.0001:
                return
            center_distance = (start + end) / 2
            location = (ax + ux * center_distance, ay + uy * center_distance, (z_min + z_max) / 2)
            obj = box(name, location, (end - start, thickness, z_max - z_min), target, mats["wall"], bevel=0.004, rotation=angle)
            obj["semantic_type"] = "wall_segment"
            obj["wall_id"] = wall["id"]
            obj["segment_role"] = role
            obj["exterior"] = wall["id"] in exterior
            if opening_id:
                obj["opening_id"] = opening_id
            segments.append({"object": obj.name, "wall": wall["id"], "opening": opening_id, "role": role, "along": [start, end], "z": [z_min, z_max]})

        openings = sorted(openings_by_wall.get(wall["id"], []), key=lambda item: item["offset"])
        cursor = 0.0
        for index, opening in enumerate(openings):
            start = max(0.0, opening["offset"])
            end = min(length, opening["offset"] + opening["width"])
            segment(f"{wall['id']} full {index}", cursor, start, 0.0, height, "full")
            if opening["kind"] == "window":
                segment(f"{opening['id']} sill", start, end, 0.0, opening["sill"], "sill", opening["id"])
            segment(f"{opening['id']} header", start, end, opening["sill"] + opening["height"], height, "header", opening["id"])
            frame_mat = mats["window_frame"] if opening["kind"] == "window" else mats["dark_wood"]
            center_x = ax + ux * (start + end) / 2
            center_y = ay + uy * (start + end) / 2
            frame_width = 0.045
            for side, distance in (("left", start), ("right", end)):
                obj = box(f"{opening['id']} {side} jamb", (ax + ux * distance, ay + uy * distance, opening["sill"] + opening["height"] / 2), (frame_width, thickness * 1.08, opening["height"]), target, frame_mat, bevel=0.006, rotation=angle)
                obj["semantic_type"] = "door_frame" if opening["kind"] == "door" else "window_frame"
                obj["opening_id"] = opening["id"]
                obj["exterior"] = wall["id"] in exterior
            obj = box(f"{opening['id']} frame header", (center_x, center_y, opening["sill"] + opening["height"]), (end - start, thickness * 1.08, frame_width), target, frame_mat, bevel=0.005, rotation=angle)
            obj["semantic_type"] = "door_frame" if opening["kind"] == "door" else "window_frame"
            obj["opening_id"] = opening["id"]
            obj["exterior"] = wall["id"] in exterior
            if opening["kind"] == "window":
                obj = box(f"{opening['id']} glass", (center_x, center_y, opening["sill"] + opening["height"] / 2), (end - start - 0.07, 0.012, opening["height"] - 0.07), target, mats["glass"], bevel=0.002, rotation=angle)
                obj["semantic_type"] = "window_glass"
                obj["opening_id"] = opening["id"]
                obj["exterior"] = wall["id"] in exterior
                obj = box(f"{opening['id']} mullion", (center_x, center_y, opening["sill"] + opening["height"] / 2), (0.035, thickness * 0.6, opening["height"] - 0.05), target, frame_mat, bevel=0.004, rotation=angle)
                obj["semantic_type"] = "window_frame"
                obj["opening_id"] = opening["id"]
                obj["exterior"] = wall["id"] in exterior
            cursor = end
        segment(f"{wall['id']} full end", cursor, length, 0.0, height, "full")
    return segments, exterior


def build_floors(scene, target, mats, wall_height):
    xs = [point[0] for wall in scene["walls"] for point in (wall["a"], wall["b"])]
    ys = [point[1] for wall in scene["walls"] for point in (wall["a"], wall["b"])]
    base = box("continuous marble living dining floor", ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, -0.045), (max(xs) - min(xs), max(ys) - min(ys), 0.09), target, mats["marble"], bevel=0.0)
    base["semantic_type"] = "floor"
    base["zone_id"] = "living_dining_kitchen"
    for zone in scene["calibration"].get("zones", []):
        zone_id = zone["id"].lower()
        if "bed" not in zone_id and "bath" not in zone_id:
            continue
        x1, y1, x2, y2 = zone["bounds"]
        mat = mats["wood"] if "bed" in zone_id else mats["tile"]
        obj = box(f"{zone['id']} finish floor", ((x1 + x2) / 2, (y1 + y2) / 2, 0.008), (x2 - x1, y2 - y1, 0.016), target, mat)
        obj["semantic_type"] = "floor"
        obj["zone_id"] = zone["id"]
    for column_item in scene.get("columns", []):
        sx, sy = column_item["size"]
        obj = box(column_item["id"], (column_item["center"][0], column_item["center"][1], wall_height / 2), (sx, sy, wall_height), target, mats["column"], bevel=0.008)
        obj["semantic_id"] = column_item["id"]
        obj["semantic_type"] = "column"


def point_camera(name, location, target_location, target, lens=24, ortho=None):
    data = bpy.data.cameras.new(name)
    obj = bpy.data.objects.new(name, data)
    target.objects.link(obj)
    obj.location = location
    direction = Vector(target_location) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    data.lens = lens
    data.clip_start = 0.05
    data.clip_end = 200.0
    if ortho:
        data.type = "ORTHO"
        data.ortho_scale = ortho
    return obj


def area_light(name, location, target_location, energy, color, size, target):
    data = bpy.data.lights.new(name, "AREA")
    data.energy = energy
    data.color = color
    data.shape = "DISK"
    data.size = size
    obj = bpy.data.objects.new(name, data)
    target.objects.link(obj)
    obj.location = location
    obj.rotation_euler = (Vector(target_location) - obj.location).to_track_quat("-Z", "Y").to_euler()
    return obj


def build_lighting(scene, target, mats):
    world = bpy.data.worlds.new("Soft daylight environment")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.72, 0.8, 0.92, 1.0)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.32
    bpy.context.scene.world = world
    xs = [point[0] for wall in scene["walls"] for point in (wall["a"], wall["b"])]
    ys = [point[1] for wall in scene["walls"] for point in (wall["a"], wall["b"])]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    area_light("large soft daylight", (cx, min(ys) - 2.5, 5.0), (cx, cy, 0.5), 1050, (0.84, 0.91, 1.0), 5.0, target)
    area_light("overview softbox", (cx - 2.0, cy + 1.0, 7.0), (cx, cy, 0.0), 850, (1.0, 0.9, 0.76), 5.0, target)
    for index, (x, y) in enumerate(((2.0, 4.6), (4.65, 1.65), (8.3, 4.8))):
        area_light(f"warm interior lamp {index + 1}", (x, y, 2.5), (x, y, 0.0), 95, (1.0, 0.68, 0.4), 0.65, target)
        cylinder(f"lamp {index + 1} stem", (x, y, 2.58), 0.012, 0.3, target, mats["bronze"], bevel=0.002, vertices=20)
        sphere(f"lamp {index + 1} diffuser", (x, y, 2.42), (0.12, 0.12, 0.075), target, mats["lamp_glow"])


def configure_render(scene):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 32
    scene.cycles.use_denoising = True
    scene.render.resolution_x = 1440
    scene.render.resolution_y = 1000
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.render.image_settings.color_mode = "RGB"
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"


def cutout_checks(scene, segments):
    results = []
    for opening in scene["openings"]:
        z_min = opening["sill"]
        z_max = opening["sill"] + opening["height"]
        start = opening["offset"]
        end = start + opening["width"]
        conflicts = []
        for segment in segments:
            if segment["wall"] != opening["wall"]:
                continue
            along_overlap = min(end, segment["along"][1]) - max(start, segment["along"][0])
            z_overlap = min(z_max, segment["z"][1]) - max(z_min, segment["z"][0])
            if along_overlap > 0.0001 and z_overlap > 0.0001:
                conflicts.append(segment["object"])
        results.append({"opening": opening["id"], "kind": opening["kind"], "clear_structural_cutout": not conflicts, "conflicts": conflicts, "door_leaf_exported": False if opening["kind"] == "door" else None})
    return results


def mesh_extents(objects):
    points = []
    for obj in objects:
        if obj.type != "MESH":
            continue
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not points:
        return None
    minimum = [min(point[i] for point in points) for i in range(3)]
    maximum = [max(point[i] for point in points) for i in range(3)]
    return {"min_m": minimum, "max_m": maximum, "dimensions_m": [maximum[i] - minimum[i] for i in range(3)]}


def camera_clearance(scene, camera_location):
    x, y, z = camera_location
    conflicts = []
    for wall in scene["walls"]:
        ax, ay = wall["a"]
        bx, by = wall["b"]
        length = math.hypot(bx - ax, by - ay)
        ux, uy = (bx - ax) / length, (by - ay) / length
        projection = (x - ax) * ux + (y - ay) * uy
        perpendicular = abs(-(x - ax) * uy + (y - ay) * ux)
        if -0.02 <= projection <= length + 0.02 and perpendicular <= wall["thickness"] / 2 + 0.02 and z <= wall["height"]:
            conflicts.append(wall["id"])
    for item in scene.get("furniture", []):
        if item["type"] == "rug":
            continue
        angle = math.radians(item.get("rotation_deg", 0.0))
        dx, dy = x - item["center"][0], y - item["center"][1]
        local_x = math.cos(angle) * dx + math.sin(angle) * dy
        local_y = -math.sin(angle) * dx + math.cos(angle) * dy
        if abs(local_x) <= item["size"][0] / 2 and abs(local_y) <= item["size"][1] / 2 and z <= item["size"][2]:
            conflicts.append(item["id"])
    return not conflicts, conflicts


def tag_cutaway(scene, exterior, overview):
    walls = {wall["id"]: wall for wall in scene["walls"]}
    xs = [point[0] for wall in scene["walls"] for point in (wall["a"], wall["b"])]
    ys = [point[1] for wall in scene["walls"] for point in (wall["a"], wall["b"])]
    building_center = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, 0.0))
    for obj in bpy.context.scene.objects:
        wall_id = obj.get("wall_id")
        if not wall_id and obj.get("opening_id"):
            opening = next((item for item in scene["openings"] if item["id"] == obj["opening_id"]), None)
            wall_id = opening["wall"] if opening else None
        if wall_id not in exterior:
            obj["cutaway_hide"] = False
            continue
        wall = walls[wall_id]
        center = Vector(((wall["a"][0] + wall["b"][0]) / 2, (wall["a"][1] + wall["b"][1]) / 2, 0.0))
        outward = center - building_center
        outward.z = 0.0
        view = overview.location - center
        view.z = 0.0
        obj["cutaway_hide"] = outward.dot(view) > 0.0


def entrance_camera(scene, exterior, target):
    xs = [point[0] for wall in scene["walls"] for point in (wall["a"], wall["b"])]
    ys = [point[1] for wall in scene["walls"] for point in (wall["a"], wall["b"])]
    building_center = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, 0.0))
    walls = {wall["id"]: wall for wall in scene["walls"]}
    candidates = [item for item in scene["openings"] if item["kind"] == "door" and item["wall"] in exterior]
    opening = (candidates or [item for item in scene["openings"] if item["kind"] == "door"])[0]
    wall = walls[opening["wall"]]
    ax, ay = wall["a"]
    bx, by = wall["b"]
    length = math.hypot(bx - ax, by - ay)
    ux, uy = (bx - ax) / length, (by - ay) / length
    doorway = Vector((ax + ux * (opening["offset"] + opening["width"] / 2), ay + uy * (opening["offset"] + opening["width"] / 2), 0.0))
    inward = Vector((-uy, ux, 0.0))
    if inward.dot(building_center - doorway) < 0:
        inward = -inward
    camera_location = doorway + inward * 0.45
    camera_location.z = 1.55
    target_location = doorway + inward * 3.4
    target_location.z = 1.0
    return point_camera("living camera", camera_location, target_location, target, lens=20), target_location


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main():
    args = parse_args()
    scene_data = json.loads(args.scene.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for current in list(bpy.data.collections):
        if current.name == "Collection":
            bpy.data.collections.remove(current)
    architecture = collection("Architecture")
    furniture_collection = collection("Parametric furniture")
    lighting = collection("Lighting and cameras")
    textures = {kind: generated_texture(kind, kind, args.output) for kind in ("marble", "wood", "burl", "tile", "rug")}
    mats = {
        "wall": material("warm mineral wall paint", (0.79, 0.77, 0.71), 0.78),
        "column": material("column limestone", (0.63, 0.62, 0.58), 0.68),
        "marble": material("honed veined marble image", (0.8, 0.78, 0.74), 0.3, texture=textures["marble"]),
        "wood": material("natural oak image", (0.4, 0.25, 0.13), 0.48, texture=textures["wood"]),
        "burl": material("shadow wood burl image", (0.3, 0.2, 0.15), 0.4, texture=textures["burl"]),
        "tile": material("bath tile and grout image", (0.65, 0.68, 0.69), 0.42, texture=textures["tile"]),
        "rug": material("sparse geometric rug image", (0.7, 0.64, 0.54), 0.9, texture=textures["rug"]),
        "fabric_cream": material("cream woven upholstery", (0.78, 0.71, 0.6), 0.88),
        "fabric_taupe": material("taupe woven upholstery", (0.48, 0.41, 0.33), 0.86),
        "fabric_sage": material("sage cotton coverlet", (0.32, 0.39, 0.34), 0.9),
        "linen_white": material("white cotton linen", (0.86, 0.85, 0.8), 0.92),
        "dark_wood": material("dark stained wood", (0.075, 0.052, 0.038), 0.48),
        "metal_dark": material("powder coated charcoal", (0.025, 0.029, 0.032), 0.34, 0.6),
        "screen": material("dark display glass", (0.008, 0.015, 0.021), 0.16, 0.25),
        "bronze": material("brushed warm bronze", (0.28, 0.18, 0.09), 0.3, 0.75),
        "steel": material("satin stainless steel", (0.48, 0.52, 0.55), 0.32, 0.72),
        "chrome": material("polished chrome", (0.65, 0.7, 0.73), 0.16, 0.9),
        "porcelain": material("glazed warm porcelain", (0.85, 0.85, 0.81), 0.2),
        "basin": material("basin interior shadow", (0.12, 0.16, 0.17), 0.26),
        "window_frame": material("painted window frames", (0.72, 0.73, 0.7), 0.35),
        "glass": material("thin architectural glass", (0.55, 0.76, 0.84), 0.08),
        "lamp_glow": material("warm lamp diffuser", (0.95, 0.72, 0.42), 0.4),
    }
    glass_shader = mats["glass"].node_tree.nodes.get("Principled BSDF")
    glass_shader.inputs["Transmission Weight"].default_value = 0.72
    glass_shader.inputs["IOR"].default_value = 1.45
    glass_shader.inputs["Alpha"].default_value = 0.28
    mats["glass"].diffuse_color = (0.55, 0.76, 0.84, 0.28)
    glow_shader = mats["lamp_glow"].node_tree.nodes.get("Principled BSDF")
    glow_shader.inputs["Emission Color"].default_value = (1.0, 0.55, 0.22, 1.0)
    glow_shader.inputs["Emission Strength"].default_value = 1.8
    wall_height = max(wall["height"] for wall in scene_data["walls"])
    wall_segments, exterior = build_walls(scene_data, architecture, mats)
    build_floors(scene_data, architecture, mats, wall_height)
    furniture_roots, supports = build_furniture(scene_data, furniture_collection, mats)
    build_lighting(scene_data, lighting, mats)
    xs = [point[0] for wall in scene_data["walls"] for point in (wall["a"], wall["b"])]
    ys = [point[1] for wall in scene_data["walls"] for point in (wall["a"], wall["b"])]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    overview = point_camera("overview camera", (max(xs) + 4.0, min(ys) - 12.0, 12.5), (cx, cy, 0.4), lighting, ortho=max(max(xs) - min(xs), max(ys) - min(ys)) * 1.38)
    interior, interior_target = entrance_camera(scene_data, exterior, lighting)
    tag_cutaway(scene_data, exterior, overview)
    configure_render(bpy.context.scene)
    bpy.context.scene.camera = overview
    bpy.context.view_layer.update()
    checks = cutout_checks(scene_data, wall_segments)
    camera_clear, camera_conflicts = camera_clearance(scene_data, interior.location)
    blend_path = args.output / "scene.blend"
    glb_path = args.output / "model.glb"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format="GLB", export_extras=True, export_yup=True, export_apply=True, export_animations=False, export_cameras=False, export_lights=False)
    rendered = []
    if args.render:
        cutaway_objects = [obj for obj in bpy.context.scene.objects if obj.get("cutaway_hide")]
        for obj in cutaway_objects:
            obj.hide_render = True
        bpy.context.scene.camera = overview
        bpy.context.scene.render.filepath = str(args.output / "overview.png")
        bpy.ops.render.render(write_still=True)
        rendered.append("overview.png")
        for obj in cutaway_objects:
            obj.hide_render = False
        bpy.context.scene.camera = interior
        bpy.context.scene.render.filepath = str(args.output / "living.png")
        bpy.ops.render.render(write_still=True)
        rendered.append("living.png")
    source_extents = mesh_extents(bpy.context.scene.objects)
    counter_checks = counter_separation_checks(furniture_roots)
    report = {
        "status": "built",
        "schema": scene_data.get("schema"),
        "units": scene_data.get("units"),
        "counts": {
            "source_walls": len(scene_data["walls"]),
            "wall_mesh_segments": len(wall_segments),
            "openings": len(scene_data["openings"]),
            "columns": len(scene_data.get("columns", [])),
            "furniture": len(furniture_roots),
            "mesh_objects": sum(obj.type == "MESH" for obj in bpy.context.scene.objects),
            "materials": len(mats),
        },
        "source_mesh_extents": source_extents,
        "materials": sorted(mat.name for mat in mats.values()),
        "image_textures": sorted(f"{kind}.png" for kind in textures),
        "texture_export_method": "Blender-generated PNG images linked directly to Principled base color with primitive UV maps",
        "parametric_furniture": True,
        "furniture_supports": supports,
        "counter_geometry_checks": counter_checks,
        "door_window_cutout_checks": checks,
        "doors": "Open doorway holes with jamb/header frames only; no solid door leaves exported",
        "ceiling": False,
        "ceiling_disclosure": "No ceiling is modeled; the overview uses camera-facing exterior wall cutaways, while the interior render restores all walls",
        "cameras": {
            "overview": {"position_m": list(overview.location), "target_m": [cx, cy, 0.4], "projection": "orthographic"},
            "living": {"position_m": list(interior.location), "target_m": list(interior_target), "height_m": 1.55, "clear_of_declared_walls_and_furniture": camera_clear, "conflicts": camera_conflicts},
        },
        "overview_cutaway_hidden_objects": [obj.name for obj in bpy.context.scene.objects if obj.get("cutaway_hide")],
        "lighting": "Soft daylight environment and area lights plus warm artificial area lamps",
        "render": {"engine": "Cycles CPU", "samples": 32, "resolution": [1440, 1000], "files": rendered},
        "files": {},
    }
    for path in [blend_path, glb_path] + [args.output / name for name in rendered]:
        report["files"][path.name] = {"exists": path.exists(), "bytes": path.stat().st_size if path.exists() else 0}
    write_json(args.output / "build-report.json", report)
    original_scene = bpy.context.scene
    verification_scene = bpy.data.scenes.new("GLB reimport verification")
    bpy.context.window.scene = verification_scene
    bpy.ops.import_scene.gltf(filepath=str(glb_path))
    bpy.context.view_layer.update()
    imported = list(verification_scene.objects)
    imported_walls = sorted({obj["wall_id"] for obj in imported if obj.get("wall_id")})
    expected_furniture = {item["id"] for item in scene_data.get("furniture", [])}
    imported_furniture = {obj["semantic_id"] for obj in imported if obj.get("semantic_type") == "furniture" and obj.get("semantic_id")}
    verification = {
        "status": "passed",
        "method": "Source wall-segment interval checks plus Blender glTF reimport, object extras, and world-space mesh bounding boxes",
        "source": {
            "wall_count": len(scene_data["walls"]),
            "opening_count": len(scene_data["openings"]),
            "furniture_count": len(expected_furniture),
            "all_structural_openings_clear": all(item["clear_structural_cutout"] for item in checks),
            "mesh_extents": source_extents,
        },
        "reimported_glb": {
            "wall_count": len(imported_walls),
            "wall_ids": imported_walls,
            "furniture_count": len(imported_furniture),
            "missing_furniture_ids": sorted(expected_furniture - imported_furniture),
            "mesh_extents": mesh_extents(imported),
            "mesh_object_count": sum(obj.type == "MESH" for obj in imported),
        },
        "output_files": report["files"],
        "counter_geometry_checks": counter_checks,
        "interior_camera_clear": camera_clear,
        "known_limits": ["Verification checks structural wall meshes, semantic presence, and metric envelopes; it is not a construction or engineering validation", "Image texture appearance is visually reviewed separately in rendered PNGs"],
    }
    failures = []
    if len(imported_walls) != len(scene_data["walls"]):
        failures.append("wall count mismatch after GLB reimport")
    if expected_furniture - imported_furniture:
        failures.append("furniture missing after GLB reimport")
    if not all(item["clear_structural_cutout"] for item in checks):
        failures.append("structural opening conflict")
    if not camera_clear:
        failures.append("interior camera conflicts with declared geometry")
    if not all(item["passed"] for item in counter_checks):
        failures.append("counter carcass/worktop separation check failed")
    for name, item in report["files"].items():
        if not item["exists"] or item["bytes"] <= 0:
            failures.append(f"missing or empty output: {name}")
    verification["failures"] = failures
    verification["status"] = "passed" if not failures else "failed"
    write_json(args.output / "verification-report.json", verification)
    bpy.context.window.scene = original_scene
    print(json.dumps({"output": str(args.output), "build_report": str(args.output / "build-report.json"), "verification": verification["status"], "mesh_objects": report["counts"]["mesh_objects"], "furniture": len(furniture_roots), "rendered": rendered}))
    if failures:
        raise RuntimeError("; ".join(failures))


if __name__ == "__main__":
    main()
