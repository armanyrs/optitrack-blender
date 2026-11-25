from collections import OrderedDict
import re
from mathutils import Quaternion

_LIVE_BONE_DELIM_RE = re.compile(r'[_:\-\.\s/"\'\\|]+')

# Canonical actor bone list with reference T-pose rotations
actor_bones = OrderedDict()
actor_bones["hip"] = Quaternion((-1.0, 0.0, -0.0, 0.0))
actor_bones["spine"] = Quaternion((-0.0, -0.0, 0.0, -1.0))
actor_bones["chest"] = Quaternion((-0.0, -0.0, 0.0, -1.0))
actor_bones["neck"] = Quaternion((-0.0, -0.0, 0.0, -1.0))
actor_bones["head"] = Quaternion((-0.0, -0.0, 0.0, -1.0))

actor_bones["leftShoulder"] = Quaternion((-0.70711, 0.0, 0.0, 0.70711))
actor_bones["leftUpperArm"] = Quaternion((-0.5, -0.5, -0.5, 0.5))
actor_bones["leftLowerArm"] = Quaternion((-0.5, -0.5, -0.5, 0.5))
actor_bones["leftHand"] = Quaternion((-0.5, -0.5, -0.5, 0.5))

actor_bones["rightShoulder"] = Quaternion((0.70711, 0.0, -0.0, 0.70711))
actor_bones["rightUpperArm"] = Quaternion((0.5, 0.5, -0.5, 0.5))
actor_bones["rightLowerArm"] = Quaternion((0.5, 0.5, -0.5, 0.5))
actor_bones["rightHand"] = Quaternion((0.5, 0.5, -0.5, 0.5))

actor_bones["leftUpLeg"] = Quaternion((0.70711, -0.0, 0.70711, -0.0))
actor_bones["leftLeg"] = Quaternion((0.70711, -0.0, 0.70711, 0.0))
actor_bones["leftFoot"] = Quaternion((0.0, -0.0, 0.70711, -0.70711))
actor_bones["leftToe"] = Quaternion((0.0, -0.0, 0.70711, -0.70711))

actor_bones["rightUpLeg"] = Quaternion((0.70711, -0.0, -0.70711, 0.0))
actor_bones["rightLeg"] = Quaternion((0.70711, -0.0, -0.70711, 0.0))
actor_bones["rightFoot"] = Quaternion((0.0, 0.0, -0.70711, 0.70711))
actor_bones["rightToe"] = Quaternion((0.0, 0.0, -0.70711, 0.70711))

# Extended with gloves/fingers
glove_bones = OrderedDict(actor_bones)

glove_bones["leftThumbProximal"] = Quaternion((-0.0923, -0.56098, -0.70106, 0.43046))
glove_bones["leftThumbMedial"] = Quaternion((-0.2706, -0.65328, -0.65328, 0.2706))
glove_bones["leftThumbDistal"] = Quaternion((-0.2706, -0.65328, -0.65328, 0.2706))

glove_bones["leftIndexProximal"] = Quaternion((-0.5, -0.5, -0.5, 0.5))
glove_bones["leftIndexMedial"] = Quaternion((-0.5, -0.5, -0.5, 0.5))
glove_bones["leftIndexDistal"] = Quaternion((-0.5, -0.5, -0.5, 0.5))

glove_bones["leftMiddleProximal"] = Quaternion((-0.5, -0.5, -0.5, 0.5))
glove_bones["leftMiddleMedial"] = Quaternion((-0.5, -0.5, -0.5, 0.5))
glove_bones["leftMiddleDistal"] = Quaternion((-0.5, -0.5, -0.5, 0.5))

glove_bones["leftRingProximal"] = Quaternion((-0.5, -0.5, -0.5, 0.5))
glove_bones["leftRingMedial"] = Quaternion((-0.5, -0.5, -0.5, 0.5))
glove_bones["leftRingDistal"] = Quaternion((-0.5, -0.5, -0.5, 0.5))

glove_bones["leftLittleProximal"] = Quaternion((-0.5, -0.5, -0.5, 0.5))
glove_bones["leftLittleMedial"] = Quaternion((-0.5, -0.5, -0.5, 0.5))
glove_bones["leftLittleDistal"] = Quaternion((-0.5, -0.5, -0.5, 0.5))

