import copy
import math
import os
import time
import bpy
import bpy_extras
import mathutils
from bpy.props import CollectionProperty, StringProperty
from bpy.types import Operator, OperatorFileListElement

from .core import animation_lists, custom_schemes_manager, detection_manager, utils
from .retarget_properties import BoneListItem, get_source_armature, get_target_armature

RETARGET_ID = "_MOTIVE_RETARGET"
HELPER_SUFFIX = "_MOTIVE_RETARGET_HELPER"
ANCHOR_PREFIX = "MotiveRetarget_Anchor_"


def _orthonormalize_mat3(mat3):
    """Returns a pure orthogonal rotation Matrix (3x3) free of scale/shear."""
    return mat3.to_quaternion().to_matrix()


def _is_target_linked(armature_target):
    """Returns True if armature_target currently has live retarget preview constraints."""
    if not armature_target or armature_target.type != "ARMATURE":
        return False
    for pb in armature_target.pose.bones:
        if any(RETARGET_ID in c.name for c in pb.constraints):
            return True
    return False


def _find_preview_objects(armature_target):
    """Finds the helper armature and anchor Empty currently linked to armature_target."""
    helper = None
    if armature_target and armature_target.type == "ARMATURE":
        for pb in armature_target.pose.bones:
            for c in pb.constraints:
                if RETARGET_ID in c.name and getattr(c, "target", None) is not None:
                    helper = c.target
                    break
            if helper is not None:
                break
    anchor = None
    if helper is not None and helper.parent is not None:
        if helper.parent.name.startswith(ANCHOR_PREFIX) or helper.parent.get("motive_retarget_anchor"):
            anchor = helper.parent
    return helper, anchor


def _cleanup_retarget_link(armature_target, reset_pose=False):
    """Removes all live retarget constraints on target and deletes helper armature + anchor."""
    helper, anchor = _find_preview_objects(armature_target)

    if armature_target and armature_target.type == "ARMATURE":
        for pb in armature_target.pose.bones:
            for c in list(pb.constraints):
                if RETARGET_ID in c.name:
                    pb.constraints.remove(c)
            if reset_pose:
                pb.location = (0.0, 0.0, 0.0)
                pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
                pb.rotation_euler = (0.0, 0.0, 0.0)
                pb.scale = (1.0, 1.0, 1.0)

    if helper is not None:
        helper_data = helper.data if helper.type == "ARMATURE" else None
        try:
            bpy.data.objects.remove(helper, do_unlink=True)
        except ReferenceError:
            pass
        if helper_data is not None and helper_data.users == 0:
            try:
                bpy.data.armatures.remove(helper_data)
            except ReferenceError:
                pass

    if anchor is not None:
        try:
            bpy.data.objects.remove(anchor, do_unlink=True)
        except ReferenceError:
            pass


def _read_anim_start_end(armature):
    frame_start = None
    frame_end = None
    for fcurve in detection_manager.iter_action_fcurves(armature):
        for key in fcurve.keyframe_points:
            keyframe = key.co.x
            if frame_start is None or keyframe < frame_start:
                frame_start = keyframe
            if frame_end is None or keyframe > frame_end:
                frame_end = keyframe
    return frame_start if frame_start is not None else 1, frame_end if frame_end is not None else 250


def _find_root_bones(armature_target, retarget_bone_list):
    root_bones = [b for b in armature_target.pose.bones if not b.parent]
    root_bones_animated = []
    target_bones = [item.bone_name_target for item in retarget_bone_list]
    while root_bones:
        for bone in list(root_bones):
            root_bones.remove(bone)
            if bone.name in target_bones:
                root_bones_animated.append(bone.name)
            else:
                for bone_child in bone.children:
                    root_bones.append(bone_child)
    return root_bones_animated


def _get_source_item_world_head(armature_source, bone_name_source, use_pose=True):
    pb = armature_source.pose.bones.get(bone_name_source)
    if pb is not None:
        return armature_source.matrix_world @ (pb.head if use_pose else pb.bone.head_local)
    if bone_name_source == armature_source.name:
        return armature_source.matrix_world.translation.copy()
    return None


def _compute_scale_factor(armature_source, armature_target, retarget_bone_list, root_bones):
    source_min = None
    source_min_root = None
    target_min = None
    target_min_root = None

    for item in retarget_bone_list:
        bone_target = armature_target.pose.bones.get(item.bone_name_target)
        if not bone_target:
            continue

        src_head = _get_source_item_world_head(armature_source, item.bone_name_source, use_pose=True)
        if src_head is None:
            continue

        bone_source_z = src_head.z
        bone_target_z = (armature_target.matrix_world @ bone_target.head).z

        if item.bone_name_target in root_bones:
            if source_min_root is None or source_min_root > bone_source_z:
                source_min_root = bone_source_z
            if target_min_root is None or target_min_root > bone_target_z:
                target_min_root = bone_target_z

        if source_min is None or source_min > bone_source_z:
            source_min = bone_source_z
        if target_min is None or target_min > bone_target_z:
            target_min = bone_target_z

    if (
        source_min_root is None
        or source_min is None
        or target_min_root is None
        or target_min is None
    ):
        return 1.0

    source_height = source_min_root - source_min
    target_height = target_min_root - target_min
    if abs(source_height) < 1e-4 or abs(target_height) < 1e-4:
        return 1.0

    ratio = target_height / source_height
    return ratio if ratio > 0.0 else 1.0


