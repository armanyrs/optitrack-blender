from dataclasses import dataclass
from typing import Any, Optional

import bpy
from bpy.types import EditBone, Object
from mathutils import Matrix, Quaternion, Vector

try:
    from ..core import animation_lists
except (ImportError, ValueError):
    try:
        from core import animation_lists
    except (ImportError, ValueError):
        import importlib
        animation_lists = importlib.import_module("optitrack-blender.core.animation_lists")

from .action import ActionRepository


@dataclass
class FrameData:
    location: Vector = Vector()
    quaternion_rotation: Quaternion = Quaternion()


@dataclass
class BoneData:
    bone_id: int
    bone_name: str
    t_pose_head: Vector
    parent: Optional["BoneData"] = None

    child: Optional["BoneData"] = None

    frame_pos: Optional[Vector] = None
    frame_rot: Optional[Quaternion] = None

    _blender_frame_rot: Optional[Quaternion] = None

    transform_matrix: Optional[Matrix] = None

    def __post_init__(self):
        if self.parent is None:
            self.t_pose_head = Vector()
        if self.parent and self.parent.child is None:
            self.parent.child = self

    def set_frame_pos(self, frame_pos: Vector):
        self.frame_pos = frame_pos
        self._blender_frame_pos = None

    def set_frame_rot(self, frame_rot: Quaternion):
        self.frame_rot = frame_rot
        self._blender_frame_rot = None

    def get_blender_frame_rot(self, frame_rot: Quaternion) -> Quaternion:
        return self.to_blender_rot(frame_rot)

    def get_global_pos(self) -> Vector:
        global_pos = self.t_pose_head
        if self.parent is not None:
            global_pos = global_pos + self.parent.get_global_pos()
        return global_pos

    def get_blender_global_pos(self) -> Vector:
        return self.to_blender_pos(self.get_global_pos())

    def to_blender_pos(self, pos: Vector) -> Vector:
        return Vector((pos.x, -pos.z, pos.y))

    def to_blender_rot(self, rot: Quaternion) -> Quaternion:
        convert3 = self.transform_matrix
        if convert3 is None:
            convert3 = Matrix(((1, 0, 0), (0, 0, 1), (0, -1, 0)))
        return (convert3 @ rot.to_matrix() @ convert3.inverted()).to_quaternion()

    def to_motive_pos(self, pos: Vector) -> Vector:
        return Vector((pos.x, pos.z, -pos.y))

    def set_transform_matrix(self, edit_bone: EditBone):
        self.transform_matrix = (
            Matrix(
                (
                    (1, 0, 0),
                    (0, 0, 1),
                    (0, -1, 0),
                )
            )
            @ edit_bone.matrix.to_3x3()
        )
        self.transform_matrix.transpose()


