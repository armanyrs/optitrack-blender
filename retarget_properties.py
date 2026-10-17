import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Object, PropertyGroup, Scene

from .core import animation_lists


def poll_source_armatures(self, obj):
    """Filters source objects to armatures that have an animated action."""
    return obj.type == "ARMATURE" and bool(obj.animation_data and obj.animation_data.action)


def poll_target_armatures(self, obj):
    """Filters target objects to armatures different from source."""
    return obj.type == "ARMATURE" and obj != get_source_armature()


def clear_bone_list(self, context):
    """Clears bone list when armatures are changed."""
    if hasattr(context.scene, "motive_retargeting_bone_list"):
        context.scene.motive_retargeting_bone_list.clear()


def get_source_armature():
    if hasattr(bpy.context, "scene") and hasattr(bpy.context.scene, "motive_retargeting_armature_source"):
        return bpy.context.scene.motive_retargeting_armature_source
    return None


def get_target_armature():
    if hasattr(bpy.context, "scene") and hasattr(bpy.context.scene, "motive_retargeting_armature_target"):
        return bpy.context.scene.motive_retargeting_armature_target
    return None


class BoneListItem(PropertyGroup):
    """Properties for items in the retargeting bone list UIList."""

    bone_name_source: StringProperty(
        name="Source Bone",
        description="The source bone name from the animated armature",
        default="",
    )

    bone_name_target: StringProperty(
        name="Target Bone",
        description="The target bone name on the character rig",
        default="",
    )

    bone_name_key: StringProperty(
        name="Auto Detection Key",
        description="The automatically detected canonical bone key",
        default="",
    )

    is_custom: BoolProperty(
        name="Is Custom",
        description="Determines if this field is a custom added bone entry",
        default=False,
    )


def register_properties():
    # Retargeting Scene Properties
    Scene.motive_retargeting_armature_source = PointerProperty(
        name="Source",
        description="Select the armature with the animation that you want to retarget",
        type=Object,
        poll=poll_source_armatures,
        update=clear_bone_list,
    )
    Scene.motive_retargeting_armature_target = PointerProperty(
        name="Target",
        description="Select the armature that should receive the animation",
        type=Object,
        poll=poll_target_armatures,
        update=clear_bone_list,
    )
    Scene.motive_retargeting_auto_scaling = BoolProperty(
        name="Auto Scale",
        description="Scale the source armature to fit the height of the target armature",
        default=True,
    )
    Scene.motive_retargeting_use_pose = EnumProperty(
        name="Use Pose",
        description="Select which pose of the source and target armature to use during retargeting",
        items=[
            ("REST", "Rest", "Use the rest pose during retargeting"),
            ("CURRENT", "Current", "Use the current pose during retargeting"),
        ],
        default="REST",
    )
    Scene.motive_retargeting_bone_list = CollectionProperty(
        type=BoneListItem
    )
    Scene.motive_retargeting_bone_list_index = IntProperty(
        name="Index for the retargeting bone list",
        default=0,
    )

    # Realtime Object Properties for Actor Bones
    for bone_name in animation_lists.get_bones().keys():
        setattr(
            Object,
            "motive_actor_" + bone_name,
            StringProperty(
                name=bone_name,
                description=f"Select the bone that corresponds to {bone_name}",
                default="",
            ),
        )


def unregister_properties():
    props_scene = [
        "motive_retargeting_armature_source",
        "motive_retargeting_armature_target",
        "motive_retargeting_auto_scaling",
        "motive_retargeting_use_pose",
        "motive_retargeting_bone_list",
        "motive_retargeting_bone_list_index",
    ]
    for prop in props_scene:
        if hasattr(Scene, prop):
            delattr(Scene, prop)

    for bone_name in animation_lists.get_bones().keys():
        prop = "motive_actor_" + bone_name
        if hasattr(Object, prop):
            delattr(Object, prop)