def _compute_horizontal_yaw_delta(armature_source, armature_target, retarget_bone_list):
    """
    Computes the signed World-Z yaw angle from Source's facing orientation to
    Target's facing orientation using symmetric left/right bone pairs (arms, shoulders, legs).
    Returns 0.0 if no symmetric pair is mapped or vectors are degenerate.
    """
    by_key = {
        item.bone_name_key: item
        for item in retarget_bone_list
        if item.bone_name_key
    }
    symmetric_pairs = [
        ("leftUpperArm", "rightUpperArm"),
        ("leftShoulder", "rightShoulder"),
        ("leftUpLeg", "rightUpLeg"),
        ("leftLowerArm", "rightLowerArm"),
        ("leftLeg", "rightLeg"),
    ]
    for left_key, right_key in symmetric_pairs:
        if left_key not in by_key or right_key not in by_key:
            continue
        l_item = by_key[left_key]
        r_item = by_key[right_key]

        src_l = _get_source_item_world_head(armature_source, l_item.bone_name_source, use_pose=True)
        src_r = _get_source_item_world_head(armature_source, r_item.bone_name_source, use_pose=True)
        pb_tl = armature_target.pose.bones.get(l_item.bone_name_target)
        pb_tr = armature_target.pose.bones.get(r_item.bone_name_target)
        if src_l is None or src_r is None or pb_tl is None or pb_tr is None:
            continue

        tgt_l = armature_target.matrix_world @ pb_tl.head
        tgt_r = armature_target.matrix_world @ pb_tr.head

        v_src = mathutils.Vector((src_l.x - src_r.x, src_l.y - src_r.y))
        v_tgt = mathutils.Vector((tgt_l.x - tgt_r.x, tgt_l.y - tgt_r.y))
        if v_src.length < 1e-4 or v_tgt.length < 1e-4:
            continue

        ang_src = math.atan2(v_src.y, v_src.x)
        ang_tgt = math.atan2(v_tgt.y, v_tgt.x)
        delta = ang_tgt - ang_src
        # Normalize to [-pi, pi]
        return math.atan2(math.sin(delta), math.cos(delta))

    return 0.0


def _reset_armature_pose(armature):
    for pb in armature.pose.bones:
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.rotation_euler = (0.0, 0.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)


def _save_armature_pose(armature):
    saved = {}
    for pb in armature.pose.bones:
        saved[pb.name] = (
            pb.location.copy(),
            pb.rotation_quaternion.copy(),
            pb.rotation_euler.copy(),
            pb.scale.copy(),
        )
    return saved


def _restore_armature_pose(armature, saved):
    for name, (loc, q, e, sc) in saved.items():
        pb = armature.pose.bones.get(name)
        if pb is not None:
            pb.location = loc
            pb.rotation_quaternion = q
            pb.rotation_euler = e
            pb.scale = sc


def _collect_valid_bone_list(context, armature_source, armature_target):
    retarget_bone_list = []
    for item in context.scene.motive_retargeting_bone_list:
        if not item.bone_name_source or not item.bone_name_target:
            continue
        source_is_obj = item.bone_name_source == armature_source.name
        if not source_is_obj and not armature_source.pose.bones.get(item.bone_name_source):
            continue
        if not armature_target.pose.bones.get(item.bone_name_target):
            continue
        retarget_bone_list.append(item)
    return retarget_bone_list


