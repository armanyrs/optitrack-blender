import os
import pathlib
import bpy

from .auto_detect_lists.bones import bone_list, ignore_rokoko_retargeting_bones

bone_detection_list = {}
bone_detection_list_unmodified = {}
bone_detection_list_custom = {}

main_dir = str(pathlib.Path(os.path.dirname(__file__)).parent.resolve())
resources_dir = os.path.join(main_dir, "resources")
custom_bones_dir = os.path.join(resources_dir, "custom_bones")
custom_bone_list_file = os.path.join(custom_bones_dir, "custom_bone_list.json")


def load_detection_lists():
    global bone_detection_list, bone_detection_list_unmodified, bone_detection_list_custom
    from .custom_schemes_manager import load_custom_lists_from_file

    bone_detection_list_unmodified = setup_bone_list(bone_list)
    bone_detection_list_custom, _ = load_custom_lists_from_file()
    bone_detection_list = combine_lists(bone_detection_list_unmodified, bone_detection_list_custom)


def setup_bone_list(raw_bone_list):
    new_bone_list = {}

    for bone_key, bone_values in raw_bone_list.items():
        if "left" not in bone_key:
            new_bone_list[bone_key] = [bone_value.lower() for bone_value in bone_values]
            if bone_key == "spine":
                new_bone_list["chest"] = [bone_value.lower() for bone_value in bone_values]
            continue

        bone_values_left = []
        bone_values_right = []

        for bone_name in bone_values:
            bone_name = bone_name.lower()

            if "\\l" in bone_name:
                for replacement in ["l", "left", "r", "right"]:
                    bone_name_new = bone_name.replace("\\l", replacement)
                    if bone_name_new in bone_values_left or bone_name_new in bone_values_right:
                        continue
                    if "l" in replacement:
                        bone_values_left.append(bone_name_new)
                    else:
                        bone_values_right.append(bone_name_new)

        bone_key_left = bone_key
        bone_key_right = bone_key.replace("left", "right")

        new_bone_list[bone_key_left] = bone_values_left
        new_bone_list[bone_key_right] = bone_values_right

    return new_bone_list


def combine_lists(internal_list, custom_list):
    combined_list = {}
    for key in internal_list.keys():
        combined_list[key] = []

    if custom_list:
        for key, values in custom_list.items():
            combined_list[key] = []
            for value in values:
                combined_list[key].append(value.lower())

    for key, values in internal_list.items():
        if key not in combined_list:
            combined_list[key] = []
        for value in values:
            if value not in combined_list[key]:
                combined_list[key].append(value)

    return combined_list


def standardize_bone_name(name):
    starts_with = [
        ("_", ""),
        ("ValveBiped_", ""),
        ("Valvebiped_", ""),
        ("Bip1_", "Bip_"),
        ("Bip01_", "Bip_"),
        ("Bip001_", "Bip_"),
        ("Character1_", ""),
        ("HLP_", ""),
        ("JD_", ""),
        ("JU_", ""),
        ("Armature|", ""),
        ("Bone_", ""),
        ("C_", ""),
        ("Cf_S_", ""),
        ("Cf_J_", ""),
        ("G_", ""),
        ("Joint_", ""),
        ("DEF_", ""),
        ("CC_Base_", ""),
    ]

    name = (
        name.replace(" ", "_")
        .replace("-", "_")
        .replace(".", "_")
        .replace("____", "_")
        .replace("___", "_")
        .replace("__", "_")
    )

    for replacement in starts_with:
        if name.startswith(replacement[0]):
            name = replacement[1] + name[len(replacement[0]) :]

    name_split = name.split("_")
    if len(name_split) > 1 and name_split[0].isdigit():
        name = name_split[1]

    name_split = name.split('"')
    if len(name_split) > 3:
        name = name_split[1]

    if ":" in name:
        parts = name.split(":")
        name = "".join(parts[1:])

    if name.endswith("S0"):
        name = name[:-2]
    if name.endswith("_Jnt"):
        name = name[:-4]

    return name.lower()