glove_bones["rightThumbProximal"] = Quaternion((0.0923, 0.56099, -0.70106, 0.43046))
glove_bones["rightThumbMedial"] = Quaternion((0.2706, 0.65328, -0.65328, 0.2706))
glove_bones["rightThumbDistal"] = Quaternion((0.2706, 0.65328, -0.65328, 0.2706))

glove_bones["rightIndexProximal"] = Quaternion((0.5, 0.5, -0.5, 0.5))
glove_bones["rightIndexMedial"] = Quaternion((0.5, 0.5, -0.5, 0.5))
glove_bones["rightIndexDistal"] = Quaternion((0.5, 0.5, -0.5, 0.5))

glove_bones["rightMiddleProximal"] = Quaternion((0.5, 0.5, -0.5, 0.5))
glove_bones["rightMiddleMedial"] = Quaternion((0.5, 0.5, -0.5, 0.5))
glove_bones["rightMiddleDistal"] = Quaternion((0.5, 0.5, -0.5, 0.5))

glove_bones["rightRingProximal"] = Quaternion((0.5, 0.5, -0.5, 0.5))
glove_bones["rightRingMedial"] = Quaternion((0.5, 0.5, -0.5, 0.5))
glove_bones["rightRingDistal"] = Quaternion((0.5, 0.5, -0.5, 0.5))

glove_bones["rightLittleProximal"] = Quaternion((0.5, 0.5, -0.5, 0.5))
glove_bones["rightLittleMedial"] = Quaternion((0.5, 0.5, -0.5, 0.5))
glove_bones["rightLittleDistal"] = Quaternion((0.5, 0.5, -0.5, 0.5))


def get_bones(with_gloves=True):
    """
    Return all canonical bones with their reference T-pose rotations.
    """
    return glove_bones if with_gloves else actor_bones


# Mapping between Motive native bone names and canonical Rokoko bone keys
MOTIVE_TO_CANONICAL = {
    "Hip": "hip",
    "Ab": "spine",
    "Chest": "chest",
    "Neck": "neck",
    "Head": "head",
    "LShoulder": "leftShoulder",
    "LUArm": "leftUpperArm",
    "LFArm": "leftLowerArm",
    "LHand": "leftHand",
    "RShoulder": "rightShoulder",
    "RUArm": "rightUpperArm",
    "RFArm": "rightLowerArm",
    "RHand": "rightHand",
    "LThigh": "leftUpLeg",
    "LShin": "leftLeg",
    "LFoot": "leftFoot",
    "LToe": "leftToe",
    "RThigh": "rightUpLeg",
    "RShin": "rightLeg",
    "RFoot": "rightFoot",
    "RToe": "rightToe",
    "LThumb1": "leftThumbProximal",
    "LThumb2": "leftThumbMedial",
    "LThumb3": "leftThumbDistal",
    "LIndex1": "leftIndexProximal",
    "LIndex2": "leftIndexMedial",
    "LIndex3": "leftIndexDistal",
    "LMiddle1": "leftMiddleProximal",
    "LMiddle2": "leftMiddleMedial",
    "LMiddle3": "leftMiddleDistal",
    "LRing1": "leftRingProximal",
    "LRing2": "leftRingMedial",
    "LRing3": "leftRingDistal",
    "LPinky1": "leftLittleProximal",
    "LPinky2": "leftLittleMedial",
    "LPinky3": "leftLittleDistal",
    "RThumb1": "rightThumbProximal",
    "RThumb2": "rightThumbMedial",
    "RThumb3": "rightThumbDistal",
    "RIndex1": "rightIndexProximal",
    "RIndex2": "rightIndexMedial",
    "RIndex3": "rightIndexDistal",
    "RMiddle1": "rightMiddleProximal",
    "RMiddle2": "rightMiddleMedial",
    "RMiddle3": "rightMiddleDistal",
    "RRing1": "rightRingProximal",
    "RRing2": "rightRingMedial",
    "RRing3": "rightRingDistal",
    "RPinky1": "rightLittleProximal",
    "RPinky2": "rightLittleMedial",
    "RPinky3": "rightLittleDistal",
}

CANONICAL_TO_MOTIVE = {v: k for k, v in MOTIVE_TO_CANONICAL.items()}