def _build_retarget_link(context, armature_source, armature_target, retarget_bone_list, root_bones):
    """
    Creates an offset-independent, zero-lever-arm retargeting rig consisting of:
      1. An Anchor Empty applying A = T(P_tgt_0) @ Rz(delta_yaw) @ S(scale_factor) @ T(-P_src_0)
      2. A hidden Helper Armature parented to Anchor that tracks Source's exact object + local pose
         and carries rest-axis-aligned helper bones placed directly at each source bone's pivot (zero lever arm).
      3. COPY_ROTATION (all mapped bones) and COPY_LOCATION (root bones) constraints on Target.
    """
    _cleanup_retarget_link(armature_target, reset_pose=False)

    scene = context.scene
    orig_frame = scene.frame_current
    orig_src_action = armature_source.animation_data.action if armature_source.animation_data else None
    orig_src_slot = (
        getattr(armature_source.animation_data, "action_slot", None)
        if armature_source.animation_data
        else None
    )
    use_rest = scene.motive_retargeting_use_pose == "REST"
    keep_target_pos = getattr(scene, "motive_retargeting_keep_target_position", True)
    align_facing = getattr(scene, "motive_retargeting_align_facing", True) and keep_target_pos
    auto_scale = getattr(scene, "motive_retargeting_auto_scaling", True)

    primary_root_item = None
    for item in retarget_bone_list:
        if item.bone_name_target in root_bones:
            primary_root_item = item
            break

    # 1. Measure source root world position at frame_start (with animation active)
    frame_start, _ = _read_anim_start_end(armature_source)
    scene.frame_set(int(frame_start))
    context.view_layer.update()
    p_src_start = (
        _get_source_item_world_head(armature_source, primary_root_item.bone_name_source, use_pose=True)
        if primary_root_item
        else armature_source.matrix_world.translation.copy()
    )

    saved_src_pose = _save_armature_pose(armature_source)
    saved_tgt_pose = _save_armature_pose(armature_target)

    try:
        # 2. Evaluate clean reference pose (REST or CURRENT)
        armature_source.data.pose_position = "POSE"
        armature_target.data.pose_position = "POSE"

        if use_rest:
            if armature_source.animation_data:
                armature_source.animation_data.action = None
            _reset_armature_pose(armature_source)
            _reset_armature_pose(armature_target)
            context.view_layer.update()

        m_src_ref = armature_source.matrix_world.copy()
        m_tgt_ref = armature_target.matrix_world.copy()

        # Reference root positions
        p_src_rest = (
            _get_source_item_world_head(armature_source, primary_root_item.bone_name_source, use_pose=True)
            if primary_root_item
            else m_src_ref.translation.copy()
        )
        if primary_root_item and armature_target.pose.bones.get(primary_root_item.bone_name_target):
            pb_tgt_root = armature_target.pose.bones[primary_root_item.bone_name_target]
            p_tgt_rest = m_tgt_ref @ pb_tgt_root.head
        else:
            p_tgt_rest = m_tgt_ref.translation.copy()

        # Scale factor & facing yaw alignment
        scale_factor = (
            _compute_scale_factor(armature_source, armature_target, retarget_bone_list, root_bones)
            if auto_scale
            else 1.0
        )
        delta_yaw = (
            _compute_horizontal_yaw_delta(armature_source, armature_target, retarget_bone_list)
            if align_facing
            else 0.0
        )

        # Build exact rigid + uniform-scale alignment matrix A:
        #   A @ P = P_tgt_0 + scale_factor * Rz(delta_yaw) @ (P - P_src_0)
        if keep_target_pos:
            p_src_0 = mathutils.Vector((p_src_start.x, p_src_start.y, p_src_rest.z))
            p_tgt_0 = p_tgt_rest.copy()
        else:
            p_src_0 = mathutils.Vector((0.0, 0.0, p_src_rest.z))
            p_tgt_0 = mathutils.Vector((0.0, 0.0, p_tgt_rest.z))

        mat_align = (
            mathutils.Matrix.Translation(p_tgt_0)
            @ mathutils.Matrix.Rotation(delta_yaw, 4, "Z")
            @ mathutils.Matrix.Scale(scale_factor, 4)
            @ mathutils.Matrix.Translation(-p_src_0)
        )

        # Capture reference bone transforms while in reference pose
        m_helper_ref = mat_align @ m_src_ref
        m_helper_ref_rot_inv = _orthonormalize_mat3(m_helper_ref.to_3x3()).inverted()

        bone_ref_specs = {}
        for item in retarget_bone_list:
            pb_tgt = armature_target.pose.bones.get(item.bone_name_target)
            if not pb_tgt:
                continue
            r_tgt_world = _orthonormalize_mat3((m_tgt_ref @ pb_tgt.matrix).to_3x3())

            pb_src = armature_source.pose.bones.get(item.bone_name_source)
            if pb_src is not None:
                m_src_bone_eval = _orthonormalize_mat3(pb_src.matrix.to_3x3())
                m_src_bone_rest = _orthonormalize_mat3(pb_src.bone.matrix_local.to_3x3())
                m_delta = m_src_bone_rest @ m_src_bone_eval.inverted()
                r_new_rest = _orthonormalize_mat3(m_delta @ m_helper_ref_rot_inv @ r_tgt_world)
                bone_ref_specs[item.bone_name_target] = (item.bone_name_source, r_new_rest)
            else:
                # Source root is the Armature object itself
                r_new_rest = _orthonormalize_mat3(m_helper_ref_rot_inv @ r_tgt_world)
                bone_ref_specs[item.bone_name_target] = (None, r_new_rest)

        # 3. Create Anchor Empty
        target_collection = (
            armature_target.users_collection[0]
            if armature_target.users_collection
            else context.collection
        )
        anchor = bpy.data.objects.new(ANCHOR_PREFIX + armature_target.name, None)
        anchor.empty_display_type = "ARROWS"
        anchor.empty_display_size = 0.25
        anchor.matrix_world = mat_align
        anchor.hide_render = True
        anchor["motive_retarget_anchor"] = True
        target_collection.objects.link(anchor)

        # 4. Create clean Helper Armature (no copied Action — driven live by Source)
        helper_data = armature_source.data.copy()
        helper_data.name = armature_source.data.name + HELPER_SUFFIX
        armature_helper = bpy.data.objects.new(armature_target.name + HELPER_SUFFIX, helper_data)
        target_collection.objects.link(armature_helper)
        armature_helper.parent = anchor
        armature_helper.matrix_parent_inverse = mathutils.Matrix.Identity(4)
        armature_helper.matrix_local = m_src_ref
        armature_helper.hide_render = True
        armature_helper["motive_retarget_source"] = armature_source.name
        armature_helper["motive_retarget_target"] = armature_target.name

        # Object-level tracking: M_helper_world(t) = A @ M_src_world(t)
        obj_con = armature_helper.constraints.new("COPY_TRANSFORMS")
        obj_con.name = "MotiveSourceTrack" + RETARGET_ID
        obj_con.target = armature_source
        obj_con.target_space = "WORLD"
        obj_con.owner_space = "LOCAL"

        orig_bone_names = [b.name for b in armature_source.pose.bones]

        # 5. Add zero-lever-arm helper bones inside Helper Armature Edit Mode
        bpy.ops.object.select_all(action="DESELECT")
        utils.set_active(armature_helper)
        bpy.ops.object.mode_set(mode="EDIT")

        for tgt_bone_name, (src_bone_name, r_new_rest) in bone_ref_specs.items():
            eb_src = armature_helper.data.edit_bones.get(src_bone_name) if src_bone_name else None
            eb_new = armature_helper.data.edit_bones.new(tgt_bone_name + RETARGET_ID)

            # Place helper bone directly at Source pivot (ZERO lever arm!)
            if eb_src is not None:
                head_loc = eb_src.head.copy()
                bone_len = eb_src.length if eb_src.length > 1e-4 else 0.1
            else:
                head_loc = mathutils.Vector((0.0, 0.0, 0.0))
                bone_len = 0.1

            y_axis = r_new_rest.col[1].normalized()
            eb_new.head = head_loc
            eb_new.tail = head_loc + y_axis * bone_len
            eb_new.roll = utils.mat3_to_vec_roll(r_new_rest)
            eb_new.parent = eb_src  # None when source_is_obj

        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        context.view_layer.update()

        # PoseBone-level tracking: mirror Source's exact local pose onto Helper's original bones
        for bname in orig_bone_names:
            pb_h = armature_helper.pose.bones.get(bname)
            if pb_h is None:
                continue
            con = pb_h.constraints.new("COPY_TRANSFORMS")
            con.name = "MotivePoseTrack" + RETARGET_ID
            con.target = armature_source
            con.subtarget = bname
            con.target_space = "LOCAL"
            con.owner_space = "LOCAL"

        # Hide helper armature in viewport (eye icon only so depsgraph still evaluates it)
        armature_helper.hide_set(True)
        anchor.hide_set(True)

    finally:
        # Always restore source action, poses, and timeline frame
        if armature_source.animation_data:
            armature_source.animation_data.action = orig_src_action
            if orig_src_slot is not None and hasattr(armature_source.animation_data, "action_slot"):
                try:
                    armature_source.animation_data.action_slot = orig_src_slot
                except Exception:
                    pass
        if not use_rest:
            _restore_armature_pose(armature_source, saved_src_pose)
            _restore_armature_pose(armature_target, saved_tgt_pose)
        scene.frame_set(orig_frame)

    # 6. Attach live constraints onto Target Armature
    for item in retarget_bone_list:
        bone_target = armature_target.pose.bones.get(item.bone_name_target)
        if not bone_target:
            continue

        c_rot = bone_target.constraints.new("COPY_ROTATION")
        c_rot.name += RETARGET_ID
        c_rot.target = armature_helper
        c_rot.subtarget = item.bone_name_target + RETARGET_ID

        if bone_target.name in root_bones:
            c_loc = bone_target.constraints.new("COPY_LOCATION")
            c_loc.name += RETARGET_ID
            c_loc.target = armature_helper
            c_loc.subtarget = item.bone_name_target + RETARGET_ID

    context.view_layer.update()
    return armature_helper, anchor


