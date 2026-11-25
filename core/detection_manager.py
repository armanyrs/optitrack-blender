import os
import pathlib
import re
import bpy

from .auto_detect_lists.bones import bone_list, ignore_rokoko_retargeting_bones

_POSE_BONE_PATH_RE = re.compile(
    r'^pose\.bones\[(?:"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\')\]'
)
_BONE_DELIMITERS_RE = re.compile(r'[_:\-\.\s/"\'\\|]+')
_TRAILING_DELIM_RE = re.compile(r'[_:\-\.\s/"\'\\|]')
_DIRECTIONAL_TOKENS = {"l", "r", "left", "right"}

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
        ("pcl_", ""),
        ("Pcl_", ""),
        ("motive_", ""),
        ("Motive_", ""),
        ("optitrack_", ""),
        ("Optitrack_", ""),
        ("skeleton_", ""),
        ("Skeleton_", ""),
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

    if ":" in name:
        parts = name.split(":")
        name = "".join(parts[1:])

    if name.endswith("S0"):
        name = name[:-2]
    if name.endswith("_Jnt"):
        name = name[:-4]

    return name.lower()


def extract_bone_name_from_data_path(data_path):
    """
    Safely extracts and unescapes the pose bone name from a Blender FCurve RNA data_path.
    Handles quotes, backslashes, brackets, colons, spaces, and any special characters.
    """
    if not data_path:
        return ""
    match = _POSE_BONE_PATH_RE.match(data_path)
    if not match:
        return ""
    raw_name = match.group(1) if match.group(1) is not None else match.group(2)
    return re.sub(r'\\(.)', r'\1', raw_name)


def iter_action_fcurves(armature):
    """
    Returns all FCurves for an armature's active Action, supporting both legacy
    action.fcurves and Blender 4.4+/5.0 slotted Action channelbags.
    """
    if not armature or not armature.animation_data or not armature.animation_data.action:
        return []

    action = armature.animation_data.action
    if hasattr(action, "fcurves"):
        try:
            fcurves = list(action.fcurves)
            if fcurves:
                return fcurves
        except Exception:
            pass

    # Blender 4.4+/5.0 slotted Action fallback
    if hasattr(action, "layers"):
        slot = getattr(armature.animation_data, "action_slot", None)
        collected = []
        for layer in action.layers:
            for strip in getattr(layer, "strips", []):
                if slot is not None and hasattr(strip, "channelbag"):
                    bag = strip.channelbag(slot)
                    if bag and hasattr(bag, "fcurves"):
                        collected.extend(bag.fcurves)
                elif hasattr(strip, "channelbags"):
                    for bag in strip.channelbags:
                        if hasattr(bag, "fcurves"):
                            collected.extend(bag.fcurves)
        return collected

    return []


def _get_all_known_bone_values():
    known = set()
    for key, values in bone_detection_list.items():
        known.add(key.lower())
        for val in values:
            known.add(val.lower())
    return known


def compute_common_bone_prefix(bone_names, known_bone_values=None):
    """
    Detects a shared skeleton/namespace prefix across bone names in an armature
    (e.g. 'Kel1_TR25"a_', 'mixamorig:', 'Actor 1 - ', or even a delimiter-less
    user prefix like 'Actor1' in 'Actor1Hips', 'Actor1Spine', 'Actor1LeftArm').
    Never strips directional prefixes such as 'Left', 'Right', 'L_', or 'R_'.
    """
    valid_names = [n for n in bone_names if n]
    if len(valid_names) < 2:
        return ""

    # 1. Count delimiter-terminated prefixes shared by a clear majority of bones
    prefix_counts = {}
    for name in valid_names:
        seen_for_bone = set()
        for match in _TRAILING_DELIM_RE.finditer(name):
            end_idx = match.end()
            if end_idx >= len(name):
                continue
            prefix = name[:end_idx]
            if prefix in seen_for_bone:
                continue
            seen_for_bone.add(prefix)

            # Avoid stripping directional prefixes (e.g. "L_", "Left_", "R_", "Right_")
            tokens = [t.lower() for t in _BONE_DELIMITERS_RE.split(prefix) if t]
            if not tokens or tokens[-1] in _DIRECTIONAL_TOKENS:
                continue

            prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1

    min_count = len(valid_names) if len(valid_names) == 2 else max(3, int(len(valid_names) * 0.6))
    best_delim_prefix = ""
    for prefix, count in prefix_counts.items():
        if count >= min_count and len(prefix) > len(best_delim_prefix):
            best_delim_prefix = prefix

    if best_delim_prefix:
        return best_delim_prefix

    # 2. Fallback for delimiter-less prefixes (e.g. 'Actor1Hips', 'Actor1Spine', 'Actor1LeftArm')
    lcp = os.path.commonprefix(valid_names)
    if lcp and lcp.lower() not in _DIRECTIONAL_TOKENS:
        if known_bone_values is None:
            known_bone_values = _get_all_known_bone_values()
        matched_after_strip = 0
        for name in valid_names:
            remainder = name[len(lcp):]
            if remainder and (
                remainder.lower() in known_bone_values
                or standardize_bone_name(remainder) in known_bone_values
            ):
                matched_after_strip += 1
        if matched_after_strip >= min(3, len(valid_names)):
            return lcp

    return ""


