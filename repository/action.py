import re
from enum import IntEnum
from typing import Protocol

import bpy
from bpy.types import (
    Action,
    FCurve,
    Object,
    PoseBone,
)

if bpy.app.version >= (5, 0, 0):
    from bpy.types import ActionChannelbagFCurves as ActionFCurves
else:
    from bpy.types import ActionFCurves


class FCurveIndexEnum(IntEnum):
    QUATERNION_ROTATION_W = 0
    QUATERNION_ROTATION_X = 1
    QUATERNION_ROTATION_Y = 2
    QUATERNION_ROTATION_Z = 3

    LOCATION_X = 4
    LOCATION_Y = 5
    LOCATION_Z = 6

    def get_keyframe_value(self, pose_bone: PoseBone) -> float:
        if self >= 4:
            value = pose_bone.location[self - 4]
        else:
            value = pose_bone.rotation_quaternion[self]
        return value


class ActionRepositoryProtocol(Protocol):
    take_idx: int

    fcurves: dict[Object, dict[PoseBone, dict[FCurveIndexEnum, FCurve]]] = {}

    @classmethod
    def get_action_name(cls) -> str:
        raise NotImplementedError()

    @classmethod
    def set_take_idx(cls) -> bool:
        raise NotImplementedError()

    @classmethod
    def cache_fcurves(cls, objects: list[Object], clear_existing: bool = True):
        raise NotImplementedError()

    @classmethod
    def assign_action(cls, object: Object):
        raise NotImplementedError()

    @classmethod
    def get_fcurves(cls, object: Object) -> ActionFCurves:
        raise NotImplementedError()

    @classmethod
    def keyframe_insert(
        cls,
        object: Object,
        frame_num: int,
    ):
        raise NotImplementedError()


class ActionRepositoryBase(ActionRepositoryProtocol):
    @classmethod
    def get_action_name(cls) -> str:
        return f"Take{cls.take_idx}"

    @classmethod
    def set_take_idx(cls) -> bool:
        cur_action = bpy.data.actions.get(cls.get_action_name())
        if cur_action is None:
            return False

        actions = list(bpy.data.actions)
        filtered_action_idxes = [
            int(action.name[4:])
            for action in actions
            if re.match(r"^Take[0-9]+$", action.name) is not None
        ]

        take_idx = 1 if not filtered_action_idxes else max(filtered_action_idxes) + 1
        cls.take_idx = take_idx
        return True

    @classmethod
    def cache_fcurves(cls, objects: list[Object], clear_existing: bool = True):
        if clear_existing:
            cls.fcurves = {}
        else:
            # Purge any removed object keys
            for existing_obj in list(cls.fcurves.keys()):
                try:
                    _ = existing_obj.name
                except ReferenceError:
                    del cls.fcurves[existing_obj]

        for object in objects:
            try:
                if object is None or object.name not in bpy.data.objects:
                    continue
                if object.type != "ARMATURE" or object.pose is None:
                    continue
            except ReferenceError:
                continue

            cls.assign_action(object)

            fcurves = cls.get_fcurves(object=object)

            assigned_hip = getattr(object, "motive_actor_hip", "")

            pose_bone_to_fcurves: dict[PoseBone, dict[FCurveIndexEnum, FCurve]] = {}
            for pose_bone in object.pose.bones:
                pose_bone_fcurves: dict[FCurveIndexEnum, FCurve] = {}

                # Determine if this bone should record location:
                # 1. Matches the assigned hip bone (custom/Mixamo rigs)
                # 2. Native Motive rig: child of root bone (bone 1)
                # 3. Fallback: bone with no parent (root bone)
                is_loc_bone = False
                if assigned_hip:
                    is_loc_bone = (pose_bone.name == assigned_hip)
                else:
                    is_loc_bone = (pose_bone.parent is None) or (
                        pose_bone.parent is not None and pose_bone.parent.parent is None
                    )

                if is_loc_bone:
                    data_path = f'pose.bones["{pose_bone.name}"].location'
                    for fcurve_index_enum in [
                        FCurveIndexEnum.LOCATION_X,
                        FCurveIndexEnum.LOCATION_Y,
                        FCurveIndexEnum.LOCATION_Z,
                    ]:
                        index = fcurve_index_enum - FCurveIndexEnum.LOCATION_X
                        fcurve = fcurves.find(
                            data_path=data_path,
                            index=index,
                        )
                        if fcurve is None:
                            fcurve = fcurves.new(
                                data_path=data_path,
                                index=index,
                            )
                        pose_bone_fcurves[fcurve_index_enum] = fcurve

                # Rotation quaternion for all animated bones (including root/hips)
                data_path = f'pose.bones["{pose_bone.name}"].rotation_quaternion'
                for fcurve_index_enum in [
                    FCurveIndexEnum.QUATERNION_ROTATION_W,
                    FCurveIndexEnum.QUATERNION_ROTATION_X,
                    FCurveIndexEnum.QUATERNION_ROTATION_Y,
                    FCurveIndexEnum.QUATERNION_ROTATION_Z,
                ]:
                    index = fcurve_index_enum
                    fcurve = fcurves.find(
                        data_path=data_path,
                        index=index,
                    )
                    if fcurve is None:
                        fcurve = fcurves.new(
                            data_path=data_path,
                            index=index,
                        )
                    pose_bone_fcurves[fcurve_index_enum] = fcurve

                pose_bone_to_fcurves[pose_bone] = pose_bone_fcurves
            cls.fcurves[object] = pose_bone_to_fcurves

    @classmethod
    def keyframe_insert(
        cls,
        object: Object,
        frame_num: int,
    ):
        try:
            if object is None or object.name not in bpy.data.objects:
                return
        except ReferenceError:
            return

        if object not in cls.fcurves:
            cls.cache_fcurves([object], clear_existing=False)
        pose_bone_to_fcurves = cls.fcurves.get(object)
        if not pose_bone_to_fcurves:
            return

        try:
            for pose_bone, fcurves in pose_bone_to_fcurves.items():
                for fcurve_index, fcurve in fcurves.items():
                    value = fcurve_index.get_keyframe_value(pose_bone)
                    fcurve.keyframe_points.insert(
                        frame=frame_num,
                        value=value,
                        options={"FAST"},
                    )
        except ReferenceError:
            # Re-cache if FCurve or PoseBone pointers were invalidated
            cls.cache_fcurves([object], clear_existing=False)
            pose_bone_to_fcurves = cls.fcurves.get(object, {})
            for pose_bone, fcurves in pose_bone_to_fcurves.items():
                for fcurve_index, fcurve in fcurves.items():
                    value = fcurve_index.get_keyframe_value(pose_bone)
                    fcurve.keyframe_points.insert(
                        frame=frame_num,
                        value=value,
                        options={"FAST"},
                    )