def _select_pose_bone(armature, bone_name, state=True):
    """Selects a pose bone across Blender 3.6-4.x (Bone.select) and 5.0+ (PoseBone.select)."""
    pb = armature.pose.bones.get(bone_name)
    if pb is not None and hasattr(pb, "select"):
        pb.select = state
        return
    db = armature.data.bones.get(bone_name)
    if db is not None and hasattr(db, "select"):
        db.select = state


def _get_action_fcurves(action, obj=None, ensure=False):
    """Returns the FCurves collection for an Action on Blender 3.6-4.x and 5.0+ (Slotted Actions)."""
    if action is None:
        return None
    if hasattr(action, "fcurves"):
        return action.fcurves

    if not hasattr(action, "layers"):
        return None

    slot = None
    if obj is not None and obj.animation_data and getattr(obj.animation_data, "action_slot", None):
        if obj.animation_data.action == action:
            slot = obj.animation_data.action_slot
    if slot is None and len(action.slots) > 0:
        slot = action.slots[0]
    if slot is None and ensure:
        slot_name = obj.name if obj is not None else "Object"
        slot = action.slots.new(id_type="OBJECT", name=slot_name)
    if slot is None:
        return None

    if obj is not None and obj.animation_data and obj.animation_data.action == action:
        if getattr(obj.animation_data, "action_slot", None) != slot:
            obj.animation_data.action_slot = slot

    if not action.layers:
        if not ensure:
            return None
        action.layers.new("Layer")
    layer = action.layers[0]

    if not layer.strips:
        if not ensure:
            return None
        layer.strips.new(type="KEYFRAME")
    strip = layer.strips[0]

    channelbag = strip.channelbag(slot, ensure=ensure)
    return channelbag.fcurves if channelbag is not None else None


def _new_fcurve(fcurves_collection, data_path, index, group_name=""):
    """Creates an FCurve cleanly on both Blender <5.0 (action_group) and 5.0+ (group_name)."""
    if bpy.app.version >= (5, 0, 0):
        if group_name:
            return fcurves_collection.new(
                data_path=data_path, index=index, group_name=group_name
            )
        return fcurves_collection.new(data_path=data_path, index=index)
    return fcurves_collection.new(
        data_path=data_path, index=index, action_group=group_name
    )


class MOTIVE_OT_build_bone_list(Operator):
    """Builds the bone list from the animated source armature and auto-matches target bones."""

    bl_idname = "motive.build_bone_list"
    bl_label = "Build Bone List"
    bl_description = "Builds the bone list from the animation and automatically detects and matches bones"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        armature_source = get_source_armature()
        armature_target = get_target_armature()

        if not armature_source or not armature_source.animation_data or not armature_source.animation_data.action:
            self.report(
                {"ERROR"},
                "No animation found on the source armature!\nSelect an armature with keyframed animation as source.",
            )
            return {"CANCELLED"}

        if not armature_target:
            self.report({"ERROR"}, "Please select a target character armature.")
            return {"CANCELLED"}

        if armature_source.name == armature_target.name:
            self.report(
                {"ERROR"},
                "Source and target armature are the same!\nPlease select different armatures.",
            )
            return {"CANCELLED"}

        retargeting_dict = detection_manager.detect_retarget_bones()

        context.scene.motive_retargeting_bone_list.clear()

        for bone_source, bone_values in retargeting_dict.items():
            bone_target, bone_key = bone_values

            bone_item = context.scene.motive_retargeting_bone_list.add()
            bone_item.bone_name_key = bone_key
            bone_item.bone_name_source = bone_source
            bone_item.bone_name_target = bone_target

        self.report(
            {"INFO"},
            f"Built bone list with {len(context.scene.motive_retargeting_bone_list)} entries.",
        )
        return {"FINISHED"}


class MOTIVE_OT_add_bone_list_item(Operator):
    """Adds a customizable bone list item to the retargeting list."""

    bl_idname = "motive.add_bone_list_item"
    bl_label = "Add Bone List Item"
    bl_description = "Adds a customizable bone list item"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        bone_item = context.scene.motive_retargeting_bone_list.add()
        bone_item.is_custom = True
        context.scene.motive_retargeting_bone_list_index = (
            len(context.scene.motive_retargeting_bone_list) - 1
        )
        return {"FINISHED"}


class MOTIVE_OT_remove_bone_list_item(Operator):
    """Removes the selected bone item from the retargeting list."""

    bl_idname = "motive.remove_bone_list_item"
    bl_label = "Remove Selected Entry"
    bl_description = "Removes the selected bone pair from the retargeting list"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        bone_list = context.scene.motive_retargeting_bone_list
        index = context.scene.motive_retargeting_bone_list_index

        if 0 <= index < len(bone_list):
            bone_list.remove(index)
            context.scene.motive_retargeting_bone_list_index = max(0, min(index, len(bone_list) - 1))
            self.report({"INFO"}, "Bone entry removed.")
            return {"FINISHED"}

        self.report({"WARNING"}, "No bone entry selected to remove.")
        return {"CANCELLED"}


class MOTIVE_OT_clear_bone_list(Operator):
    """Clears target bone assignments in the retargeting bone list."""

    bl_idname = "motive.clear_bone_list"
    bl_label = "Clear Bone List"
    bl_description = "Clears the target bone list entries so you can manually fill them in"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        for bone_item in context.scene.motive_retargeting_bone_list:
            bone_item.bone_name_target = ""
        return {"FINISHED"}