def build_bone_name_candidates(bone_name, common_prefix=""):
    """
    Generates ordered, deduplicated normalized candidate strings for matching
    a bone name against bone_detection_list regardless of user prefix or punctuation.
    """
    candidates = []

    def _add(val):
        if val and val not in candidates:
            candidates.append(val)

    # 1. Highest priority: strip detected armature-wide common prefix
    if common_prefix and bone_name.startswith(common_prefix):
        stripped = bone_name[len(common_prefix):]
        if stripped:
            _add(stripped.lower())
            _add(standardize_bone_name(stripped))

    # 2. Direct & standardized forms
    _add(bone_name.lower())
    _add(standardize_bone_name(bone_name))

    # 3. Progressive suffix candidates across all common delimiters
    tokens = [t for t in _BONE_DELIMITERS_RE.split(bone_name) if t]
    if len(tokens) > 1:
        for idx in range(1, len(tokens)):
            sub_under = "_".join(tokens[idx:])
            _add(sub_under.lower())
            _add(standardize_bone_name(sub_under))
            sub_joined = "".join(tokens[idx:])
            _add(sub_joined.lower())

    return candidates


def detect_bone(obj, bone_name_key, bone_name_source=None):
    if not obj or obj.type != "ARMATURE":
        return ""

    if not bone_detection_list:
        load_detection_lists()

    found_bone_name = ""
    is_custom = False

    if not bone_name_source:
        bone_name_source = bone_name_key

    target_prefix = compute_common_bone_prefix([b.name for b in obj.pose.bones])

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
            target_candidates = build_bone_name_candidates(bone.name, target_prefix)

            for bone_name_detected in bone_detection_list[bone_name_key]:
                if any(c == bone_name_detected for c in target_candidates):
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

    source_fcurves = iter_action_fcurves(armature_source)
    for fc in source_fcurves:
        extracted_name = extract_bone_name_from_data_path(fc.data_path)
        if extracted_name and extracted_name not in bone_list_animated:
            bone_list_animated.append(extracted_name)

    known_bone_values = _get_all_known_bone_values()
    source_prefix = compute_common_bone_prefix(
        bone_list_animated or [b.name for b in armature_source.pose.bones],
        known_bone_values=known_bone_values,
    )
    target_prefix = compute_common_bone_prefix(
        [b.name for b in armature_target.pose.bones],
        known_bone_values=known_bone_values,
    )

    hip_values = bone_detection_list.get("hip", [])

    # If source armature has no explicit hip bone, check if the source object itself represents the hip
    has_source_hip_bone = False
    for b in bone_list_animated:
        b_candidates = build_bone_name_candidates(b, source_prefix)
        if any(c in hip_values or c == "hip" for c in b_candidates):
            has_source_hip_bone = True
            break

    if not has_source_hip_bone:
        obj_candidates = build_bone_name_candidates(armature_source.name, source_prefix)
        has_obj_anim = any(
            not fc.data_path.startswith("pose.bones")
            for fc in source_fcurves
        )
        if (
            any("hip" in c or c in hip_values for c in obj_candidates)
            or has_obj_anim
        ):
            bone_list_animated.insert(0, armature_source.name)

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
        candidates = build_bone_name_candidates(bone_name, source_prefix)

        for bone_main, bone_values in bone_detection_list.items():
            if bone_main == "chest":
                continue
            if bone_main in found_main_bones:
                continue
            if any(
                c in bone_values or c == bone_main.lower()
                for c in candidates
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

    spine_values = bone_detection_list.get("spine", [])
    for bone in armature_target.pose.bones:
        target_candidates = build_bone_name_candidates(bone.name, target_prefix)
        if any(c in spine_values for c in target_candidates):
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
