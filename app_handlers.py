import bpy
from bpy.app.handlers import persistent

from .plugin_operators import ConnectOperator
from .property_definitions import CustomObjectProperties
from .repository.skeleton import SkeletonRepository


@persistent
def object_handler(scene):
    SkeletonRepository.purge_invalid_objects()
    if bpy.context.window_manager.connection_status == True:
        existing_conn = ConnectOperator.connection_setup
        # print("inside handler: ", existing_conn.assets_motive)
        # if bpy.context.window_manager.operators:
        #     last_operator = bpy.context.window_manager.operators[-1].bl_idname
        #     if last_operator == "OBJECT_OT_delete" or last_operator == "OUTLINER_OT_delete": # check if any object is deleted with the scene update
        deleted_ids = []
        for key in list(existing_conn.rev_assets_blender.keys()):
            obj_ref = existing_conn.rev_assets_blender[key].get("obj")
            is_dead = False
            try:
                if obj_ref is None or str(obj_ref) == "<bpy_struct, Object invalid>":
                    is_dead = True
                else:
                    _ = obj_ref.name
            except ReferenceError:
                is_dead = True

            if is_dead:
                deleted_ids.append(
                    (
                        key,
                        existing_conn.rev_assets_blender[key].get("m_ID"),
                        existing_conn.rev_assets_blender[key].get("asset_type"),
                    )
                )

        if deleted_ids:
<<<<<<< Updated upstream
            for (
                id
            ) in deleted_ids:  # if object deleted, update the dictionaries accordingly
                if id[1] == None:
                    del existing_conn.rev_assets_blender[id[0]]
=======
            for id_tuple in deleted_ids:
                if id_tuple[1] is None or id_tuple[1] == "None":
                    existing_conn.rev_assets_blender.pop(id_tuple[0], None)
>>>>>>> Stashed changes
                else:
                    if id_tuple[2] in existing_conn.assets_blender:
                        existing_conn.assets_blender[id_tuple[2]].pop(id_tuple[1], None)
                    existing_conn.rev_assets_blender.pop(id_tuple[0], None)
                print("Object deleted, ID: ", id_tuple)

        for (
            obj
        ) in bpy.data.objects:  # update the dictionary every time the scene updates
            uid = obj.id_data.session_uid  # session-wide identifier for the data block
            if obj.type in ["MESH", "EMPTY", "CAMERA"]:
                asset_type = "rigid_body"
            elif obj.type == "ARMATURE":
                asset_type = "skeleton"
            else:
                asset_type = obj.type
            # print("inside object_handler: ", existing_connection.assets_motive)
            # print("asset: ", obj.name, " ", obj.type, " ", asset)
            if uid not in existing_conn.rev_assets_blender:
                existing_conn.rev_assets_blender[uid] = {
                    "obj": obj,
                    "m_ID": "None",
                    "asset_type": asset_type,
                }
            else:
                existing_conn.rev_assets_blender[uid]["obj"] = obj
            if not hasattr(obj, "obj_prop"):
                obj.obj_prop = bpy.props.PointerProperty(type=CustomObjectProperties)
<<<<<<< Updated upstream
            obj.obj_prop.obj_name = (
                obj.name
            )  # assign object's name as one of the custom properties


@persistent
def model_change_handler(
    scene,
):  # if Motive's .tak is changed with already established connection in Blender
=======
            if obj.obj_prop.obj_name != obj.name:
                obj.obj_prop.obj_name = obj.name


@persistent
def model_change_handler(scene):
    """Handles Motive tracked model changes without disconnecting active streams or deleting user custom properties."""
>>>>>>> Stashed changes
    if ConnectOperator.connection_setup is not None:
        existing_connection = ConnectOperator.connection_setup
        if existing_connection.streaming_client is not None:
            if existing_connection.indicate_model_changed == True:
<<<<<<< Updated upstream
                print("True")
                bpy.context.window_manager.connection_status = False
                existing_connection.streaming_client.shutdown()
                existing_connection.reset_to_initial()
                existing_connection = None

                for attr in dir(bpy.data):
                    if "bpy_prop_collection" in str(type(getattr(bpy.data, attr))):
                        for obj in getattr(bpy.data, attr):
                            for custom_prop_name in list(obj.keys()):
                                del obj[custom_prop_name]

                # Deselect all objects
                bpy.ops.object.select_all(action="DESELECT")

                # Delete all custom properties
                scene = bpy.context.scene
                # Iterate over all custom properties in the scene
                for prop_name in list(scene.keys()):
                    if scene[prop_name].is_property:
                        del scene[prop_name]
=======
                existing_connection.indicate_model_changed = False
                try:
                    existing_connection.streaming_client.send_modeldef_command()
                except Exception:
                    pass
>>>>>>> Stashed changes


@persistent
def load_handler(dummy):  # every time a new file is loaded or created in Blender
    print("handler loaded")
    if ConnectOperator.connection_setup is not None:
        existing_connection = ConnectOperator.connection_setup
        if existing_connection.streaming_client is not None:
            existing_connection.streaming_client.shutdown()
            existing_connection.reset_to_initial()

            for attr in dir(bpy.data):
                if "bpy_prop_collection" in str(type(getattr(bpy.data, attr))):
                    for obj in getattr(bpy.data, attr):
                        for custom_prop_name in list(obj.keys()):
                            del obj[custom_prop_name]

            bpy.ops.object.select_all(action="DESELECT")