class MOTIVE_OT_link_retarget_preview(Operator):
    """Links live offset-independent retargeting constraints so you can preview motion before baking."""

    bl_idname = "motive.link_retarget_preview"
    bl_label = "Preview (Live Constraints)"
    bl_description = "Link non-destructive retargeting constraints to preview animation at the target's own position before baking"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        armature_source = get_source_armature()
        armature_target = get_target_armature()

        if not armature_source or not armature_source.animation_data or not armature_source.animation_data.action:
            self.report(
                {"ERROR"},
                "No animation found on source armature!\nSelect an armature with animation as source.",
            )
            return {"CANCELLED"}

        if not armature_target:
            self.report({"ERROR"}, "Please select a target armature.")
            return {"CANCELLED"}

        if armature_source.name == armature_target.name:
            self.report({"ERROR"}, "Source and target armature are the same!")
            return {"CANCELLED"}

        retarget_bone_list = _collect_valid_bone_list(context, armature_source, armature_target)
        if not retarget_bone_list:
            self.report(
                {"ERROR"},
                "No valid mapped bone pairs found. Check mappings or click Rebuild Bone List.",
            )
            return {"CANCELLED"}

        root_bones = _find_root_bones(armature_target, retarget_bone_list)
        if not root_bones:
            self.report(
                {"ERROR"},
                "No root bone found!\nCheck if bones are mapped correctly or try rebuilding the bone list.",
            )
            return {"CANCELLED"}

        seen = {}
        for item in retarget_bone_list:
            seen[item.bone_name_target] = seen.get(item.bone_name_target, 0) + 1
        duplicates = [k for k, v in seen.items() if v > 1]
        if duplicates:
            self.report(
                {"ERROR"},
                f"Duplicate target bone entries found: {', '.join(duplicates)}. Use each target bone once.",
            )
            return {"CANCELLED"}

        if context.active_object and context.active_object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        _build_retarget_link(context, armature_source, armature_target, retarget_bone_list, root_bones)

        bpy.ops.object.select_all(action="DESELECT")
        utils.set_active(armature_target)
        self.report({"INFO"}, "Live retarget preview linked! Press Play to inspect, then click Bake Animation or Unlink.")
        return {"FINISHED"}


class MOTIVE_OT_unlink_retarget_preview(Operator):
    """Removes live retargeting preview constraints and cleans up temporary helper objects."""

    bl_idname = "motive.unlink_retarget_preview"
    bl_label = "Unlink Preview"
    bl_description = "Remove live retargeting preview constraints and helper rig without baking"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        armature_target = get_target_armature()
        if not armature_target:
            self.report({"WARNING"}, "No target armature selected.")
            return {"CANCELLED"}

        if context.active_object and context.active_object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        _cleanup_retarget_link(armature_target, reset_pose=True)
        bpy.ops.object.select_all(action="DESELECT")
        utils.set_active(armature_target)
        context.view_layer.update()
        self.report({"INFO"}, "Retarget preview unlinked and helper rig cleaned up.")
        return {"FINISHED"}