def detect_bone(obj, bone_name_key, bone_name_source=None):
    if not obj or obj.type != "ARMATURE":
        return ""

    if not bone_detection_list:
        load_detection_lists()

    found_bone_name = ""
    is_custom = False

    if not bone_name_source:
        bone_name_source = bone_name_key

    for bone in obj.pose.bones:
        if is_custom:
            break

        if bone_detection_list_custom.get(bone_name_key):
            for bone_name_detected in bone_detection_list_custom[bone_name_key]:
                if bone_name_detected == bone.name.lower():
                    found_bone_name = bone.name
                    is_custom = True
                    break

        if found_bone_name and bone_name_key != "chest":
            continue

        if bone_name_key in bone_detection_list:
            for bone_name_detected in bone_detection_list[bone_name_key]:
                if bone_name_detected == standardize_bone_name(bone.name):
                    found_bone_name = bone.name
                    break

        if not found_bone_name and bone_name_source.lower() == bone.name.lower():
            found_bone_name = bone.name

    return found_bone_name


def get_source_armature():
    if hasattr(bpy.context.scene, "motive_retargeting_armature_source"):
        return bpy.context.scene.motive_retargeting_armature_source
    return None


def get_target_armature():
    if hasattr(bpy.context.scene, "motive_retargeting_armature_target"):
        return bpy.context.scene.motive_retargeting_armature_target
    return None


def detect_retarget_bones():
    """
    Detects matching bones between the animated source armature and target armature.
    Returns: {source_bone_name: (target_bone_name, main_bone_name_key)}
    """
    if not bone_detection_list:
        load_detection_lists()

    bone_list_animated = []
    retargeting_dict = {}
    armature_source = get_source_armature()
    armature_target = get_target_armature()

    if not armature_source or not armature_target:
        return {}

    if not armature_source.animation_data or not armature_source.animation_data.action:
        return {}

    for fc in armature_source.animation_data.action.fcurves:
        bone_name = fc.data_path.split('"')
        if len(bone_name) == 3 and bone_name[1] not in bone_list_animated:
            bone_list_animated.append(bone_name[1])

    is_rokoko_animation = False
    if (
        "newton" in bone_list_animated
        and "RightFinger1Tip" in bone_list_animated
        and "HeadVertex" in bone_list_animated
        and "LeftFinger2Metacarpal" in bone_list_animated
    ):
        is_rokoko_animation = True

    spines_source = []
    spines_target = []
    found_main_bones = []

    for bone_name in bone_list_animated:
        if is_rokoko_animation and bone_name in ignore_rokoko_retargeting_bones:
            continue

        bone_item_source = bone_name
        main_bone_name = ""
        standardized_bone_name_source = standardize_bone_name(bone_name)

        for bone_main, bone_values in bone_detection_list.items():
            if bone_main == "chest":
                continue
            if bone_main in found_main_bones:
                continue
            if (
                bone_name.lower() in bone_values
                or standardized_bone_name_source in bone_values
                or standardized_bone_name_source == bone_main.lower()
            ):
                main_bone_name = bone_main
                if main_bone_name != "spine":
                    found_main_bones.append(main_bone_name)
                    break

        retargeting_dict[bone_item_source] = ("", main_bone_name)

        if not main_bone_name:
            continue

        if main_bone_name == "spine":
            spines_source.append(bone_name)
            continue

        custom_main_bone = main_bone_name.startswith("custom_bone_")
        if (
            custom_main_bone
            and standardize_bone_name(main_bone_name.replace("custom_bone_", ""))
            in bone_detection_list.get("spine", [])
        ):
            spines_source.append(bone_name)

        bone_item_target = detect_bone(
            armature_target, main_bone_name, bone_name_source=bone_item_source
        )
        retargeting_dict[bone_item_source] = (bone_item_target, main_bone_name)

    for bone in armature_target.pose.bones:
        bone_name_standardized = standardize_bone_name(bone.name)
        if bone_name_standardized in bone_detection_list.get("spine", []):
            spines_target.append(bone.name)

    if spines_target and spines_source:
        spine_dict = {}
        i = 0
        for spine in reversed(spines_source):
            i += 1
            if i == len(spines_target):
                break
            spine_dict[spine] = spines_target[-i]

        spine_dict[spines_source[0]] = spines_target[0]

        for spine_source, spine_target in spine_dict.items():
            for bone_source, bone_values in retargeting_dict.items():
                bone_target, bone_key = bone_values
                if bone_source == spine_source and not bone_target:
                    retargeting_dict[bone_source] = (spine_target, bone_key)
                    break

    return retargeting_dict
