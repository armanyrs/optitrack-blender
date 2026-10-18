import json
import os
import pathlib
import bpy

from . import detection_manager

main_dir = str(pathlib.Path(os.path.dirname(__file__)).parent.resolve())
resources_dir = os.path.join(main_dir, "resources")
custom_bones_dir = os.path.join(resources_dir, "custom_bones")
custom_bone_list_file = os.path.join(custom_bones_dir, "custom_bone_list.json")


def get_source_armature():
    if hasattr(bpy.context.scene, "motive_retargeting_armature_source"):
        return bpy.context.scene.motive_retargeting_armature_source
    return None


def get_target_armature():
    if hasattr(bpy.context.scene, "motive_retargeting_armature_target"):
        return bpy.context.scene.motive_retargeting_armature_target
    return None


def save_retargeting_to_list():
    armature_target = get_target_armature()
    if not armature_target:
        return
    retargeting_dict = detection_manager.detect_retarget_bones()

    bone_list_prop = getattr(bpy.context.scene, "motive_retargeting_bone_list", None)
    if not bone_list_prop:
        return

    for bone_item in bone_list_prop:
        if not bone_item.bone_name_source or not bone_item.bone_name_target:
            continue

        bone_name_key = bone_item.bone_name_key
        bone_name_source = bone_item.bone_name_source.lower()
        bone_name_target = bone_item.bone_name_target.lower()
        
        detected_info = retargeting_dict.get(bone_item.bone_name_source)
        if not detected_info:
            continue
        bone_name_target_detected, bone_name_key_detected = detected_info

        if bone_name_target_detected == bone_item.bone_name_target:
            continue

        if bone_name_key_detected and bone_name_key_detected != "spine":
            if not detection_manager.bone_detection_list_custom.get(bone_name_key_detected):
                detection_manager.bone_detection_list_custom[bone_name_key_detected] = []

            if bone_name_target_detected.lower() in detection_manager.bone_detection_list_custom[bone_name_key_detected]:
                if bone_name_key_detected.startswith("custom_bone_") and len(detection_manager.bone_detection_list_custom[bone_name_key_detected]) == 2:
                    detection_manager.bone_detection_list_custom.pop(bone_name_key_detected)
                else:
                    detection_manager.bone_detection_list_custom[bone_name_key_detected].remove(bone_name_target_detected.lower())

                detection_manager.bone_detection_list = detection_manager.combine_lists(
                    detection_manager.bone_detection_list_unmodified,
                    detection_manager.bone_detection_list_custom,
                )

                retargeting_dict = detection_manager.detect_retarget_bones()
                bone_name_detected_new, _ = retargeting_dict.get(bone_item.bone_name_source, ("", ""))
                if bone_name_detected_new.lower() == bone_name_target:
                    continue

            if bone_name_target not in detection_manager.bone_detection_list_custom[bone_name_key_detected]:
                detection_manager.bone_detection_list_custom[bone_name_key_detected] = [
                    bone_name_target
                ] + detection_manager.bone_detection_list_custom[bone_name_key_detected]
            continue

        detection_manager.bone_detection_list_custom["custom_bone_" + bone_name_source] = [
            bone_name_source,
            bone_name_target,
        ]

    save_to_file_and_update()


def save_live_data_bone_to_list(bone_key, bone_name, bone_name_previous=None):
    if not detection_manager.bone_detection_list_custom.get(bone_key):
        detection_manager.bone_detection_list_custom[bone_key] = []

    if bone_name_previous:
        prev_clean = bone_name_previous.lower()
        if prev_clean in [b.lower() for b in detection_manager.bone_detection_list_custom[bone_key]]:
            detection_manager.bone_detection_list_custom[bone_key] = [
                b for b in detection_manager.bone_detection_list_custom[bone_key] if b.lower() != prev_clean
            ]

    clean_name = bone_name.lower()
    if clean_name not in [b.lower() for b in detection_manager.bone_detection_list_custom[bone_key]]:
        detection_manager.bone_detection_list_custom[bone_key].insert(0, clean_name)


def save_to_file_and_update():
    save_custom_to_file()
    detection_manager.load_detection_lists()


def save_custom_to_file(file_path=custom_bone_list_file):
    new_custom_list = clean_custom_list()

    target_dir = os.path.dirname(file_path)
    if target_dir and not os.path.isdir(target_dir):
        os.makedirs(target_dir, exist_ok=True)

    with open(file_path, "w", encoding="utf8") as outfile:
        json.dump(new_custom_list, outfile, ensure_ascii=False, indent=4)


def load_custom_lists_from_file(file_path=custom_bone_list_file):
    custom_bone_list = {}
    try:
        with open(file_path, encoding="utf8") as file:
            custom_bone_list = json.load(file)
    except FileNotFoundError:
        pass
    except json.decoder.JSONDecodeError:
        print("[Motive Retarget] Custom bone list is not a valid json file!")

    has_valid_header = (
        custom_bone_list.get("motive_custom_names") is not None
        or custom_bone_list.get("rokoko_custom_names") is not None
    )
    if (
        not has_valid_header
        or custom_bone_list.get("version") is None
        or custom_bone_list.get("bones") is None
    ):
        return {}, {}

    return custom_bone_list.get("bones", {}), custom_bone_list.get("shapes", {})


def clean_custom_list():
    new_custom_list = {
        "motive_custom_names": True,
        "rokoko_custom_names": True,
        "version": 1,
        "bones": {},
        "shapes": {},
    }

    new_bone_list = {}
    for key, values in detection_manager.bone_detection_list_custom.items():
        if not values:
            continue
        cleaned = [v.lower() for v in values]
        new_bone_list[key] = cleaned

    new_custom_list["bones"] = new_bone_list
    return new_custom_list


def import_custom_list(directory, file_name):
    file_path = os.path.join(directory, file_name)
    new_custom_bone_list, _ = load_custom_lists_from_file(file_path=file_path)

    for key, bones in detection_manager.bone_detection_list_custom.items():
        if not new_custom_bone_list.get(key):
            new_custom_bone_list[key] = []

        for bone in new_custom_bone_list[key]:
            if bone in bones:
                bones.remove(bone)

        new_custom_bone_list[key] += bones

    detection_manager.bone_detection_list_custom = new_custom_bone_list


def export_custom_list(file_path):
    if not detection_manager.bone_detection_list_custom:
        return None

    save_custom_to_file(file_path=file_path)
    return os.path.basename(file_path)


def delete_custom_bone_list():
    detection_manager.bone_detection_list_custom = {}
    save_to_file_and_update()