# Standard humanoid parent-child bone hierarchy for parent-relative FK evaluation
CANONICAL_PARENT = {
    "hip": None,
    "spine": "hip",
    "chest": "spine",
    "neck": "chest",
    "head": "neck",
    "leftShoulder": "chest",
    "leftUpperArm": "leftShoulder",
    "leftLowerArm": "leftUpperArm",
    "leftHand": "leftLowerArm",
    "rightShoulder": "chest",
    "rightUpperArm": "rightShoulder",
    "rightLowerArm": "rightUpperArm",
    "rightHand": "rightLowerArm",
    "leftUpLeg": "hip",
    "leftLeg": "leftUpLeg",
    "leftFoot": "leftLeg",
    "leftToe": "leftFoot",
    "rightUpLeg": "hip",
    "rightLeg": "rightUpLeg",
    "rightFoot": "rightLeg",
    "rightToe": "rightFoot",
    # Finger hierarchy (Left)
    "leftThumbProximal": "leftHand",
    "leftThumbMedial": "leftThumbProximal",
    "leftThumbDistal": "leftThumbMedial",
    "leftIndexProximal": "leftHand",
    "leftIndexMedial": "leftIndexProximal",
    "leftIndexDistal": "leftIndexMedial",
    "leftMiddleProximal": "leftHand",
    "leftMiddleMedial": "leftMiddleProximal",
    "leftMiddleDistal": "leftMiddleMedial",
    "leftRingProximal": "leftHand",
    "leftRingMedial": "leftRingProximal",
    "leftRingDistal": "leftRingMedial",
    "leftLittleProximal": "leftHand",
    "leftLittleMedial": "leftLittleProximal",
    "leftLittleDistal": "leftLittleMedial",
    # Finger hierarchy (Right)
    "rightThumbProximal": "rightHand",
    "rightThumbMedial": "rightThumbProximal",
    "rightThumbDistal": "rightThumbMedial",
    "rightIndexProximal": "rightHand",
    "rightIndexMedial": "rightIndexProximal",
    "rightIndexDistal": "rightIndexMedial",
    "rightMiddleProximal": "rightHand",
    "rightMiddleMedial": "rightMiddleProximal",
    "rightMiddleDistal": "rightMiddleMedial",
    "rightRingProximal": "rightHand",
    "rightRingMedial": "rightRingProximal",
    "rightRingDistal": "rightRingMedial",
    "rightLittleProximal": "rightHand",
    "rightLittleMedial": "rightLittleProximal",
    "rightLittleDistal": "rightLittleMedial",
}

# Expanded mappings including FBX, BVH, and Motive conventions
EXTENDED_MOTIVE_TO_CANONICAL = dict(MOTIVE_TO_CANONICAL)
EXTENDED_MOTIVE_TO_CANONICAL.update({
    # Hips / Pelvis
    "Hips": "hip",
    "Pelvis": "hip",
    "Root": "hip",
    # Spine / Ab
    "Spine": "spine",
    "Spine1": "spine",
    "Abdomen": "spine",
    "LowerSpine": "spine",
    # Chest
    "Spine2": "chest",
    "Spine3": "chest",
    "UpperChest": "chest",
    "Torso": "chest",
    # Neck & Head
    "Neck1": "neck",
    # Shoulders
    "LeftShoulder": "leftShoulder",
    "LeftCollar": "leftShoulder",
    "LCollar": "leftShoulder",
    "RightShoulder": "rightShoulder",
    "RightCollar": "rightShoulder",
    "RCollar": "rightShoulder",
    # Arms
    "LeftArm": "leftUpperArm",
    "LeftUpperArm": "leftUpperArm",
    "LUpArm": "leftUpperArm",
    "LArm": "leftUpperArm",
    "RightArm": "rightUpperArm",
    "RightUpperArm": "rightUpperArm",
    "RUpArm": "rightUpperArm",
    "RArm": "rightUpperArm",
    # Forearms
    "LeftForeArm": "leftLowerArm",
    "LeftLowerArm": "leftLowerArm",
    "LLowArm": "leftLowerArm",
    "LForeArm": "leftLowerArm",
    "RightForeArm": "rightLowerArm",
    "RightLowerArm": "rightLowerArm",
    "RLowArm": "rightLowerArm",
    "RForeArm": "rightLowerArm",
    # Hands
    "LeftHand": "leftHand",
    "RightHand": "rightHand",
    # Legs
    "LeftUpLeg": "leftUpLeg",
    "LeftThigh": "leftUpLeg",
    "RightUpLeg": "rightUpLeg",
    "RightThigh": "rightUpLeg",
    # Shins
    "LeftLeg": "leftLeg",
    "LeftLowLeg": "leftLeg",
    "LeftShin": "leftLeg",
    "RightLeg": "rightLeg",
    "RightLowLeg": "rightLeg",
    "RightShin": "rightLeg",
    # Feet
    "LeftFoot": "leftFoot",
    "RightFoot": "rightFoot",
    # Toes
    "LeftToe": "leftToe",
    "LeftToeBase": "leftToe",
    "RightToe": "rightToe",
    "RightToeBase": "rightToe",
    # Fingers (Left)
    "LeftHandThumb1": "leftThumbProximal",
    "LeftHandThumb2": "leftThumbMedial",
    "LeftHandThumb3": "leftThumbDistal",
    "LeftHandIndex1": "leftIndexProximal",
    "LeftHandIndex2": "leftIndexMedial",
    "LeftHandIndex3": "leftIndexDistal",
    "LeftHandMiddle1": "leftMiddleProximal",
    "LeftHandMiddle2": "leftMiddleMedial",
    "LeftHandMiddle3": "leftMiddleDistal",
    "LeftHandRing1": "leftRingProximal",
    "LeftHandRing2": "leftRingMedial",
    "LeftHandRing3": "leftRingDistal",
    "LeftHandPinky1": "leftLittleProximal",
    "LeftHandPinky2": "leftLittleMedial",
    "LeftHandPinky3": "leftLittleDistal",
    # Fingers (Right)
    "RightHandThumb1": "rightThumbProximal",
    "RightHandThumb2": "rightThumbMedial",
    "RightHandThumb3": "rightThumbDistal",
    "RightHandIndex1": "rightIndexProximal",
    "RightHandIndex2": "rightIndexMedial",
    "RightHandIndex3": "rightIndexDistal",
    "RightHandMiddle1": "rightMiddleProximal",
    "RightHandMiddle2": "rightMiddleMedial",
    "RightHandMiddle3": "rightMiddleDistal",
    "RightHandRing1": "rightRingProximal",
    "RightHandRing2": "rightRingMedial",
    "RightHandRing3": "rightRingDistal",
    "RightHandPinky1": "rightLittleProximal",
    "RightHandPinky2": "rightLittleMedial",
    "RightHandPinky3": "rightLittleDistal",
})