class MOTIVE_OT_retarget_animation(Operator):
    """Retargets (or bakes an active live preview of) the offline FBX/BVH animation onto the target character."""

    bl_idname = "motive.retarget_animation"
    bl_label = "Retarget Animation"
    bl_description = "Retargets and bakes the animation from the source armature to the target armature"
    bl_options = {"REGISTER", "UNDO"}

    retarget_bone_list: list = []

    def execute(self, context):
        armature_source = get_source_armature()
        armature_target = get_target_armature()

        if not armature_source or not armature_source.animation_data or not armature_source.animation_data.action:
            self.report(
                {"ERROR"},
                "No animation found on source armature!\nSelect an armature with animation as source.",
            )
            return {"CANCELLED"}

        if not armature_target:
            self.report({"ERROR"}, "Please select a target armature.")
            return {"CANCELLED"}

        if armature_source.name == armature_target.name:
            self.report({"ERROR"}, "Source and target armature are the same!")
            return {"CANCELLED"}

        if context.active_object and context.active_object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        was_preview_linked = _is_target_linked(armature_target)

        if was_preview_linked:
            # Bake directly from active live preview constraints (preserves any live anchor adjustments)
            target_bones_to_bake = []
            root_bones = []
            for pb in armature_target.pose.bones:
                has_rot = any(RETARGET_ID in c.name and c.type == "COPY_ROTATION" for c in pb.constraints)
                has_loc = any(RETARGET_ID in c.name and c.type == "COPY_LOCATION" for c in pb.constraints)
                if has_rot or has_loc:
                    target_bones_to_bake.append(pb.name)
                if has_loc:
                    root_bones.append(pb.name)
            if not target_bones_to_bake:
                self.report({"ERROR"}, "No active retarget preview constraints found on target armature.")
                return {"CANCELLED"}
        else:
            # Build retargeting bone list
            self.retarget_bone_list = _collect_valid_bone_list(context, armature_source, armature_target)
            if not self.retarget_bone_list:
                self.report(
                    {"ERROR"},
                    "No valid mapped bone pairs found. Check mappings or click Rebuild Bone List.",
                )
                return {"CANCELLED"}

            # Find the root bones
            root_bones = _find_root_bones(armature_target, self.retarget_bone_list)
            if not root_bones:
                self.report(
                    {"ERROR"},
                    "No root bone found!\nCheck if bones are mapped correctly or try rebuilding the bone list.",
                )
                return {"CANCELLED"}

            # Check for duplicate target bone entries
            seen = {}
            for item in self.retarget_bone_list:
                seen[item.bone_name_target] = seen.get(item.bone_name_target, 0) + 1
            duplicates = [k for k, v in seen.items() if v > 1]
            if duplicates:
                self.report(
                    {"ERROR"},
                    f"Duplicate target bone entries found: {', '.join(duplicates)}. Use each target bone once.",
                )
                return {"CANCELLED"}

            # Save custom schemes if user adjusted mappings
            custom_schemes_manager.save_retargeting_to_list()

            # Build offset-independent zero-lever-arm link
            _build_retarget_link(context, armature_source, armature_target, self.retarget_bone_list, root_bones)
            target_bones_to_bake = [item.bone_name_target for item in self.retarget_bone_list]

        rotation_mode = armature_target.rotation_mode

        try:
            # Select ONLY mapped bones on target armature for clean baking
            for pb in armature_target.pose.bones:
                _select_pose_bone(armature_target, pb.name, False)
            for bname in target_bones_to_bake:
                _select_pose_bone(armature_target, bname, True)

            # Bake animation driven by live helper constraints
            self.bake_animation(armature_source, armature_target, root_bones)

            # Rename baked action
            if armature_target.animation_data and armature_target.animation_data.action:
                src_action_name = (
                    armature_source.animation_data.action.name
                    if (armature_source.animation_data and armature_source.animation_data.action)
                    else "Motive"
                )
                armature_target.animation_data.action.name = src_action_name + " Retarget"
        finally:
            # Always clean up temporary helper armature, anchor, and constraints
            _cleanup_retarget_link(armature_target, reset_pose=False)
            armature_target.rotation_mode = rotation_mode

        bpy.ops.object.select_all(action="DESELECT")
        utils.set_active(armature_target)
        context.view_layer.update()
        self.report({"INFO"}, "Successfully retargeted animation!")
        return {"FINISHED"}

    def find_root_bones(self, armature_target):
        root_bones = [b for b in armature_target.pose.bones if not b.parent]
        root_bones_animated = []
        target_bones = [item.bone_name_target for item in self.retarget_bone_list]
        while root_bones:
            for bone in list(root_bones):
                root_bones.remove(bone)
                if bone.name in target_bones:
                    root_bones_animated.append(bone.name)
                else:
                    for bone_child in bone.children:
                        root_bones.append(bone_child)
        return root_bones_animated

    def clean_animation(self, armature_source):
        deletable_fcurves = ["scale"]
        if not armature_source.animation_data or not armature_source.animation_data.action:
            return
        fcurves = _get_action_fcurves(armature_source.animation_data.action, armature_source)
        if not fcurves:
            return
        for fcurve in list(fcurves):
            if fcurve.data_path in deletable_fcurves:
                fcurves.remove(fcurve)

    def get_and_reset_pose_rotations(self, armature):
        bpy.ops.object.select_all(action="DESELECT")
        utils.set_active(armature)
        bpy.ops.object.mode_set(mode="POSE")

        pose_rotations = {}
        for bone in armature.pose.bones:
            if bone.rotation_mode == "QUATERNION":
                pose_rotations[bone.name] = copy.deepcopy(bone.rotation_quaternion)
                bone.rotation_quaternion = (1, 0, 0, 0)
            else:
                pose_rotations[bone.name] = copy.deepcopy(bone.rotation_euler)
                bone.rotation_euler = (0, 0, 0)

        bpy.ops.object.mode_set(mode="OBJECT")
        return pose_rotations

    def scale_armature(self, armature_source, armature_target, root_bones):
        source_min = None
        source_min_root = None
        target_min = None
        target_min_root = None

        for item in self.retarget_bone_list:
            bone_source = armature_source.pose.bones.get(item.bone_name_source)
            bone_target = armature_target.pose.bones.get(item.bone_name_target)
            if not bone_target:
                continue

            if bone_source:
                bone_source_z = (armature_source.matrix_world @ bone_source.head)[2]
            elif item.bone_name_source == armature_source.name:
                bone_source_z = armature_source.matrix_world.translation[2]
            else:
                continue

            bone_target_z = (armature_target.matrix_world @ bone_target.head)[2]

            if item.bone_name_target in root_bones:
                if source_min_root is None or source_min_root > bone_source_z:
                    source_min_root = bone_source_z
                if target_min_root is None or target_min_root > bone_target_z:
                    target_min_root = bone_target_z

            if source_min is None or source_min > bone_source_z:
                source_min = bone_source_z
            if target_min is None or target_min > bone_target_z:
                target_min = bone_target_z

        if source_min_root is None or source_min is None or target_min_root is None or target_min is None:
            return

        source_height = source_min_root - source_min
        target_height = target_min_root - target_min

        if not source_height or not target_height:
            return

        scale_factor = target_height / source_height
        armature_source.scale *= scale_factor

    def read_anim_start_end(self, armature):
        frame_start = None
        frame_end = None
        for fcurve in detection_manager.iter_action_fcurves(armature):
            for key in fcurve.keyframe_points:
                keyframe = key.co.x
                if frame_start is None or keyframe < frame_start:
                    frame_start = keyframe
                if frame_end is None or keyframe > frame_end:
                    frame_end = keyframe
        return frame_start or 1, frame_end or 250

    def copy_rest_pose(self, context, armature_source):
        context.scene.tool_settings.use_keyframe_insert_auto = False

        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        utils.set_active(armature_source)
        bpy.ops.object.mode_set(mode="OBJECT")

        bpy.ops.object.duplicate_move(
            OBJECT_OT_duplicate={"linked": False, "mode": "TRANSLATION"},
            TRANSFORM_OT_translate={"value": (0, 0, 0)},
        )

        source_armature_copy = context.object
        source_armature_copy.name = armature_source.name + "_copy"

        bpy.ops.object.select_all(action="DESELECT")
        utils.set_active(source_armature_copy)
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.mode_set(mode="POSE")

        action_tmp = source_armature_copy.animation_data.action
        source_armature_copy.animation_data.action = None
        bpy.ops.pose.armature_apply()
        source_armature_copy.animation_data.action = action_tmp

        for bone in source_armature_copy.pose.bones:
            constraint = bone.constraints.new("COPY_TRANSFORMS")
            constraint.name = bone.name
            constraint.target = armature_source
            constraint.subtarget = bone.name

        bpy.ops.object.mode_set(mode="OBJECT")
        return source_armature_copy

    def bake_animation(self, armature_source, armature_target, root_bones):
        frame_split = 25
        frame_start, frame_end = self.read_anim_start_end(armature_source)
        frame_start, frame_end = int(frame_start), int(frame_end)
        utils.set_active(armature_target)

        actions_all = []
        current_step = 0
        steps = int((frame_end - frame_start) / frame_split) + 1
        wm = bpy.context.window_manager
        wm.progress_begin(current_step, steps)

        bpy.ops.object.mode_set(mode="POSE")
        for frame in range(frame_start, frame_end + 2, frame_split):
            start = frame
            end = frame + frame_split - 1
            if end > frame_end:
                end = frame_end
            if start > end:
                continue

            bpy.ops.nla.bake(
                frame_start=start,
                frame_end=end,
                visual_keying=True,
                only_selected=True,
                use_current_action=False,
                bake_types={"POSE"},
            )

            armature_target.animation_data.action.name = "MOTIVE_RETARGETING_" + str(frame)
            actions_all.append(armature_target.animation_data.action)

            current_step += 1
            if steps != current_step:
                wm.progress_update(current_step)

        bpy.ops.object.mode_set(mode="OBJECT")

        if not actions_all:
            wm.progress_end()
            return

        key_counts = {}
        for action in actions_all:
            act_fcurves = _get_action_fcurves(action)
            if not act_fcurves:
                continue
            for fcurve in act_fcurves:
                key = fcurve.data_path + str(fcurve.array_index)
                key_counts[key] = key_counts.get(key, 0) + len(fcurve.keyframe_points)

        action_final = bpy.data.actions.new(name="MOTIVE_RETARGETING_FINAL")
        action_final.use_fake_user = True
        armature_target.animation_data_create().action = action_final
        final_fcurves = _get_action_fcurves(action_final, armature_target, ensure=True)

        first_fcurves = _get_action_fcurves(actions_all[0]) or []
        for fcurve in first_fcurves:
            if fcurve.data_path.endswith("scale"):
                continue
            if fcurve.data_path.endswith("location"):
                bone_name = detection_manager.extract_bone_name_from_data_path(fcurve.data_path)
                if not bone_name or bone_name not in root_bones:
                    continue

            group_name = fcurve.group.name if fcurve.group else ""
            curve_final = _new_fcurve(
                final_fcurves,
                data_path=fcurve.data_path,
                index=fcurve.array_index,
                group_name=group_name,
            )
            keyframe_points = curve_final.keyframe_points
            keyframe_points.add(key_counts[fcurve.data_path + str(fcurve.array_index)])

            idx = 0
            for action in actions_all:
                src_fcurves = _get_action_fcurves(action)
                if not src_fcurves:
                    continue
                fc_to_add = src_fcurves.find(
                    data_path=fcurve.data_path, index=fcurve.array_index
                )
                if not fc_to_add:
                    continue
                for kp in fc_to_add.keyframe_points:
                    keyframe_points[idx].co.x = kp.co.x
                    keyframe_points[idx].co.y = kp.co.y
                    keyframe_points[idx].interpolation = "LINEAR"
                    idx += 1

        for fcurve in final_fcurves:
            if len(fcurve.keyframe_points) <= 2:
                continue
            kp_pre_pre = fcurve.keyframe_points[0]
            kp_pre = fcurve.keyframe_points[1]
            kp_to_delete = []
            for kp in fcurve.keyframe_points[2:]:
                if round(kp_pre_pre.co.y, 5) == round(kp_pre.co.y, 5) == round(kp.co.y, 5):
                    kp_to_delete.append(kp_pre)
                kp_pre_pre = kp_pre
                kp_pre = kp

            for kp in reversed(kp_to_delete):
                fcurve.keyframe_points.remove(kp)

        for action in actions_all:
            bpy.data.actions.remove(action)

        wm.progress_end()

        # Blender 4.4+ Action Slot support
        if hasattr(armature_target.animation_data, "action_slot") and hasattr(
            armature_target.animation_data, "action_suitable_slots"
        ):
            if armature_target.animation_data.action_suitable_slots:
                armature_target.animation_data.action_slot = (
                    armature_target.animation_data.action_suitable_slots[0]
                )