@dataclass
class SkeletonData:
    skeleton_id: int
    skeleton_name: str
    bones: dict[int, BoneData]

    @classmethod
    def create_skeleton(
        cls, skeleton_id: int, skeleton_name: str, bones: dict[int, BoneData]
    ) -> "SkeletonData":
        new_skelenton_data = cls(
            skeleton_id=skeleton_id,
            skeleton_name=skeleton_name,
            bones=bones,
        )
        return new_skelenton_data

    def append_bone(self, bone: BoneData):
        self.bones[bone.bone_id] = bone

    def get_bone_by_id(self, bone_id: int) -> BoneData:
        return self.bones[bone_id]

    def create_armature(
        self,
        is_create_armature: bool = True,
    ) -> bool:
        prev_active = bpy.context.view_layer.objects.active
        armature = None
        armature_object = None
        try:
            if prev_active is not None and prev_active.mode != "OBJECT":
                try:
                    bpy.ops.object.mode_set(mode="OBJECT")
                except Exception:
                    pass

            armature = bpy.data.armatures.new(self.skeleton_name)
            armature_object = bpy.data.objects.new(self.skeleton_name, armature)
            bpy.context.collection.objects.link(armature_object)
            bpy.context.view_layer.objects.active = armature_object

            def get_bone_direction(head: Vector) -> Vector:
                root_child = self.bones[0].child if (0 in self.bones and self.bones[0].child) else None
                heap_head = root_child.get_blender_global_pos() if root_child else Vector()
                offset = head - heap_head

                if offset.z < -0.2:
                    # Foot
                    direction = Vector((0, -1, 0))
                elif offset.x > 0.2:
                    # Left Hand
                    direction = Vector((1, 0, 0))
                elif offset.x < -0.2:
                    # Right Hand
                    direction = Vector((-1, 0, 0))
                else:
                    # Head
                    direction = Vector((0, 0, 1))
                return direction

            bpy.ops.object.mode_set(mode="EDIT")
            for bone in self.bones.values():
                edit_bone = armature_object.data.edit_bones.new(bone.bone_name)

                if bone.parent is not None:
                    edit_bone_parent = armature_object.data.edit_bones.get(
                        bone.parent.bone_name
                    )
                    edit_bone.parent = edit_bone_parent

                    if bone.parent.child is bone and edit_bone_parent is not None:
                        edit_bone_parent.tail = bone.get_blender_global_pos()
                        bone.parent.set_transform_matrix(edit_bone=edit_bone_parent)

                edit_bone.roll = 0
                edit_bone.head = (
                    bone.get_blender_global_pos()
                    if bone.parent is not None
                    else Vector()
                )

                if bone.child is None:
                    direction = get_bone_direction(edit_bone.head)

                    bone_length = edit_bone.parent.length if edit_bone.parent else 0.1

                    edit_bone.tail = direction * bone_length + edit_bone.head

                    bone.set_transform_matrix(edit_bone=edit_bone)

                edit_bone.use_local_location = True
                edit_bone.use_inherit_rotation = True
        except Exception:
            raise
        else:
            if is_create_armature and armature_object is not None:
                SkeletonRepository.render_object_to_skeleton[armature_object] = self
                try:
                    if hasattr(armature_object, "obj_prop"):
                        armature_object.obj_prop.obj_name = armature_object.name
                        armature_object.obj_prop.skeletons = str(self.skeleton_id)
                except Exception:
                    pass
            return True
        finally:
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:
                pass

            if not is_create_armature:
                # Used to initialize bone transform_matrix without keeping a scene object
                if armature_object is not None:
                    bpy.data.objects.remove(armature_object, do_unlink=True)
                if armature is not None:
                    bpy.data.armatures.remove(armature, do_unlink=True)
                if prev_active is not None:
                    try:
                        if prev_active.name in bpy.data.objects:
                            bpy.context.view_layer.objects.active = prev_active
                    except ReferenceError:
                        pass

    def create_frame_data(
        self,
        data: dict[int, dict[str, Any]],
    ) -> dict[str, FrameData]:
        frames = {}
        if not data:
            return frames

        min_key = min(data.keys())
        bones_list = list(self.bones.values())
        for idx, bone_id in enumerate(data):
            pos = Vector(data[bone_id]["pos"])
            x, y, z, w = data[bone_id]["rot"]
            rot = Quaternion((w, x, y, z))

            frame_data = FrameData(location=pos, quaternion_rotation=rot)

            bone_data = None
            if bone_id in self.bones:
                bone_data = self.bones[bone_id]
            elif (bone_id - min_key + 1) in self.bones:
                bone_data = self.bones[bone_id - min_key + 1]
            elif (bone_id - min_key) in self.bones:
                bone_data = self.bones[bone_id - min_key]
            elif idx < len(bones_list):
                bone_data = bones_list[idx]

            if bone_data:
                frames[bone_data.bone_name] = frame_data
        return frames

    def render_skeleton_and_insert_keyframe(
        self,
        object: Object,
        frame_data: dict[str, FrameData],
        keyframe_num: Optional[int] = None,
    ):
        try:
            if object is None or object.name not in bpy.data.objects:
                SkeletonRepository.remove_render_object(object)
                return

            frames = frame_data

            # Check if this object has retargeting mapped bones
            custom_data = object.get("CUSTOM", {})
            tpose_bones = (
                custom_data.get("motive_tpose_bones")
                or custom_data.get("rsl_tpose_bones")
                or {}
            )

            has_mapped_bones = any(
                bool(getattr(object, "motive_actor_" + b, None))
                for b in animation_lists.get_bones().keys()
            )

            if has_mapped_bones:
                # Realtime live retargeting pathway for character rigs (Mixamo, CC4, custom rigs)
                mat_motive_to_blender = Matrix(((1, 0, 0), (0, 0, 1), (0, -1, 0)))

                # Determine Motive rest hip height from skeleton definition or default (0.86m)
                motive_rest_y = 0.86
                if self.bones:
                    if 1 in self.bones:
                        motive_rest_y = self.bones[1].get_global_pos().y
                    elif 0 in self.bones and self.bones[0].child:
                        motive_rest_y = self.bones[0].child.get_global_pos().y

                for canonical_key in animation_lists.get_bones().keys():
                    assigned_name = getattr(object, "motive_actor_" + canonical_key, "")
                    if not assigned_name:
                        continue
                    pose_bone = object.pose.bones.get(assigned_name)
                    if not pose_bone:
                        continue

                    frame_entry = animation_lists.find_frame_data_for_canonical_bone(
                        canonical_key, frames, skeleton_name=self.skeleton_name
                    )
                    if not frame_entry:
                        continue

                    # Ensure bone uses inherit rotation so the skeletal FK hierarchy functions naturally
                    data_bone = object.data.bones.get(assigned_name)
                    if data_bone and not data_bone.use_inherit_rotation:
                        data_bone.use_inherit_rotation = True

                    # Compute parent-relative rotation in Motive space
                    parent_key = animation_lists.CANONICAL_PARENT.get(canonical_key)
                    q_child = frame_entry.quaternion_rotation

                    # Climb to find active parent in this frame
                    q_parent = None
                    curr_parent = parent_key
                    while curr_parent:
                        parent_entry = animation_lists.find_frame_data_for_canonical_bone(
                            curr_parent, frames, skeleton_name=self.skeleton_name
                        )
                        if parent_entry:
                            q_parent = parent_entry.quaternion_rotation
                            break
                        curr_parent = animation_lists.CANONICAL_PARENT.get(curr_parent)

                    if q_parent is not None:
                        q_local_mot = q_parent.inverted() @ q_child
                    else:
                        q_local_mot = q_child

                    # Transform Motive parent-relative rotation into Blender bone local space
                    convert3 = (mat_motive_to_blender @ pose_bone.bone.matrix_local.to_3x3()).transposed()
                    b_local_rot = (convert3 @ q_local_mot.to_matrix() @ convert3.inverted()).to_quaternion()

                    # Apply on top of character's saved T-Pose offset (if any)
                    bone_tpose_data = tpose_bones.get(assigned_name)
                    if bone_tpose_data and "rotation_local" in bone_tpose_data:
                        saved_local_rot = Quaternion(bone_tpose_data["rotation_local"])
                        final_rot = b_local_rot @ saved_local_rot
                    else:
                        final_rot = b_local_rot

                    pose_bone.rotation_mode = "QUATERNION"
                    pose_bone.rotation_quaternion = final_rot

                    # Root hip translation
                    if canonical_key == "hip":
                        p = frame_entry.location
                        delta_world = Vector((p.x, -p.z, p.y - motive_rest_y))
                        delta_armature = object.matrix_world.to_3x3().inverted() @ delta_world
                        delta_bone_local = pose_bone.bone.matrix_local.to_3x3().inverted() @ delta_armature

                        tpose_loc = Vector(bone_tpose_data.get("location_local", (0, 0, 0))) if bone_tpose_data else Vector((0, 0, 0))
                        pose_bone.location = tpose_loc + delta_bone_local

            else:
                # Native Motive armature pathway
                if self.bones and 0 in self.bones and self.bones[0].transform_matrix is None:
                    try:
                        self.create_armature(is_create_armature=False)
                    except Exception:
                        pass

                for bone in self.bones.values():
                    if bone.bone_name not in object.pose.bones:
                        continue
                    pose_bone = object.pose.bones[bone.bone_name]

                    if pose_bone.parent is None:
                        # Root
                        child_bone = bone.child
                        if child_bone and child_bone.bone_name in object.pose.bones:
                            child_pose_bone = object.pose.bones[child_bone.bone_name]
                            if child_bone.bone_name in frames:
                                frame_pos = frames[child_bone.bone_name].location.copy()
                                frame_pos.y -= child_bone.get_global_pos().y
                                if child_bone.transform_matrix:
                                    child_pose_bone.location = child_bone.transform_matrix @ frame_pos
                                else:
                                    child_pose_bone.location = frame_pos
                    else:
                        if bone.bone_name in frames:
                            frame_rot = frames[bone.bone_name].quaternion_rotation
                            pose_bone.rotation_mode = "QUATERNION"
                            pose_bone.rotation_quaternion = bone.get_blender_frame_rot(
                                frame_rot=frame_rot,
                            )

            if keyframe_num is not None:
                ActionRepository.assign_action(object=object)
                ActionRepository.keyframe_insert(
                    object=object,
                    frame_num=keyframe_num,
                )

        except ReferenceError:
            SkeletonRepository.remove_render_object(object)


