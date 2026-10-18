import bpy
from bpy.types import Panel, UIList

from .icon_viewer import IconsLoader
from .retarget_properties import get_source_armature, get_target_armature


class MOTIVE_UL_BoneList(UIList):
    """UIList for Retargeting bone pairs (Source -> Target)."""

    bl_idname = "MOTIVE_UL_BoneList"

    def draw_item(
        self, context, layout, data, item, icon, active_data, active_propname, index
    ):
        armature_target = get_target_armature()
        armature_source = get_source_armature()

        layout = layout.split(factor=0.45, align=True)

        # Displays source bone
        if item.is_custom and armature_source and armature_source.type == "ARMATURE":
            layout.prop_search(item, "bone_name_source", armature_source.pose, "bones", text="")
        else:
            layout.label(text=item.bone_name_source, icon="BONE_DATA")

        # Displays target bone
        if armature_target and armature_target.type == "ARMATURE":
            layout.prop_search(item, "bone_name_target", armature_target.pose, "bones", text="")
        else:
            layout.prop(item, "bone_name_target", text="")


class VIEW3D_PT_motive_retarget(Panel):
    """N-Panel for offline FBX/BVH animation retargeting."""

    bl_idname = "VIEW3D_PT_motive_retarget"
    bl_label = "Retargeting"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Motive"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False

        row = layout.row(align=True)
        row.label(text="Select the armatures:")

        row = layout.row(align=True)
        row.prop(context.scene, "motive_retargeting_armature_source", icon="ARMATURE_DATA")

        row = layout.row(align=True)
        row.prop(context.scene, "motive_retargeting_armature_target", icon="ARMATURE_DATA")

        # Check if an animated armature exists in scene
        anim_exists = False
        for obj in bpy.data.objects:
            if obj.animation_data and obj.animation_data.action:
                anim_exists = True
                break

        if not anim_exists:
            row = layout.row(align=True)
            row.label(text="No animated armature found!", icon="INFO")
            return

        if (
            not context.scene.motive_retargeting_armature_source
            or not context.scene.motive_retargeting_armature_target
        ):
            self.draw_import_export(layout)
            return

        if not context.scene.motive_retargeting_bone_list:
            row = layout.row(align=True)
            row.scale_y = 1.2
            row.operator("motive.build_bone_list", text="Build Bone List", icon="AUTO")
            self.draw_import_export(layout)
            return

        subrow = layout.row(align=True)
        row = subrow.row(align=True)
        row.scale_y = 1.2
        row.operator("motive.build_bone_list", text="Rebuild Bone List", icon="FILE_REFRESH")
        row = subrow.row(align=True)
        row.scale_y = 1.2
        row.alignment = "RIGHT"
        row.operator("motive.clear_bone_list", text="", icon="X")

        layout.separator()

        row = layout.row(align=True)
        row.template_list(
            "MOTIVE_UL_BoneList",
            "Bone List",
            context.scene,
            "motive_retargeting_bone_list",
            context.scene,
            "motive_retargeting_bone_list_index",
            rows=6,
            maxrows=12,
        )

        row = layout.row(align=True)
        row.operator("motive.add_bone_list_item", text="Add Custom Entry", icon="ADD")
        row.operator("motive.remove_bone_list_item", text="Remove Selected", icon="REMOVE")

        row = layout.row(align=True)
        row.prop(context.scene, "motive_retargeting_auto_scaling")

        row = layout.row(align=True)
        row.label(text="Use Pose:")
        row.prop(context.scene, "motive_retargeting_use_pose", expand=True)

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator(
            "motive.retarget_animation",
            text="Retarget Animation",
            icon_value=IconsLoader.get_icon("Motive") if hasattr(IconsLoader, "get_icon") else 0,
        )

        self.draw_import_export(layout)

    def draw_import_export(self, layout):
        layout.separator()

        row = layout.row(align=True)
        row.label(text="Custom Naming Schemes:")

        subrow = layout.row(align=True)
        row = subrow.row(align=True)
        row.scale_y = 0.9
        row.operator("motive.save_custom_bones_retargeting", text="Save")
        row.operator("motive.import_custom_schemes", text="Import")
        row.operator("motive.export_custom_schemes", text="Export")
        row = subrow.row(align=True)
        row.scale_y = 0.9
        row.alignment = "RIGHT"
        row.operator("motive.clear_custom_bones", text="", icon="X")