# -------------------------------------------------------------
# REALTIME ACTOR & T-POSE OPERATORS
# -------------------------------------------------------------


class MOTIVE_OT_init_tpose(Operator):
    """Saves the current pose of the selected armature as reference T-Pose."""

    bl_idname = "motive.init_tpose"
    bl_label = "Set as T-Pose"
    bl_description = "Press this button when this character armature is in T-Pose"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.object
        if not obj or obj.type != "ARMATURE":
            self.report({"ERROR"}, "Active object is not an Armature!")
            return {"CANCELLED"}

        bpy.ops.object.mode_set(mode="OBJECT")

        custom_data = obj.get("CUSTOM")
        if not custom_data:
            custom_data = {}

        bones = {}
        for bone in obj.pose.bones:
            rotation_mode = bone.rotation_mode
            bone.rotation_mode = "QUATERNION"

            loc_obj = (bone.matrix @ bone.location) if bone.location else bone.location.copy()
            rot_global = bone.matrix.to_quaternion()
            inherit_rot = obj.data.bones.get(bone.name).use_inherit_rotation

            bones[bone.name] = {
                "location_local": bone.location.copy(),
                "location_object": loc_obj,
                "rotation_local": bone.rotation_quaternion.copy(),
                "rotation_global": rot_global,
                "inherit_rotation": inherit_rot,
            }
            bone.rotation_mode = rotation_mode

        custom_data["motive_tpose_bones"] = copy.deepcopy(bones)
        custom_data["rsl_tpose_bones"] = copy.deepcopy(bones)
        custom_data.pop("motive_ref_tpose", None)
        custom_data.pop("motive_ref_hip_pos", None)
        obj["CUSTOM"] = custom_data

        self.report({"INFO"}, "T-Pose successfully saved for character!")
        return {"FINISHED"}


class MOTIVE_OT_reset_tpose(Operator):
    """Resets the armature to its saved T-Pose."""

    bl_idname = "motive.reset_tpose"
    bl_label = "Reset to T-Pose"
    bl_description = "Use this to reset the armature to its saved T-Pose"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.object
        if not obj or obj.type != "ARMATURE":
            self.report({"ERROR"}, "Active object is not an Armature!")
            return {"CANCELLED"}

        custom_data = obj.get("CUSTOM")
        if not custom_data:
            self.report({"ERROR"}, "Please set the T-Pose first.")
            return {"CANCELLED"}

        tpose_bones = custom_data.get("motive_tpose_bones") or custom_data.get("rsl_tpose_bones")
        if not tpose_bones:
            self.report({"ERROR"}, "Please set the T-Pose first.")
            return {"CANCELLED"}

        for bone_name, data in tpose_bones.items():
            bone = obj.pose.bones.get(bone_name)
            if bone:
                rot_mode = bone.rotation_mode
                bone.rotation_mode = "QUATERNION"
                bone.rotation_quaternion = mathutils.Quaternion(data["rotation_local"])
                bone.location = mathutils.Vector(data["location_local"])
                db = obj.data.bones.get(bone_name)
                if db:
                    db.use_inherit_rotation = data["inherit_rotation"]
                bone.rotation_mode = rot_mode

        custom_data.pop("motive_ref_tpose", None)
        custom_data.pop("motive_ref_hip_pos", None)
        obj["CUSTOM"] = custom_data

        self.report({"INFO"}, "T-Pose successfully restored!")
        return {"FINISHED"}


