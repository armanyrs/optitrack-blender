import bpy
from bpy.app.handlers import persistent

from .plugin_operators import ConnectOperator
from .property_definitions import CustomObjectProperties


@persistent
def object_handler(scene):
    if bpy.context.window_manager.connection_status == True:
        existing_conn = ConnectOperator.connection_setup
        deleted_ids = []
        for key in existing_conn.rev_assets_blender:
            if (
                str(existing_conn.rev_assets_blender[key]["obj"])
                == "<bpy_struct, Object invalid>"
            ):
                deleted_ids.append(
                    (
                        key,
                        existing_conn.rev_assets_blender[key]["m_ID"],
                        existing_conn.rev_assets_blender[key]["asset_type"],
                    )
                )

        if deleted_ids:
            for id in deleted_ids:
                if id[1] == None:
                    del existing_conn.rev_assets_blender[id[0]]
                else:
                    if id[2] in existing_conn.assets_blender:
                        if id[1] in existing_conn.assets_blender[id[2]]:
                            del existing_conn.assets_blender[id[2]][id[1]]
                    del existing_conn.rev_assets_blender[id[0]]
                print("Object deleted, ID: ", id)

        for obj in bpy.data.objects:
            uid = obj.id_data.session_uid
            if obj.type in ["MESH", "EMPTY", "CAMERA"]:
                asset_type = "rigid_body"
            elif obj.type == "ARMATURE":
                asset_type = "skeleton"
            else:
                asset_type = obj.type

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
            obj.obj_prop.obj_name = obj.name


@persistent
def model_change_handler(scene):
    """Handles Motive tracked model changes without deleting user or NPR custom properties."""
    if ConnectOperator.connection_setup is not None:
        existing_connection = ConnectOperator.connection_setup
        if existing_connection.streaming_client is not None:
            if existing_connection.indicate_model_changed == True:
                bpy.context.window_manager.connection_status = False
                bpy.context.window_manager.start_status = False
                existing_connection.streaming_client.shutdown()
                existing_connection.reset_to_initial()


@persistent
def load_handler(dummy):
    """Shuts down active NatNet sockets cleanly when a new blend file is loaded."""
    if ConnectOperator.connection_setup is not None:
        existing_connection = ConnectOperator.connection_setup
        if existing_connection.streaming_client is not None:
            existing_connection.streaming_client.shutdown()
            existing_connection.reset_to_initial()
