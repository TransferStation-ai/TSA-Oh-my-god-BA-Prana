"""Blender 内的建模 API。

这个模块只在 Blender 的 Python 环境里导入。主进程通过 ``blender_body.py``
把命令转发到这里，避免把 bpy 代码散落在主程序里。
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

import bpy
from mathutils import Matrix, Vector


def _object(name: str):
    return bpy.data.objects.get(name)


def _activate(obj) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _remove_existing(name: str) -> None:
    obj = _object(name)
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)


def reset_scene() -> dict[str, Any]:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)
    bpy.context.view_layer.update()
    return {"ok": True, "objects": 0}


def scene_summary() -> dict[str, Any]:
    meshes = [obj for obj in bpy.data.objects if obj.type == "MESH"]
    triangles = 0
    for obj in meshes:
        obj.data.calc_loop_triangles()
        triangles += len(obj.data.loop_triangles)
    return {
        "ok": True,
        "objects": len(bpy.data.objects),
        "meshes": len(meshes),
        "armatures": len([obj for obj in bpy.data.objects if obj.type == "ARMATURE"]),
        "materials": len(bpy.data.materials),
        "triangles": triangles,
        "names": [obj.name for obj in bpy.data.objects],
    }


def add_box(name: str, center: Sequence[float], size: Sequence[float]) -> dict[str, Any]:
    _remove_existing(name)
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=tuple(center))
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = tuple(size)
    _activate(obj)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return {"ok": True, "name": name, "vertices": len(obj.data.vertices), "triangles": 12}


def add_cylinder(
    name: str,
    center: Sequence[float],
    radius: float,
    depth: float,
    vertices: int = 16,
) -> dict[str, Any]:
    _remove_existing(name)
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=max(3, int(vertices)),
        radius=float(radius),
        depth=float(depth),
        location=tuple(center),
    )
    obj = bpy.context.object
    obj.name = name
    return {"ok": True, "name": name, "vertices": len(obj.data.vertices)}


def add_uv_sphere(name: str, center: Sequence[float], radius: float) -> dict[str, Any]:
    _remove_existing(name)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=float(radius), location=tuple(center))
    obj = bpy.context.object
    obj.name = name
    return {"ok": True, "name": name, "vertices": len(obj.data.vertices)}


def bevel(name: str, width: float, segments: int = 2) -> dict[str, Any]:
    obj = _object(name)
    if obj is None or obj.type != "MESH":
        return {"ok": False, "error": f"找不到网格：{name}"}
    _activate(obj)
    modifier = obj.modifiers.new("PranaBevel", "BEVEL")
    modifier.width = float(width)
    modifier.segments = max(1, int(segments))
    modifier.limit_method = "ANGLE"
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    return {"ok": True, "name": name}


def mirror_x(names: Sequence[str]) -> dict[str, Any]:
    created: list[str] = []
    for name in names:
        obj = _object(name)
        if obj is None:
            continue
        copy = obj.copy()
        copy.data = obj.data.copy()
        copy.name = f"{name}_mirror"
        copy.matrix_world = Matrix.Scale(-1.0, 4, (1.0, 0.0, 0.0)) @ obj.matrix_world
        bpy.context.scene.collection.objects.link(copy)
        created.append(copy.name)
    return {"ok": True, "names": created}


def join_meshes(name: str, names: Sequence[str]) -> dict[str, Any]:
    objects = [_object(item) for item in names]
    objects = [obj for obj in objects if obj is not None and obj.type == "MESH"]
    if not objects:
        return {"ok": False, "error": "没有可合并的网格。"}
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    joined = bpy.context.object
    joined.name = name
    return {"ok": True, "name": joined.name, "vertices": len(joined.data.vertices)}


def new_material(
    name: str,
    base_color: Sequence[float],
    metallic: float,
    roughness: float,
) -> dict[str, Any]:
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True
    material.diffuse_color = tuple(base_color)
    nodes = material.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = tuple(base_color)
        bsdf.inputs["Metallic"].default_value = float(metallic)
        bsdf.inputs["Roughness"].default_value = float(roughness)
    return {"ok": True, "name": material.name}


def assign_material(name: str, material: str) -> dict[str, Any]:
    obj = _object(name)
    if obj is None or obj.type != "MESH":
        return {"ok": False, "error": f"找不到网格：{name}"}
    mat = bpy.data.materials.get(material)
    if mat is None:
        return {"ok": False, "error": f"找不到材质：{material}"}
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    for polygon in obj.data.polygons:
        polygon.material_index = 0
    return {"ok": True, "name": name, "material": material}


def smart_uv(name: str) -> dict[str, Any]:
    obj = _object(name)
    if obj is None or obj.type != "MESH":
        return {"ok": False, "error": f"找不到网格：{name}"}
    _activate(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    return {"ok": True, "name": name}


def _face_uv(cube: dict[str, Any], polygon, texture_size: tuple[int, int]) -> list[tuple[float, float]]:
    uv_data = cube.get("uv")
    if not isinstance(uv_data, dict):
        return [(0.0, 0.0)] * len(polygon.loop_indices)
    normal = polygon.normal
    if abs(normal.z) >= abs(normal.x) and abs(normal.z) >= abs(normal.y):
        face = "up" if normal.z >= 0 else "down"
    elif abs(normal.x) >= abs(normal.y):
        face = "east" if normal.x >= 0 else "west"
    else:
        face = "south" if normal.y >= 0 else "north"
    entry = uv_data.get(face)
    if not isinstance(entry, dict):
        return [(0.0, 0.0)] * len(polygon.loop_indices)
    origin = entry.get("uv", [0, 0])
    size = entry.get("uv_size", [1, 1])
    try:
        u0, v0 = float(origin[0]), float(origin[1])
        u1, v1 = u0 + float(size[0]), v0 + float(size[1])
    except (TypeError, ValueError, IndexError):
        return [(0.0, 0.0)] * len(polygon.loop_indices)
    width, height = max(1, texture_size[0]), max(1, texture_size[1])
    corners = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    result = []
    for index in range(len(polygon.loop_indices)):
        cx, cy = corners[index % 4]
        result.append(((u0 + (u1 - u0) * cx) / width, 1.0 - (v0 + (v1 - v0) * cy) / height))
    return result


def _reference_material(texture_path: str):
    mat = bpy.data.materials.get("REFERENCE_Texture") or bpy.data.materials.new("REFERENCE_Texture")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    image_node = nodes.get("REFERENCE_Image")
    if image_node is None:
        image_node = nodes.new("ShaderNodeTexImage")
        image_node.name = "REFERENCE_Image"
    if texture_path and os.path.isfile(texture_path):
        image = bpy.data.images.load(texture_path, check_existing=True)
        image_node.image = image
        if bsdf is not None:
            links.new(image_node.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def import_ysm_reference(model: dict[str, Any], texture_path: str, scale: float) -> dict[str, Any]:
    collection = bpy.data.collections.get("REFERENCE")
    if collection is None:
        collection = bpy.data.collections.new("REFERENCE")
        bpy.context.scene.collection.children.link(collection)

    texture_size = (
        int(model.get("texture_width", 1024) or 1024),
        int(model.get("texture_height", 1024) or 1024),
    )
    material = _reference_material(texture_path)
    bones = model.get("bones", [])
    bone_objects: dict[str, Any] = {}
    bone_pivots: dict[str, Vector] = {}
    if isinstance(bones, list):
        for bone in bones:
            if not isinstance(bone, dict) or not bone.get("name"):
                continue
            pivot = bone.get("pivot", [0, 0, 0])
            try:
                location = Vector((float(pivot[0]), float(pivot[1]), float(pivot[2]))) * float(scale)
            except (TypeError, ValueError, IndexError):
                location = Vector((0, 0, 0))
            empty = bpy.data.objects.new(f"REF_{bone['name']}", None)
            empty.empty_display_type = "PLAIN_AXES"
            empty.empty_display_size = 0.8
            empty.location = location
            collection.objects.link(empty)
            bone_objects[bone["name"]] = empty
            bone_pivots[bone["name"]] = location
        for bone in bones:
            if not isinstance(bone, dict) or not bone.get("name"):
                continue
            parent = bone.get("parent")
            if parent in bone_objects:
                child = bone_objects[bone["name"]]
                child.parent = bone_objects[parent]
                child.matrix_parent_inverse = bone_objects[parent].matrix_world.inverted()

    cubes: list[dict[str, Any]] = []
    for bone in bones if isinstance(bones, list) else []:
        if isinstance(bone, dict):
            for cube in bone.get("cubes", []) or []:
                if isinstance(cube, dict):
                    item = dict(cube)
                    item.setdefault("bone", bone.get("name", "Root"))
                    cubes.append(item)
    if not cubes:
        for cube in model.get("cubes", []) or []:
            if isinstance(cube, dict):
                cubes.append(cube)

    for index, cube in enumerate(cubes):
        origin = cube.get("origin", [0, 0, 0])
        size = cube.get("size", [1, 1, 1])
        try:
            center = Vector((float(origin[0]) + float(size[0]) / 2, float(origin[1]) + float(size[1]) / 2, float(origin[2]) + float(size[2]) / 2)) * float(scale)
            dimensions = Vector((abs(float(size[0])), abs(float(size[1])), abs(float(size[2])))) * float(scale)
        except (TypeError, ValueError, IndexError):
            continue
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=center)
        obj = bpy.context.object
        obj.name = f"REF_CUBE_{index}_{cube.get('bone', 'Root')}"
        obj.dimensions = dimensions
        _activate(obj)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        for old_collection in list(obj.users_collection):
            old_collection.objects.unlink(obj)
        collection.objects.link(obj)
        obj.data.materials.append(material)
        uv_layer = obj.data.uv_layers.get("UVMap") or obj.data.uv_layers.new(name="UVMap")
        for polygon in obj.data.polygons:
            mapped = _face_uv(cube, polygon, texture_size)
            for loop_index, uv in zip(polygon.loop_indices, mapped):
                uv_layer.data[loop_index].uv = uv
        bone_name = cube.get("bone")
        if bone_name in bone_objects:
            obj.parent = bone_objects[bone_name]
            obj.matrix_parent_inverse = bone_objects[bone_name].matrix_world.inverted()

    return {
        "ok": True,
        "collection": collection.name,
        "bones": len(bone_objects),
        "cubes": len(cubes),
        "texture": texture_path,
    }


def build_armature(bones: Sequence[dict[str, Any]]) -> dict[str, Any]:
    _remove_existing("PranaRig")
    armature = bpy.data.armatures.new("PranaRig")
    arm_obj = bpy.data.objects.new("PranaRig", armature)
    bpy.context.scene.collection.objects.link(arm_obj)
    _activate(arm_obj)
    bpy.ops.object.mode_set(mode="EDIT")

    heads: dict[str, Vector] = {}
    for bone in bones:
        if not isinstance(bone, dict) or not bone.get("name"):
            continue
        raw = bone.get("pos", bone.get("head", [0, 0, 0]))
        try:
            heads[bone["name"]] = Vector((float(raw[0]), float(raw[1]), float(raw[2])))
        except (TypeError, ValueError, IndexError):
            heads[bone["name"]] = Vector((0, 0, 0))
    child_of: dict[str, str] = {}
    for bone in bones:
        if isinstance(bone, dict) and bone.get("parent") in heads:
            child_of.setdefault(bone["parent"], bone["name"])

    edit_bones: dict[str, Any] = {}
    for bone in bones:
        if not isinstance(bone, dict) or not bone.get("name"):
            continue
        name = bone["name"]
        edit_bone = armature.edit_bones.new(name)
        edit_bone.head = heads.get(name, Vector((0, 0, 0)))
        raw_tail = bone.get("tail")
        if isinstance(raw_tail, (list, tuple)) and len(raw_tail) == 3:
            tail = Vector((float(raw_tail[0]), float(raw_tail[1]), float(raw_tail[2])))
        elif child_of.get(name) in heads:
            tail = heads[child_of[name]]
        else:
            tail = edit_bone.head + Vector((0, 0, 5))
        if (tail - edit_bone.head).length < 0.01:
            tail = edit_bone.head + Vector((0, 0, 1))
        edit_bone.tail = tail
        edit_bones[name] = edit_bone
    for bone in bones:
        if not isinstance(bone, dict) or not bone.get("name"):
            continue
        parent = bone.get("parent")
        if parent in edit_bones:
            edit_bones[bone["name"]].parent = edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return {"ok": True, "armature": arm_obj.name, "bones": len(edit_bones)}


def bind_mesh(mesh_names: Sequence[str], armature_name: str) -> dict[str, Any]:
    arm_obj = _object(armature_name)
    if arm_obj is None or arm_obj.type != "ARMATURE":
        return {"ok": False, "error": f"找不到骨架：{armature_name}"}
    meshes = [_object(name) for name in mesh_names]
    meshes = [obj for obj in meshes if obj is not None and obj.type == "MESH"]
    if not meshes:
        return {"ok": False, "error": "没有可绑定的网格。"}
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    arm_obj.select_set(True)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    return {"ok": True, "armature": armature_name, "meshes": [obj.name for obj in meshes]}


def _bone_side(name: str) -> str | None:
    lowered = name.lower()
    if "left" in lowered or "_l_" in lowered or lowered.startswith("l_") or lowered.endswith(".l") or lowered.endswith("_l"):
        return "left"
    if "right" in lowered or "_r_" in lowered or lowered.startswith("r_") or lowered.endswith(".r") or lowered.endswith("_r"):
        return "right"
    return None


def validate_hands() -> dict[str, Any]:
    names: set[str] = set()
    for arm_obj in [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]:
        names.update(bone.name for bone in arm_obj.data.bones)
    for obj in [item for item in bpy.data.objects if item.type == "MESH"]:
        names.update(group.name for group in obj.vertex_groups)

    fingers: dict[str, set[str]] = {"left": set(), "right": set()}
    thumbs: dict[str, set[str]] = {"left": set(), "right": set()}
    for name in names:
        side = _bone_side(name)
        if side is None:
            continue
        lowered = name.lower()
        if "thumb" in lowered or "pollex" in lowered:
            thumbs[side].add("thumb")
        match = re.search(r"finger[\s._-]*(\d+)", lowered)
        if match:
            fingers[side].add(f"finger{match.group(1)}")
        elif "finger" in lowered:
            fingers[side].add(name)

    left = len(fingers["left"]) + len(thumbs["left"])
    right = len(fingers["right"]) + len(thumbs["right"])
    errors = []
    if left < 5:
        errors.append(f"左手只有 {left} 个手指链，需要 4 指 + 拇指。")
    if right < 5:
        errors.append(f"右手只有 {right} 个手指链，需要 4 指 + 拇指。")
    return {"ok": not errors, "left": left, "right": right, "errors": errors}


def _scene_bounds() -> tuple[Vector, Vector]:
    points: list[Vector] = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            points.append(obj.matrix_world @ Vector(corner))
    if not points:
        return Vector((0, 0, 0)), Vector((1, 1, 1))
    minimum = Vector((min(point.x for point in points), min(point.y for point in points), min(point.z for point in points)))
    maximum = Vector((max(point.x for point in points), max(point.y for point in points), max(point.z for point in points)))
    return minimum, maximum


def render_preview(view: str, resolution: str, output: str) -> dict[str, Any]:
    try:
        width_text, height_text = resolution.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
    except (ValueError, AttributeError):
        return {"ok": False, "error": f"分辨率格式无效：{resolution}"}
    if width <= 0 or height <= 0 or width > 1600 or height > 1600:
        return {"ok": False, "error": "分辨率必须在 1..1600 之间。"}

    minimum, maximum = _scene_bounds()
    center = (minimum + maximum) / 2
    extent = maximum - minimum
    distance = max(extent.length * 1.8, 3.0)
    directions = {
        "front": Vector((0, -1, 0)),
        "back": Vector((0, 1, 0)),
        "left": Vector((-1, 0, 0)),
        "right": Vector((1, 0, 0)),
        "three_quarter": Vector((1, -1, 0.55)),
    }
    direction = directions.get(view, directions["three_quarter"]).normalized()
    camera = bpy.data.objects.get("PreviewCamera")
    if camera is None or camera.type != "CAMERA":
        camera_data = bpy.data.cameras.new("PreviewCamera")
        camera = bpy.data.objects.new("PreviewCamera", camera_data)
        bpy.context.scene.collection.objects.link(camera)
    camera.location = center + direction * distance
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.lens = 55
    bpy.context.scene.camera = camera

    for name, kind, location, energy in (
        ("PreviewSun", "SUN", Vector((3, -4, 8)), 3.0),
        ("PreviewFill", "AREA", Vector((-4, -3, 4)), 500.0),
    ):
        light = bpy.data.objects.get(name)
        if light is None or light.type != kind:
            data = bpy.data.lights.new(name, kind)
            light = bpy.data.objects.new(name, data)
            bpy.context.scene.collection.objects.link(light)
        light.location = location
        light.data.energy = energy
        if kind == "AREA":
            light.data.size = 5.0

    world = bpy.context.scene.world or bpy.data.worlds.new("PranaWorld")
    bpy.context.scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.08, 0.09, 0.11, 1.0)
        background.inputs["Strength"].default_value = 0.7

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.render.filepath = os.path.abspath(output)
    os.makedirs(os.path.dirname(scene.render.filepath), exist_ok=True)
    bpy.ops.render.render(write_still=True)
    return {"ok": True, "path": scene.render.filepath, "view": view, "resolution": f"{width}x{height}"}


def save_blend(path: str) -> dict[str, Any]:
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=target)
    return {"ok": True, "path": target}


def _evaluated_mesh(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    return evaluated, evaluated.to_mesh()


def export_smd(path: str, mesh_names: Sequence[str], armature_name: str | None = None) -> dict[str, Any]:
    arm_obj = _object(armature_name) if armature_name else None
    if armature_name and (arm_obj is None or arm_obj.type != "ARMATURE"):
        return {"ok": False, "error": f"找不到骨架：{armature_name}"}

    if arm_obj is not None:
        bone_names = [bone.name for bone in arm_obj.data.bones]
        bone_index = {name: index for index, name in enumerate(bone_names)}
        nodes = []
        for bone in arm_obj.data.bones:
            parent_index = bone_index.get(bone.parent.name, -1) if bone.parent else -1
            nodes.append((bone.name, parent_index))
        skeleton = []
        for bone in arm_obj.data.bones:
            euler = bone.matrix_local.to_euler()
            skeleton.append((bone.head_local.x, bone.head_local.y, bone.head_local.z, euler.x, euler.y, euler.z))
    else:
        bone_names = ["static"]
        bone_index = {"static": 0}
        nodes = [("static", -1)]
        skeleton = [(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)]

    vertices: list[list[Any]] = []
    triangles: list[tuple[str, int, int, int]] = []
    for name in mesh_names:
        obj = _object(name)
        if obj is None or obj.type != "MESH":
            continue
        evaluated, mesh = _evaluated_mesh(obj)
        matrix = evaluated.matrix_world
        if arm_obj is not None:
            matrix = arm_obj.matrix_world.inverted() @ matrix
        normal_matrix = matrix.to_3x3().inverted().transposed()
        uv_layer = mesh.uv_layers.active
        for vertex in mesh.vertices:
            links = []
            for group in vertex.groups:
                group_name = obj.vertex_groups[group.group].name
                if group_name in bone_index and group.weight > 0:
                    links.append((bone_index[group_name], float(group.weight)))
            links.sort(key=lambda item: item[1], reverse=True)
            links = links[:3]
            if not links:
                links = [(0, 1.0)]
            position = matrix @ vertex.co
            normal = (normal_matrix @ vertex.normal).normalized()
            vertices.append([links[0][0], position, normal, (0.0, 0.0), links])

        mesh.calc_loop_triangles()
        for triangle in mesh.loop_triangles:
            try:
                material = obj.material_slots[triangle.material_index].material
                material_name = material.name if material is not None else "default"
            except (IndexError, AttributeError):
                material_name = "default"
            indices = []
            for loop_index in triangle.loops:
                loop = mesh.loops[loop_index]
                vertex = mesh.vertices[loop.vertex_index]
                links = []
                for group in vertex.groups:
                    group_name = obj.vertex_groups[group.group].name
                    if group_name in bone_index and group.weight > 0:
                        links.append((bone_index[group_name], float(group.weight)))
                links.sort(key=lambda item: item[1], reverse=True)
                links = links[:3] or [(0, 1.0)]
                position = matrix @ vertex.co
                normal = (normal_matrix @ loop.normal).normalized()
                uv = (0.0, 0.0)
                if uv_layer is not None:
                    uv = tuple(uv_layer.data[loop_index].uv)
                vertices.append([links[0][0], position, normal, uv, links])
                indices.append(len(vertices) - 1)
            triangles.append((material_name, indices[0], indices[1], indices[2]))

    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("version 1\nnodes\n")
        for index, (name, parent) in enumerate(nodes):
            handle.write(f'{index} "{name}" {parent}\n')
        handle.write("end\nskeleton\ntime 0\n")
        for position in skeleton:
            handle.write(" ".join(f"{value:.6f}" for value in position) + "\n")
        handle.write("end\ntriangles\n")
        for material_name, a, b, c in triangles:
            handle.write(f"{material_name}\n")
            for index in (a, b, c):
                parent, position, normal, uv, links = vertices[index]
                link_text = " ".join(f"{bone} {weight:.6f}" for bone, weight in links)
                handle.write(
                    f"{parent} {position.x:.6f} {position.y:.6f} {position.z:.6f} "
                    f"{normal.x:.6f} {normal.y:.6f} {normal.z:.6f} "
                    f"{uv[0]:.6f} {uv[1]:.6f} {link_text}\n"
                )
        handle.write("end\n")
    return {"ok": True, "path": target, "triangles": len(triangles), "vertices": len(vertices)}


def bake_pbr(name: str, size: int = 2048, output_dir: str | None = None) -> dict[str, Any]:
    obj = _object(name)
    if obj is None or obj.type != "MESH":
        return {"ok": False, "error": f"找不到网格：{name}"}
    if not obj.data.materials:
        return {"ok": False, "error": f"网格 {name} 没有材质。"}
    size = max(64, min(int(size), 4096))
    target_dir = Path(output_dir) if output_dir else Path(bpy.path.abspath("//")) / "bake"
    target_dir.mkdir(parents=True, exist_ok=True)
    image = bpy.data.images.new(f"{name}_basecolor", width=size, height=size, alpha=True)
    material = obj.data.materials[0]
    material.use_nodes = True
    nodes = material.node_tree.nodes
    node = nodes.new("ShaderNodeTexImage")
    node.image = image
    nodes.active = node
    _activate(obj)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 4
    try:
        bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, use_clear=True)
    except Exception as exc:
        return {"ok": False, "error": f"烘焙失败：{exc}"}
    output = target_dir / f"{name}_basecolor.png"
    image.filepath_raw = str(output)
    image.file_format = "PNG"
    image.save()
    return {"ok": True, "path": str(output), "size": size}


__all__ = [
    "add_box",
    "add_cylinder",
    "add_uv_sphere",
    "assign_material",
    "bake_pbr",
    "bevel",
    "bind_mesh",
    "build_armature",
    "export_smd",
    "import_ysm_reference",
    "join_meshes",
    "mirror_x",
    "new_material",
    "render_preview",
    "reset_scene",
    "save_blend",
    "scene_summary",
    "smart_uv",
    "validate_hands",
]
