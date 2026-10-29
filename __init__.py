# Edit -> Preferences -> Add-ons
bl_info = {
    "name": "Motive Blender Plugin",
    "author": "OptiTrack",
    "version": (1, 2, 0),
    "blender": (4, 4, 0),
    "location": "View3D > Toolbar > Motive",
    "description": "Stream Motive asset data and retarget motion in Blender",
    "warning": "",
    "category": "OptiTrack",
}

# Import libraries
if "bpy" not in locals():
    import bpy  # Blender Python Module


# Register and unregister classes
def register():
    from . import (
        Modified_NatNetClient,
        app_handlers,
        core,
        diagnostics,
        icon_viewer,
        plugin_operators,
        plugin_panels,
        plugin_skeletons,
        property_definitions,
        repository,
        retarget_operators,
        retarget_properties,
        retarget_ui,
    )

    if "bpy" not in locals():
        pass
    else:
        import importlib

        importlib.reload(core)
        importlib.reload(plugin_panels)
        importlib.reload(plugin_operators)
        importlib.reload(plugin_skeletons)
        importlib.reload(Modified_NatNetClient)
        importlib.reload(property_definitions)
        importlib.reload(app_handlers)
        importlib.reload(icon_viewer)
        importlib.reload(retarget_properties)
        importlib.reload(retarget_operators)
        importlib.reload(retarget_ui)
        importlib.reload(diagnostics)

    global classes
    classes = [
        plugin_panels.PluginMotive,
        property_definitions.CustomSceneProperties,
        property_definitions.CustomObjectProperties,
        plugin_panels.InitialSettings,
        plugin_panels.AssignObjects,
        plugin_panels.AllocatedObjects,
        plugin_panels.AllocatedArmatureBones,
        plugin_panels.Connection,
        plugin_panels.Recorder,
        plugin_operators.ResetOperator,
        plugin_operators.ConnectOperator,
        plugin_operators.RefreshAssetsOperator,
        plugin_operators.StartOperator,
        plugin_operators.PauseOperator,
        plugin_operators.StartRecordOperator,
        plugin_operators.StopRecordOperator,
        plugin_operators.StartEndFrameOperator,
        plugin_operators.StartFrameRecordOperator,
        plugin_operators.StopFrameRecordOperator,
        plugin_operators.newActionOperator,
        plugin_skeletons.MotiveArmatureOperator,
        plugin_panels.Info,
        # Retargeting PropertyGroup
        retarget_properties.BoneListItem,
        # Retargeting UIList and Panel (3D Viewport N-Panel)
        retarget_ui.MOTIVE_UL_BoneList,
        retarget_ui.VIEW3D_PT_motive_retarget,
        # Offline Retargeting Operators (FBX/BVH Baking)
        retarget_operators.MOTIVE_OT_build_bone_list,
        retarget_operators.MOTIVE_OT_add_bone_list_item,
        retarget_operators.MOTIVE_OT_remove_bone_list_item,
        retarget_operators.MOTIVE_OT_clear_bone_list,
        retarget_operators.MOTIVE_OT_retarget_animation,
        # Realtime Retargeting Operators (Object Properties & Schemes)
        retarget_operators.MOTIVE_OT_init_tpose,
        retarget_operators.MOTIVE_OT_reset_tpose,
        retarget_operators.MOTIVE_OT_detect_actor_bones,
        retarget_operators.MOTIVE_OT_save_custom_bones,
        retarget_operators.MOTIVE_OT_save_custom_bones_retargeting,
        retarget_operators.MOTIVE_OT_import_custom_schemes,
        retarget_operators.MOTIVE_OT_export_custom_schemes,
        retarget_operators.MOTIVE_OT_clear_custom_bones,
    ]

    for cls in classes:
        bpy.utils.register_class(cls)

    retarget_properties.register_properties()
    diagnostics.register_diagnostics()
    core.detection_manager.load_detection_lists()

    icon_viewer.IconsLoader.registering_icons()
    bpy.types.Scene.init_prop = bpy.props.PointerProperty(
        type=property_definitions.CustomSceneProperties
    )
    bpy.types.Object.obj_prop = bpy.props.PointerProperty(
        type=property_definitions.CustomObjectProperties
    )

    bpy.app.handlers.depsgraph_update_post.append(app_handlers.object_handler)
    bpy.app.handlers.depsgraph_update_pre.append(app_handlers.model_change_handler)
    bpy.app.handlers.load_post.append(app_handlers.load_handler)

    repository.register()


def unregister():
    from . import app_handlers, diagnostics, icon_viewer, repository, retarget_properties

    diagnostics.unregister_diagnostics()
    retarget_properties.unregister_properties()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.init_prop
    del bpy.types.Object.obj_prop
    icon_viewer.IconsLoader.unregistering_icons()

    if app_handlers.object_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(app_handlers.object_handler)
    if app_handlers.model_change_handler in bpy.app.handlers.depsgraph_update_pre:
        bpy.app.handlers.depsgraph_update_pre.remove(app_handlers.model_change_handler)

    bpy.app.handlers.load_post.remove(app_handlers.load_handler)

    repository.unregister()


if __name__ == "__main__":
    register()