class MOTIVE_OT_detect_actor_bones(Operator):
    """Automatically detects bones on this armature for all supported naming schemes."""

    bl_idname = "motive.detect_actor_bones"
    bl_label = "Auto Detect"
    bl_description = "Automatically detect actor bones for supported character naming schemes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.object
        if not obj or obj.type != "ARMATURE":
            self.report({"ERROR"}, "Active object is not an Armature!")
            return {"CANCELLED"}

        detected_count = 0
        for bone_name_key in animation_lists.get_bones().keys():
            matched_bone = detection_manager.detect_bone(obj, bone_name_key)
            setattr(obj, "motive_actor_" + bone_name_key, matched_bone)
            if matched_bone:
                detected_count += 1

        self.report({"INFO"}, f"Auto-detected {detected_count} bones on '{obj.name}'.")
        return {"FINISHED"}


class MOTIVE_OT_save_custom_bones(Operator):
    """Saves the current bone mapping to custom schemes so they will be detected automatically."""

    bl_idname = "motive.save_custom_bones"
    bl_label = "Save Current Naming Scheme"
    bl_description = "Saves the currently mapped bones so they are automatically detected in the future"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        obj = context.object
        if not obj or obj.type != "ARMATURE":
            self.report({"ERROR"}, "Please select an active armature object!")
            return {"CANCELLED"}

        saved_count = 0
        for bone_name_key in animation_lists.get_bones().keys():
            bone_selected = getattr(obj, "motive_actor_" + bone_name_key, "")
            if not bone_selected:
                continue

            bone_detected = detection_manager.detect_bone(obj, bone_name_key)
            custom_schemes_manager.save_live_data_bone_to_list(
                bone_name_key, bone_selected, bone_detected
            )
            saved_count += 1

        if saved_count == 0:
            self.report({"WARNING"}, "No mapped bones found on active armature to save!")
            return {"FINISHED"}

        custom_schemes_manager.save_to_file_and_update()
        self.report({"INFO"}, f"Successfully saved {saved_count} mapped bones to custom scheme!")
        return {"FINISHED"}


class MOTIVE_OT_save_custom_bones_retargeting(Operator):
    """Saves the current retargeting bone list to custom schemes."""

    bl_idname = "motive.save_custom_bones_retargeting"
    bl_label = "Save"
    bl_description = "Saves the current retargeting bone pairs to custom naming schemes"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        custom_schemes_manager.save_retargeting_to_list()
        self.report({"INFO"}, "Retargeting bone scheme saved!")
        return {"FINISHED"}


class MOTIVE_OT_import_custom_schemes(Operator, bpy_extras.io_utils.ImportHelper):
    """Import a custom naming scheme JSON file."""

    bl_idname = "motive.import_custom_schemes"
    bl_label = "Import"
    bl_description = "Import custom naming scheme JSON file"
    bl_options = {"REGISTER", "UNDO"}

    files: CollectionProperty(type=OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(maxlen=1024, subtype="FILE_PATH", options={"HIDDEN", "SKIP_SAVE"})
    filter_glob: StringProperty(default="*.json;", options={"HIDDEN"})

    def execute(self, context):
        import_count = 0
        if self.directory:
            for f in self.files:
                if not f.name.endswith(".json"):
                    continue
                custom_schemes_manager.import_custom_list(self.directory, f.name)
                import_count += 1
        elif self.filepath:
            custom_schemes_manager.import_custom_list(
                os.path.dirname(self.filepath), os.path.basename(self.filepath)
            )
            import_count += 1

        custom_schemes_manager.save_to_file_and_update()
        if not import_count:
            self.report({"ERROR"}, "No files were imported.")
            return {"FINISHED"}

        self.report({"INFO"}, "Successfully imported custom naming schemes.")
        return {"FINISHED"}


class MOTIVE_OT_export_custom_schemes(Operator, bpy_extras.io_utils.ExportHelper):
    """Export custom naming schemes to a JSON file."""

    bl_idname = "motive.export_custom_schemes"
    bl_label = "Export"
    bl_description = "Export custom naming schemes to JSON"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json;", options={"HIDDEN"})

    def execute(self, context):
        # Auto-populate from active armature if custom schemes list is currently empty
        if not detection_manager.bone_detection_list_custom and context.object and context.object.type == "ARMATURE":
            for bone_name_key in animation_lists.get_bones().keys():
                bone_selected = getattr(context.object, "motive_actor_" + bone_name_key, "")
                if bone_selected:
                    custom_schemes_manager.save_live_data_bone_to_list(bone_name_key, bone_selected)
            if detection_manager.bone_detection_list_custom:
                custom_schemes_manager.save_to_file_and_update()

        file_name = custom_schemes_manager.export_custom_list(self.filepath)
        if not file_name:
            self.report({"ERROR"}, "No custom naming schemes found to export! Map bones on an armature and click 'Save Current Naming Scheme' first.")
            return {"FINISHED"}

        self.report({"INFO"}, f'Exported naming schemes as "{file_name}".')
        return {"FINISHED"}


class MOTIVE_OT_clear_custom_bones(Operator):
    """Clear all custom bone naming schemes."""

    bl_idname = "motive.clear_custom_bones"
    bl_label = "Clear Custom Bones"
    bl_description = "Clear all custom bone naming schemes"
    bl_options = {"INTERNAL"}

    def draw(self, context):
        layout = self.layout
        layout.separator()
        row = layout.row(align=True)
        row.scale_y = 0.6
        row.label(
            text="You are about to delete all stored custom naming schemes.",
            icon="ERROR",
        )
        row = layout.row(align=True)
        row.scale_y = 0.6
        row.label(text="Continue?", icon="BLANK1")
        layout.separator()

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=400)

    def execute(self, context):
        custom_schemes_manager.delete_custom_bone_list()
        self.report({"INFO"}, "Cleared all custom bone naming schemes!")
        return {"FINISHED"}