class SkeletonRepository:
    skeletons: dict[int, SkeletonData] = {}
    skeleton_name_to_id: dict[str, int] = {}  # skeleton_name, skeleton_id

    render_object_to_skeleton: dict[Object, Optional[SkeletonData]] = {}

    @classmethod
    def purge_invalid_objects(cls) -> None:
        for obj in list(cls.render_object_to_skeleton.keys()):
            try:
                if obj is None or obj.name not in bpy.data.objects:
                    del cls.render_object_to_skeleton[obj]
            except ReferenceError:
                del cls.render_object_to_skeleton[obj]

    @classmethod
    def sync_scene_armatures(cls) -> None:
        cls.purge_invalid_objects()
        for obj in bpy.data.objects:
            if obj.type == "ARMATURE":
                sk_prop = ""
                if hasattr(obj, "obj_prop"):
                    sk_prop = getattr(obj.obj_prop, "skeletons", "")
                if sk_prop and sk_prop not in ["None", ""]:
                    try:
                        sk_id = int(sk_prop)
                        if sk_id in cls.skeletons:
                            cls.render_object_to_skeleton[obj] = cls.skeletons[sk_id]
                    except (ValueError, TypeError):
                        pass
                elif obj not in cls.render_object_to_skeleton:
                    # Also match native Motive armatures by exact skeleton name
                    if obj.name in cls.skeleton_name_to_id:
                        sk_id = cls.skeleton_name_to_id[obj.name]
                        if sk_id in cls.skeletons:
                            cls.render_object_to_skeleton[obj] = cls.skeletons[sk_id]

    @classmethod
    def append_skeleton(cls, skeleton: SkeletonData) -> None:
        cls.skeletons[skeleton.skeleton_id] = skeleton
        cls.skeleton_name_to_id[skeleton.skeleton_name] = skeleton.skeleton_id

        try:
            cls.sync_scene_armatures()
        except Exception:
            pass

    @classmethod
    def set_transform_matrix(cls) -> None:
        for skeleton in cls.skeletons.values():
            if skeleton.bones and 0 in skeleton.bones and skeleton.bones[0].transform_matrix is None:
                try:
                    skeleton.create_armature(is_create_armature=False)
                except Exception:
                    pass

    @classmethod
    def create_armatures(
        cls,
    ) -> int:
        num_armatures = 0
        for skeleton in cls.skeletons.values():
            num_armatures += skeleton.create_armature()
        return num_armatures

    @classmethod
    def get_by_id(cls, skeleton_id) -> Optional[SkeletonData]:
        if skeleton_id in cls.skeletons:
            return cls.skeletons[skeleton_id]
        try:
            return cls.skeletons.get(int(skeleton_id))
        except (ValueError, TypeError):
            return None

    @classmethod
    def update_render_object(cls, skeleton_id: str, object: Object):
        cls.purge_invalid_objects()
        try:
            sk_id = int(skeleton_id)
        except (ValueError, TypeError):
            sk_id = -1

        if sk_id in cls.skeletons:
            cls.render_object_to_skeleton[object] = cls.skeletons[sk_id]
        elif object in cls.render_object_to_skeleton:
            del cls.render_object_to_skeleton[object]

    @classmethod
    def remove_render_object(cls, object: Object):
        if object in cls.render_object_to_skeleton:
            del cls.render_object_to_skeleton[object]

    @classmethod
    def clear(cls):
        cls.skeletons = {}
        cls.skeleton_name_to_id = {}

        for object in list(cls.render_object_to_skeleton.keys()):
            try:
                object.obj_prop.skeletons = "None"
            except Exception:
                pass

        cls.render_object_to_skeleton = {}

    @classmethod
    def render_skeletons_and_insert_keyframe(
        cls,
        target_skeleton_data: SkeletonData,
        frame_data: dict[str, FrameData],
        keyframe_num: Optional[int] = None,
    ):
        cls.purge_invalid_objects()
        rendered = False
        for object, skeleton_data in list(cls.render_object_to_skeleton.items()):
            if (
                skeleton_data is not None
                and skeleton_data.skeleton_id == target_skeleton_data.skeleton_id
            ):
                skeleton_data.render_skeleton_and_insert_keyframe(
                    object=object,
                    keyframe_num=keyframe_num,
                    frame_data=frame_data,
                )
                rendered = True

        # Fallback: if render_object_to_skeleton was not mapped yet, sync and check all scene armatures
        if not rendered:
            cls.sync_scene_armatures()
            for object, skeleton_data in list(cls.render_object_to_skeleton.items()):
                if (
                    skeleton_data is not None
                    and skeleton_data.skeleton_id == target_skeleton_data.skeleton_id
                ):
                    skeleton_data.render_skeleton_and_insert_keyframe(
                        object=object,
                        keyframe_num=keyframe_num,
                        frame_data=frame_data,
                    )

    @classmethod
    def get_render_skeletons(cls) -> list[Object]:
        cls.sync_scene_armatures()
        valid_objects = []
        for object, skeleton_data in list(cls.render_object_to_skeleton.items()):
            if skeleton_data is None:
                continue
            try:
                if object is not None and object.name in bpy.data.objects:
                    valid_objects.append(object)
            except ReferenceError:
                del cls.render_object_to_skeleton[object]
        return valid_objects