def find_frame_data_for_canonical_bone(canonical_key, frames, skeleton_name=""):
    """
    Finds the matching FrameData for a canonical bone from the frames dictionary,
    handling Motive, FBX, and BVH naming conventions, prefixes, and casing differences.
    """
    if not frames:
        return None

    # 1. Direct canonical to motive name
    direct_name = CANONICAL_TO_MOTIVE.get(canonical_key)
    if direct_name and direct_name in frames:
        return frames[direct_name]

    # Candidate search list for this canonical bone
    candidates = []
    if direct_name:
        candidates.append(direct_name)

    for mot_name, can_key in EXTENDED_MOTIVE_TO_CANONICAL.items():
        if can_key == canonical_key and mot_name not in candidates:
            candidates.append(mot_name)

    # 2. Check candidate names directly
    for cand in candidates:
        if cand in frames:
            return frames[cand]

    # 3. Normalized check (strip prefix, delimiters, and case-insensitive)
    prefix = (skeleton_name.lower() + "_") if skeleton_name else ""
    colon_prefix = (skeleton_name.lower() + ":") if skeleton_name else ""
    raw_skel_prefix = skeleton_name.lower() if skeleton_name else ""

    cand_set = {c.lower() for c in candidates}
    for frame_bone_name, frame_entry in frames.items():
        clean_name = frame_bone_name.lower()
        if prefix and clean_name.startswith(prefix):
            clean_name = clean_name[len(prefix):]
        elif colon_prefix and clean_name.startswith(colon_prefix):
            clean_name = clean_name[len(colon_prefix):]
        elif raw_skel_prefix and clean_name.startswith(raw_skel_prefix):
            clean_name = clean_name[len(raw_skel_prefix):]
        clean_name = clean_name.lstrip("_:-. /\"'\\|")

        if clean_name in cand_set:
            return frame_entry

        tokens = [t for t in _LIVE_BONE_DELIM_RE.split(frame_bone_name) if t]
        if len(tokens) > 1:
            for idx in range(1, len(tokens)):
                suffix_under = "_".join(tokens[idx:]).lower()
                suffix_joined = "".join(tokens[idx:]).lower()
                if suffix_under in cand_set or suffix_joined in cand_set:
                    return frame_entry

    return None