class ActionRepositoryWithSlot(ActionRepositoryBase):
    take_idx: int = 1

    fcurves: dict[Object, dict[PoseBone, dict[FCurveIndexEnum, FCurve]]] = {}

    @classmethod
    def get_fcurves(cls, object: Object) -> ActionFCurves:
        action = object.animation_data.action
        action_slot = object.animation_data.action_slot

        if not action.layers:
            action.layers.new("Layer")
        layer = action.layers[0]

        if not layer.strips:
            layer.strips.new(type="KEYFRAME")
        strip = layer.strips[0]

        channelbag = strip.channelbag(action_slot, ensure=True)

        return channelbag.fcurves

    @classmethod
    def get_action(cls, action_name: str) -> Action:
        action = bpy.data.actions.get(action_name)
        if not action:
            action = bpy.data.actions.new(name=action_name)
            action.use_fake_user = True
        return action

    @classmethod
    def assign_action(cls, object: Object):
        action_name = cls.get_action_name()
        if not object.animation_data:
            object.animation_data_create()
        action = cls.get_action(action_name=action_name)
        if object.animation_data.action != action:
            object.animation_data.action = action

        expected_slot_id = f"OB{object.name}"
        current_slot = object.animation_data.action_slot
        if (
            current_slot is None
            or getattr(current_slot, "identifier", "") != expected_slot_id
        ):
            action_slot = (
                action.slots.get(expected_slot_id)
                or action.slots.get(object.name)
            )
            if action_slot is None:
                action_slot = action.slots.new(
                    id_type="OBJECT", name=object.name
                )
            object.animation_data.action_slot = action_slot

        if not action.layers:
            action.layers.new("Layer")
        layer = action.layers[0]
        if not layer.strips:
            layer.strips.new(type="KEYFRAME")
        strip = layer.strips[0]
        if object.animation_data.action_slot is not None:
            strip.channelbag(object.animation_data.action_slot, ensure=True)


class ActionRepositoryWithoutSlot(ActionRepositoryBase):
    take_idx: int = 1

    fcurves: dict[Object, dict[PoseBone, dict[FCurveIndexEnum, FCurve]]] = {}

    @classmethod
    def get_fcurves(cls, object: Object) -> ActionFCurves:
        return object.animation_data.action.fcurves

    @classmethod
    def assign_action(cls, object: Object):
        action_name = f"{object.name}.{ActionRepository.get_action_name()}"
        if not object.animation_data:
            object.animation_data_create()
        if (
            not object.animation_data.action
            or object.animation_data.action.name != action_name
        ):
            action = bpy.data.actions.get(action_name)
            if action is None:
                action = bpy.data.actions.new(action_name)
                action.use_fake_user = True
            object.animation_data.action = action


if bpy.app.version >= (4, 4, 0):
    # ActionSlot is available after blender v4.4.0
    ActionRepository: ActionRepositoryProtocol = ActionRepositoryWithSlot
else:
    ActionRepository: ActionRepositoryProtocol = ActionRepositoryWithoutSlot
