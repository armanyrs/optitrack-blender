import copy
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


class MOTIVE_OT_retarget_animation(Operator):
    """Retargets the offline FBX/BVH animation from the source armature to the target character."""

    bl_idname = "motive.retarget_animation"
    bl_label = "Retarget Animation"
    bl_description = "Retargets the animation from the source armature to the target armature"
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

        # Build retargeting bone list
        self.retarget_bone_list = []
        for item in context.scene.motive_retargeting_bone_list:
            if not item.bone_name_source or not item.bone_name_target:
                continue
            source_is_obj = (item.bone_name_source == armature_source.name)
            if not source_is_obj and not armature_source.pose.bones.get(item.bone_name_source):
                continue
            if not armature_target.pose.bones.get(item.bone_name_target):
                continue
            self.retarget_bone_list.append(item)

        if not self.retarget_bone_list:
            self.report(
                {"ERROR"},
                "No valid mapped bone pairs found. Check mappings or click Rebuild Bone List.",
            )
            return {"CANCELLED"}

        # Find the root bones
        root_bones = self.find_root_bones(armature_target)
        if not root_bones:
            self.report(
                {"ERROR"},
                "No root bone found!\nCheck if bones are mapped correctly or try rebuilding the bone list.",
            )
            return {"CANCELLED"}

        # Check for duplicate target bone entries
        seen = {}
        for item in self.retarget_bone_list:
            count = seen.get(item.bone_name_target, 0)
            seen[item.bone_name_target] = count + 1
        duplicates = [k for k, v in seen.items() if v > 1]
        if duplicates:
            self.report(
                {"ERROR"},
                f"Duplicate target bone entries found: {', '.join(duplicates)}. Use each target bone once.",
            )
            return {"CANCELLED"}

        # Save custom schemes if user adjusted mappings
        custom_schemes_manager.save_retargeting_to_list()

        # Prepare armatures
        utils.set_active(armature_target)
        bpy.ops.object.mode_set(mode="OBJECT")
        utils.set_active(armature_source)
        bpy.ops.object.mode_set(mode="OBJECT")

        armature_source.data.pose_position = "POSE"
        armature_target.data.pose_position = "POSE"

        if context.scene.motive_retargeting_use_pose == "REST":
            self.get_and_reset_pose_rotations(armature_source)
            self.get_and_reset_pose_rotations(armature_target)

        # Auto scaling
        source_scale = None
        if context.scene.motive_retargeting_auto_scaling:
            self.clean_animation(armature_source)
            source_scale = copy.deepcopy(armature_source.scale)
            self.scale_armature(armature_source, armature_target, root_bones)

        # Duplicate source armature
        armature_source_original = armature_source
        armature_source = self.copy_rest_pose(context, armature_source)

        # Save target transforms
        rotation_mode = armature_target.rotation_mode

        # Calculate target bone rest transforms relative to source armature without transform_apply
        bone_transforms = {}
        for item in self.retarget_bone_list:
            bone = armature_target.data.bones.get(item.bone_name_target)
            if not bone:
                continue
            head_world = armature_target.matrix_world @ bone.head_local
            tail_world = armature_target.matrix_world @ bone.tail_local
            head_loc = armature_source.matrix_world.inverted() @ head_world
            tail_loc = armature_source.matrix_world.inverted() @ tail_world
            mat_world = armature_target.matrix_world.to_3x3() @ bone.matrix_local.to_3x3()
            roll = utils.mat3_to_vec_roll(
                armature_source.matrix_world.inverted().to_3x3() @ mat_world
            )
            bone_transforms[item.bone_name_target] = (head_loc, tail_loc, roll)

        bpy.ops.object.select_all(action="DESELECT")
        utils.set_active(armature_source)
        bpy.ops.object.mode_set(mode="EDIT")

        # Recreate target bones inside source armature
        for item in self.retarget_bone_list:
            bone_source = armature_source.data.edit_bones.get(item.bone_name_source)
            bone_new = armature_source.data.edit_bones.new(item.bone_name_target + RETARGET_ID)
            bone_new.head, bone_new.tail, bone_new.roll = bone_transforms[item.bone_name_target]
            if bone_source:
                bone_new.parent = bone_source
            else:
                bone_new.parent = None

        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")

        # Constraints inside duplicate helper for bones whose source is the root object
        for item in self.retarget_bone_list:
            if item.bone_name_source == armature_source_original.name:
                pb = armature_source.pose.bones.get(item.bone_name_target + RETARGET_ID)
                if pb:
                    c = pb.constraints.new("COPY_ROTATION")
                    c.target = armature_source_original

        # Add constraints to target armature
        for item in self.retarget_bone_list:
            bone_target = armature_target.pose.bones.get(item.bone_name_target)

            constraint = bone_target.constraints.new("COPY_ROTATION")
            constraint.name += RETARGET_ID
            constraint.target = armature_source
            constraint.subtarget = item.bone_name_target + RETARGET_ID

            if bone_target.name in root_bones:
                constraint = bone_target.constraints.new("COPY_LOCATION")
                constraint.name += RETARGET_ID
                constraint.target = armature_source
                constraint.subtarget = item.bone_name_target + RETARGET_ID

            _select_pose_bone(armature_target, item.bone_name_target, True)

        # Bake animation
        self.bake_animation(armature_source, armature_target, root_bones)

        # Delete duplicate helper armature
        bpy.ops.object.select_all(action="DESELECT")
        utils.set_active(armature_source)
        if armature_source.animation_data and armature_source.animation_data.action:
            bpy.data.actions.remove(armature_source.animation_data.action)
        bpy.ops.object.delete()

        armature_source = armature_source_original

        # Rename action
        if armature_target.animation_data and armature_target.animation_data.action:
            src_action_name = (
                armature_source.animation_data.action.name
                if (armature_source.animation_data and armature_source.animation_data.action)
                else "Motive"
            )
            armature_target.animation_data.action.name = src_action_name + " Retarget"

        # Remove constraints from target armature
        for bone in armature_target.pose.bones:
            for constraint in list(bone.constraints):
                if RETARGET_ID in constraint.name:
                    bone.constraints.remove(constraint)

        bpy.ops.object.select_all(action="DESELECT")
        utils.set_active(armature_target)

        armature_target.rotation_mode = rotation_mode

        if source_scale:
            armature_source.scale = source_scale

        bpy.ops.object.select_all(action="DESELECT")
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
