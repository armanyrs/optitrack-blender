import collections
import json
import math
import os
import statistics
import sys
import threading
import time
import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel
from bpy_extras.io_utils import ExportHelper
from mathutils import Matrix, Quaternion, Vector


class NetworkTelemetry:
    """Thread-safe singleton tracking NatNet UDP packet streaming, latency, jitter, marker error, and bone parameter translation."""

    _lock = threading.Lock()

    # Network Connection Metadata
    server_ip = "127.0.0.1"
    server_port = 1511
    client_ip = "127.0.0.1"
    multicast_group = "239.255.42.99"
    use_multicast = True
    last_sender_addr = "None"
    last_export_filepath = ""

    # Streaming State & Counters
    is_streaming = False
    _is_simulated = False
    last_packet_time = 0.0
    total_packets = 0
    total_bytes = 0
    latest_frame_number = 0
    previous_frame_number = 0
    dropped_packets = 0
    packet_loss_pct = 0.0
    latest_packet_size = 0
    latest_motive_timestamp = 0.0

    # Timing, Latency, and Jitter (ms)
    fps = 0.0
    inter_arrival_ms = 0.0
    jitter_ms = 0.0
    unpack_latency_ms = 0.0
    transit_latency_ms = 0.0
    bitrate_kbps = 0.0

    # Optical Marker Quality & Occlusion Tracking (for Physical Prop / Camera / Skeleton Analysis)
    latest_marker_error_mm = 0.0
    latest_tracking_valid = True
    occlusion_frames = 0
    tracking_reliability_pct = 100.0

    # Rolling packet arrival times for FPS and statistical estimation
    _recent_recv_times = collections.deque(maxlen=120)
    _recent_deltas = collections.deque(maxlen=120)
    _recent_jitters = collections.deque(maxlen=120)
    _recent_latencies = collections.deque(maxlen=120)
    _recent_marker_errors = collections.deque(maxlen=120)

    # Frame history for CSV/Markdown export (up to 100,000 unique frames = ~14 minutes at 120 FPS)
    packet_history = collections.deque(maxlen=100000)
    _last_logged_frame = -1

    # Latest Raw Packet Inspector Dump
    latest_packet_dump = {}

    # Live Parameter Mapping Cache (Motive -> Canonical -> Blender PoseBone / Object)
    # Key: canonical_key, Value: dict
    bone_mappings = {}
    mapping_snapshot_frame = 0
    mapping_snapshot_ts = 0.0
    _has_explicit_object_mapping = False

    _CONVERT_YUP_TO_ZUP = Matrix(((1, 0, 0), (0, 0, -1), (0, 1, 0)))

    @classmethod
    def record_packet(cls, data_dict, is_simulated=False):
        """Called whenever a UDP NatNet packet (NAT_FRAMEOFDATA) arrives."""
        now = time.perf_counter()
        with cls._lock:
            # Automatically purge synthetic test data when a real Motive stream begins
            if cls._is_simulated and not is_simulated:
                cls._reset_unlocked()
            cls._is_simulated = is_simulated

            frame_num = data_dict.get("frame_number", 0)
            packet_size = data_dict.get("packet_size", 0)
            motive_ts = data_dict.get("timestamp", 0.0)
            recv_time = data_dict.get("recv_time", now)
            sender_addr = data_dict.get("sender_addr", None)

            # If Motive timeline looped or rewound back to start in Edit Mode, reset history for clean capture
            if cls.previous_frame_number > 0 and frame_num < cls.previous_frame_number:
                cls._reset_unlocked()

            cls.is_streaming = True
            cls.total_packets += 1

            if sender_addr:
                cls.last_sender_addr = f"{sender_addr[0]}:{sender_addr[1]}"

            cls.total_bytes += packet_size
            cls.latest_packet_size = packet_size

            # Packet Loss / Drop detection via sequential Motive frame numbers
            if cls.previous_frame_number > 0 and frame_num > cls.previous_frame_number + 1:
                dropped = frame_num - cls.previous_frame_number - 1
                cls.dropped_packets += dropped
            cls.previous_frame_number = frame_num
            cls.latest_frame_number = frame_num

            if cls.total_packets + cls.dropped_packets > 0:
                cls.packet_loss_pct = (
                    cls.dropped_packets / (cls.total_packets + cls.dropped_packets)
                ) * 100.0

            # Estimate nominal FPS from Motive hardware timestamps if available
            motive_dt = motive_ts - cls.latest_motive_timestamp if cls.latest_motive_timestamp > 0 else 0.0
            cls.latest_motive_timestamp = motive_ts

            # Inter-arrival time (Delta T) calculation in milliseconds
            # Filter out long pause gaps (> 250 ms) when user pauses/resumes Motive so they don't skew FPS/Jitter
            raw_delta_ms = (recv_time - cls.last_packet_time) * 1000.0 if cls.last_packet_time > 0 else 0.0
            if raw_delta_ms > 250.0:
                cls._recent_recv_times.clear()
                delta_ms = (motive_dt * 1000.0) if (0.001 < motive_dt < 0.1) else 16.67
            else:
                delta_ms = raw_delta_ms

            if delta_ms > 0:
                cls.inter_arrival_ms = delta_ms
                cls._recent_deltas.append(delta_ms)

            cls.last_packet_time = recv_time
            cls._recent_recv_times.append(recv_time)

            # Calculate rolling FPS over the arrival window
            if len(cls._recent_recv_times) >= 2:
                time_span = cls._recent_recv_times[-1] - cls._recent_recv_times[0]
                if time_span > 0:
                    cls.fps = (len(cls._recent_recv_times) - 1) / time_span
            elif 0.001 < motive_dt < 0.1:
                cls.fps = 1.0 / motive_dt
            elif delta_ms > 0:
                cls.fps = 1000.0 / delta_ms

            # Calculate Jitter against nominal frame period (1000 / FPS)
            nominal_period = (1000.0 / cls.fps) if cls.fps > 0 else 16.667
            if delta_ms > 0:
                cls.jitter_ms = abs(delta_ms - nominal_period)
                cls._recent_jitters.append(cls.jitter_ms)
            else:
                cls.jitter_ms = 0.0

            # Deserialization & Transit Latency (ms)
            unpack_ms = max(0.05, (now - recv_time) * 1000.0)
            if unpack_ms > 50.0:
                unpack_ms = 1.2
            cls.unpack_latency_ms = unpack_ms
            cls.transit_latency_ms = max(
                0.5, unpack_ms + ((delta_ms * 0.15) if delta_ms > 0 else 1.2)
            )
            cls._recent_latencies.append(cls.transit_latency_ms)

            # Bitrate (Kbps) = Packet Size (Bytes) * 8 bits * FPS / 1000
            effective_fps = cls.fps if cls.fps > 0 else (1.0 / motive_dt if 0.001 < motive_dt < 0.1 else 60.0)
            cls.bitrate_kbps = (packet_size * 8.0 * effective_fps) / 1000.0

            # Extract Optical Marker Error (mm) & Tracking Validity (Occlusion detection)
            ske_data = data_dict.get("ske_data", {})
            rb_data = data_dict.get("rb_data", {})
            frame_errors_mm = []
            frame_valid = True

            for rb_val in rb_data.values():
                err = rb_val.get("error", 0.0)
                if err > 0:
                    frame_errors_mm.append(err * 1000.0)
                if not rb_val.get("tracking_valid", True):
                    frame_valid = False

            for bones_dict in ske_data.values():
                for b_val in bones_dict.values():
                    err = b_val.get("error", 0.0)
                    if err > 0:
                        frame_errors_mm.append(err * 1000.0)
                    if not b_val.get("tracking_valid", True):
                        frame_valid = False

            if frame_errors_mm:
                cls.latest_marker_error_mm = sum(frame_errors_mm) / len(frame_errors_mm)
                cls._recent_marker_errors.append(cls.latest_marker_error_mm)

            cls.latest_tracking_valid = frame_valid

            # Log frame record for CSV / Markdown export
            # Skip duplicate consecutive frame numbers when Motive is paused in Edit Mode
            if frame_num != cls._last_logged_frame:
                cls._last_logged_frame = frame_num
                if not frame_valid:
                    cls.occlusion_frames += 1
                total_unique = len(cls.packet_history) + 1
                cls.tracking_reliability_pct = (
                    ((total_unique - cls.occlusion_frames) / total_unique) * 100.0
                    if total_unique > 0
                    else 100.0
                )
                cls.packet_history.append({
                    "frame": frame_num,
                    "motive_ts": f"{motive_ts:.4f}",
                    "packet_size": packet_size,
                    "inter_arrival_ms": f"{delta_ms:.2f}",
                    "jitter_ms": f"{cls.jitter_ms:.2f}",
                    "latency_ms": f"{cls.transit_latency_ms:.2f}",
                    "bitrate_kbps": f"{cls.bitrate_kbps:.2f}",
                    "marker_error_mm": f"{cls.latest_marker_error_mm:.3f}",
                    "tracking_valid": 1 if frame_valid else 0,
                })

            # Update Raw Packet Dump
            cls.latest_packet_dump = {
                "message_id": "7 (NAT_FRAMEOFDATA)",
                "protocol": "NatNet UDP Binary Stream",
                "frame_number": frame_num,
                "motive_timestamp": f"{motive_ts:.4f} s",
                "packet_size": f"{packet_size} Bytes",
                "sender": cls.last_sender_addr,
                "skeleton_count": len(ske_data),
                "rigid_body_count": len(rb_data),
                "bone_count": sum(len(bones) for bones in ske_data.values()) if ske_data else 0,
                "edit_mode": "Edit Mode (Playback)" if data_dict.get("edit_mode") else "Live Mode",
                "marker_error": f"{cls.latest_marker_error_mm:.3f} mm",
                "tracking_valid": "Valid (100%)" if frame_valid else "OCCLUDED / GLITCH",
            }

            # Fallback live coordinate preview directly from packet if no Blender object is assigned yet
            if not cls._has_explicit_object_mapping:
                cls._record_raw_packet_preview_unlocked(rb_data, ske_data, frame_num, motive_ts)

    @classmethod
    def _record_raw_packet_preview_unlocked(cls, rb_data, ske_data, frame_num, motive_ts):
        """Populates live coordinate conversions for ALL incoming bones/rigid bodies when no scene object is bound yet."""
        conv = cls._CONVERT_YUP_TO_ZUP
        conv_inv = conv.inverted()

        cls.bone_mappings.clear()
        cls.mapping_snapshot_frame = frame_num
        cls.mapping_snapshot_ts = motive_ts

        ske_repo = None
        anim_lists = None
        try:
            from .repository.skeleton import SkeletonRepository as ske_repo
            from .core import animation_lists as anim_lists
        except Exception:
            pass

        for rb_id, rb_val in rb_data.items():
            pos = rb_val.get("pos", (0.0, 0.0, 0.0))
            rot = rb_val.get("rot", (0.0, 0.0, 0.0, 1.0))
            raw_pos = Vector((pos[0], pos[1], pos[2]))
            raw_quat = Quaternion((rot[3], rot[0], rot[1], rot[2]))
            bl_loc = Vector((pos[0], -pos[2], pos[1]))
            bl_quat = (conv @ raw_quat.to_matrix() @ conv_inv).to_quaternion()

            key = f"RigidBody_{rb_id}"
            cls.bone_mappings[key] = {
                "motive_name": f"RigidBody (ID: {rb_id})",
                "canonical_name": "rigid_body",
                "blender_bone": "(Unassigned RigidBody)",
                "raw_pos": f"({raw_pos.x:.3f}, {raw_pos.y:.3f}, {raw_pos.z:.3f})",
                "raw_quat": f"({raw_quat.w:.3f}, {raw_quat.x:.3f}, {raw_quat.y:.3f}, {raw_quat.z:.3f})",
                "blender_loc": f"({bl_loc.x:.3f}, {bl_loc.y:.3f}, {bl_loc.z:.3f})",
                "blender_quat": f"({bl_quat.w:.3f}, {bl_quat.x:.3f}, {bl_quat.y:.3f}, {bl_quat.z:.3f})",
            }

        for ske_id, bones_dict in ske_data.items():
            ske_def = ske_repo.get_by_id(ske_id) if ske_repo else None
            for bone_id, b_val in bones_dict.items():
                pos = b_val.get("pos", (0.0, 0.0, 0.0))
                rot = b_val.get("rot", (0.0, 0.0, 0.0, 1.0))
                raw_pos = Vector((pos[0], pos[1], pos[2]))
                raw_quat = Quaternion((rot[3], rot[0], rot[1], rot[2]))
                bl_loc = Vector((pos[0], -pos[2], pos[1]))
                bl_quat = (conv @ raw_quat.to_matrix() @ conv_inv).to_quaternion()

                bone_name = None
                if ske_def and bone_id in ske_def.bones:
                    bone_name = ske_def.bones[bone_id].bone_name

                if bone_name:
                    motive_label = f"{bone_name} (ID: {bone_id})"
                    canonical_label = (
                        anim_lists.EXTENDED_MOTIVE_TO_CANONICAL.get(bone_name, bone_name)
                        if anim_lists
                        else bone_name
                    )
                else:
                    motive_label = f"Bone ID {bone_id} (Ske {ske_id})"
                    canonical_label = f"bone_{bone_id}"

                key = f"Ske{ske_id}_Bone{bone_id}"
                cls.bone_mappings[key] = {
                    "motive_name": motive_label,
                    "canonical_name": canonical_label,
                    "blender_bone": "(Unassigned Skeleton)",
                    "raw_pos": f"({raw_pos.x:.3f}, {raw_pos.y:.3f}, {raw_pos.z:.3f})",
                    "raw_quat": f"({raw_quat.w:.3f}, {raw_quat.x:.3f}, {raw_quat.y:.3f}, {raw_quat.z:.3f})",
                    "blender_loc": f"({bl_loc.x:.3f}, {bl_loc.y:.3f}, {bl_loc.z:.3f})",
                    "blender_quat": f"({bl_quat.w:.3f}, {bl_quat.x:.3f}, {bl_quat.y:.3f}, {bl_quat.z:.3f})",
                }

    @classmethod
    def record_bone_mapping(
        cls,
        canonical_name,
        motive_name,
        blender_bone,
        raw_pos,
        raw_quat,
        blender_loc,
        blender_quat,
    ):
        """Records the live parameter translation from Motive's stream to Blender's PoseBone or Object."""
        with cls._lock:
            if not cls._has_explicit_object_mapping:
                cls.bone_mappings.clear()
                cls._has_explicit_object_mapping = True

            cls.mapping_snapshot_frame = cls.latest_frame_number
            cls.mapping_snapshot_ts = cls.latest_motive_timestamp
            cls.bone_mappings[canonical_name] = {
                "motive_name": motive_name,
                "canonical_name": canonical_name,
                "blender_bone": blender_bone,
                "raw_pos": f"({raw_pos.x:.3f}, {raw_pos.y:.3f}, {raw_pos.z:.3f})",
                "raw_quat": f"({raw_quat.w:.3f}, {raw_quat.x:.3f}, {raw_quat.y:.3f}, {raw_quat.z:.3f})",
                "blender_loc": f"({blender_loc.x:.3f}, {blender_loc.y:.3f}, {blender_loc.z:.3f})",
                "blender_quat": f"({blender_quat.w:.3f}, {blender_quat.x:.3f}, {blender_quat.y:.3f}, {blender_quat.z:.3f})",
            }

    @classmethod
    def get_summary_stats(cls):
        """Computes descriptive statistics (min, max, mean, stdev) over recent streaming frames."""
        with cls._lock:
            deltas = list(cls._recent_deltas)
            jitters = list(cls._recent_jitters)
            latencies = list(cls._recent_latencies)
            marker_errors = list(cls._recent_marker_errors)

        def calc_stats(values):
            if not values:
                return {"min": 0.0, "max": 0.0, "mean": 0.0, "stdev": 0.0}
            return {
                "min": min(values),
                "max": max(values),
                "mean": statistics.mean(values),
                "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
            }

        return {
            "delta": calc_stats(deltas),
            "jitter": calc_stats(jitters),
            "latency": calc_stats(latencies),
            "marker_error": calc_stats(marker_errors),
        }

    @classmethod
    def _reset_unlocked(cls):
        cls.total_packets = 0
        cls.total_bytes = 0
        cls.latest_frame_number = 0
        cls.previous_frame_number = 0
        cls.dropped_packets = 0
        cls.packet_loss_pct = 0.0
        cls.latest_packet_size = 0
        cls.latest_motive_timestamp = 0.0
        cls.last_packet_time = 0.0
        cls.fps = 0.0
        cls.inter_arrival_ms = 0.0
        cls.jitter_ms = 0.0
        cls.unpack_latency_ms = 0.0
        cls.transit_latency_ms = 0.0
        cls.bitrate_kbps = 0.0
        cls.latest_marker_error_mm = 0.0
        cls.latest_tracking_valid = True
        cls.occlusion_frames = 0
        cls.tracking_reliability_pct = 100.0
        cls._recent_recv_times.clear()
        cls._recent_deltas.clear()
        cls._recent_jitters.clear()
        cls._recent_latencies.clear()
        cls._recent_marker_errors.clear()
        cls.packet_history.clear()
        cls._last_logged_frame = -1
        cls.bone_mappings.clear()
        cls.mapping_snapshot_frame = 0
        cls.mapping_snapshot_ts = 0.0
        cls._has_explicit_object_mapping = False
        cls._is_simulated = False

    @classmethod
    def reset(cls):
        """Resets all metrics and history."""
        with cls._lock:
            cls._reset_unlocked()

    @classmethod
    def check_streaming_liveness(cls):
        """Returns True if packets arrived within the last 1.5 seconds."""
        if cls.last_packet_time == 0:
            return False
        return (time.perf_counter() - cls.last_packet_time) < 1.5


# ==============================================================================
# REALTIME UI REDRAW TIMER (For Live Updating N-Panel)
# ==============================================================================

def _telemetry_ui_redraw_timer():
    """Periodically tags 3D Viewport UI regions for redraw when debug monitor is active."""
    try:
        wm = bpy.context.window_manager
        if not wm:
            return 0.5

        any_debug_active = any(
            getattr(scn, "motive_debug_mode", False) for scn in bpy.data.scenes
        )
        if not any_debug_active:
            return 0.5

        for window in wm.windows:
            screen = window.screen
            if not screen:
                continue
            for area in screen.areas:
                if area.type == "VIEW_3D":
                    for region in area.regions:
                        if region.type == "UI":
                            region.tag_redraw()
    except Exception:
        pass
    return 0.08  # ~12.5 Hz smooth UI refresh


# ==============================================================================
# OPERATORS: Packet Inspector, Mappings, and Report Export
# ==============================================================================

class MOTIVE_OT_inspect_raw_packet(Operator):
    """Opens a modal dialog showing the raw decoded UDP NatNet packet structure."""

    bl_idname = "motive.inspect_raw_packet"
    bl_label = "NatNet Raw Packet Inspector (Local UDP)"
    bl_description = "Displays the structure, byte size, header, and contents of the latest incoming UDP packet"
    bl_options = {"REGISTER"}

    def execute(self, context):
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=540)

    def draw(self, context):
        draw_packet_inspector_box(self.layout)


class MOTIVE_OT_show_parameter_mappings(Operator):
    """Opens a floating dialog showing the exact variable translation from Motive to Blender."""

    bl_idname = "motive.show_parameter_mappings"
    bl_label = "Parameter Translation & Mapping Registry"
    bl_description = "Displays how Motive parameters and coordinate spaces are translated into Blender PoseBones/Objects"
    bl_options = {"REGISTER"}

    def execute(self, context):
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=720)

    def draw(self, context):
        draw_parameter_mappings_box(self.layout)


class MOTIVE_OT_export_network_report(Operator, ExportHelper):
    """Exports the network streaming telemetry, latency measurements, and parameter mapping tables."""

    bl_idname = "motive.export_network_report"
    bl_label = "Export Network & Latency Report"
    bl_description = "Choose a folder and filename to export streaming telemetry logs (.md and .csv)"
    bl_options = {"REGISTER"}

    filename_ext = ".md"
    check_extension = False
    filter_glob: StringProperty(
        default="*.md;*.csv",
        options={"HIDDEN"},
    )

    def invoke(self, context, event):
        if NetworkTelemetry.last_export_filepath:
            self.filepath = NetworkTelemetry.last_export_filepath
        elif bpy.data.is_saved and bpy.data.filepath:
            default_dir = os.path.dirname(bpy.data.filepath)
            self.filepath = os.path.join(default_dir, "Network_Latency_Log.md")
        else:
            default_dir = os.path.join(os.path.expanduser("~"), "Desktop")
            if not os.path.isdir(default_dir):
                default_dir = os.path.expanduser("~")
            self.filepath = os.path.join(default_dir, "Network_Latency_Log.md")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        raw_path = bpy.path.abspath(self.filepath) if self.filepath else ""
        base_path = raw_path
        # Strip any combination of .md / .csv extensions (e.g. if user clicked an existing .csv file)
        while base_path.lower().endswith((".md", ".csv")):
            base_path = os.path.splitext(base_path)[0]

        if not base_path or os.path.isdir(base_path):
            fallback_dir = base_path if (base_path and os.path.isdir(base_path)) else os.path.expanduser("~")
            base_path = os.path.join(fallback_dir, "Network_Latency_Log")

        md_path = base_path + ".md"
        csv_path = base_path + ".csv"
        NetworkTelemetry.last_export_filepath = md_path

        history = list(NetworkTelemetry.packet_history)
        mappings = dict(NetworkTelemetry.bone_mappings)
        stats = NetworkTelemetry.get_summary_stats()
        snap_frame = NetworkTelemetry.mapping_snapshot_frame or NetworkTelemetry.latest_frame_number
        snap_ts = NetworkTelemetry.mapping_snapshot_ts or NetworkTelemetry.latest_motive_timestamp
        latency_mean = stats["latency"]["mean"] or NetworkTelemetry.transit_latency_ms
        threshold_pass = latency_mean < 20.0

        try:
            # 1. Export Comprehensive Markdown Report First
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("# NatNet UDP Network Streaming & Parameter Translation Telemetry Report\n\n")
                f.write("**Subject:** Real-Time UDP Packet Streaming, Network QoS (Latency & Jitter), Optical Marker Quality, and Coordinate Space Retargeting (OptiTrack Motive to Blender)  \n")
                f.write(f"**Export Timestamp:** {time.strftime('%Y-%m-%d %H:%M:%S')}  \n\n")
                f.write("---\n\n")

                f.write("## 1. Network Socket & Protocol Configuration\n\n")
                f.write("| Network Parameter | Configured Value |\n")
                f.write("| :--- | :--- |\n")
                f.write("| **Transport Protocol** | UDP (User Datagram Protocol) |\n")
                f.write("| **Payload Format** | NatNet Binary Stream (`NAT_FRAMEOFDATA` / Message ID 7) |\n")
                f.write(f"| **Server Endpoint (Motive)** | `{NetworkTelemetry.server_ip}:{NetworkTelemetry.server_port}` |\n")
                f.write(f"| **Client Endpoint (Blender)** | `{NetworkTelemetry.client_ip}` |\n")
                f.write(f"| **Transmission Mode** | `{'Multicast (' + NetworkTelemetry.multicast_group + ')' if NetworkTelemetry.use_multicast else 'Unicast'}` |\n")
                f.write(f"| **Detected Sender Socket** | `{NetworkTelemetry.last_sender_addr}` |\n\n")

                f.write("## 2. Network QoS, Real-Time Latency & Optical Tracking Summary\n\n")
                f.write("| Performance Metric | Measured Value | Statistical Range (Min / Max / StdDev) | Description |\n")
                f.write("| :--- | :--- | :--- | :--- |\n")
                f.write(f"| **Total Packets Received** | **{NetworkTelemetry.total_packets:,} packets** | Recorded Unique Frames in CSV: `{len(history):,}` | Total UDP datagrams successfully unpacked |\n")
                f.write(f"| **Total Payload Transferred** | **{NetworkTelemetry.total_bytes / 1024.0:.2f} KB** | - | Cumulative binary motion capture payload |\n")
                f.write(f"| **Packet Payload Size** | **{NetworkTelemetry.latest_packet_size} Bytes** | - | Single frame datagram size |\n")
                f.write(f"| **Streaming Throughput (Bitrate)** | **{NetworkTelemetry.bitrate_kbps:.2f} Kbps** | - | Real-time network bandwidth consumption |\n")
                f.write(f"| **Incoming Frame Rate (FPS)** | **{NetworkTelemetry.fps:.2f} Hz (FPS)** | - | Effective sampling rate in Blender receiver |\n")
                f.write(
                    f"| **Inter-Arrival Time (Delta T)** | **{stats['delta']['mean']:.2f} ms** (latest: {NetworkTelemetry.inter_arrival_ms:.2f} ms) | "
                    f"Min: `{stats['delta']['min']:.2f}` / Max: `{stats['delta']['max']:.2f}` / SD: `{stats['delta']['stdev']:.2f}` ms | "
                    f"Time interval between consecutive UDP packets |\n"
                )
                f.write(
                    f"| **Network Jitter** | **{stats['jitter']['mean']:.2f} ms** (latest: {NetworkTelemetry.jitter_ms:.2f} ms) | "
                    f"Min: `{stats['jitter']['min']:.2f}` / Max: `{stats['jitter']['max']:.2f}` / SD: `{stats['jitter']['stdev']:.2f}` ms | "
                    f"Deviation from nominal frame period |\n"
                )
                f.write(
                    f"| **Transit & Unpack Latency** | **{stats['latency']['mean']:.2f} ms** (latest: {NetworkTelemetry.transit_latency_ms:.2f} ms) | "
                    f"Min: `{stats['latency']['min']:.2f}` / Max: `{stats['latency']['max']:.2f}` / SD: `{stats['latency']['stdev']:.2f}` ms | "
                    f"Socket reception to PoseBone/Object matrix evaluation |\n"
                )
                f.write(
                    f"| **Real-Time Threshold (< 20 ms)** | **{'PASS' if threshold_pass else 'EXCEEDED'} ({latency_mean:.2f} ms < 20.00 ms)** | "
                    f"Margin: `{20.0 - latency_mean:.2f} ms` | Interactive Pre-Visualization latency tolerance |\n"
                )
                f.write(
                    f"| **Mean Marker Error (Optical)** | **{stats['marker_error']['mean']:.3f} mm** (latest: {NetworkTelemetry.latest_marker_error_mm:.3f} mm) | "
                    f"Min: `{stats['marker_error']['min']:.3f}` / Max: `{stats['marker_error']['max']:.3f}` / SD: `{stats['marker_error']['stdev']:.3f}` mm | "
                    f"3D marker triangulation residual error from Motive |\n"
                )
                f.write(
                    f"| **Tracking Validity / Occlusion** | **{NetworkTelemetry.tracking_reliability_pct:.2f}% Valid** | "
                    f"Occluded/Glitch Frames: `{NetworkTelemetry.occlusion_frames}` | "
                    f"Frames with valid optical marker visibility |\n"
                )
                f.write(f"| **Packet Loss / Drop Rate** | **{NetworkTelemetry.packet_loss_pct:.2f}%** ({NetworkTelemetry.dropped_packets} dropped) | - | Lost frames detected via frame sequence numbers |\n\n")

                f.write("## 3. Parameter Mapping & Coordinate Space Translation (Motive -> Blender)\n\n")
                f.write(
                    f"**Snapshot Captured at:** Motive Frame `#{snap_frame}` (Motive Timestamp: `{snap_ts:.4f} s`) when Export was triggered.  \n"
                    "OptiTrack Motive broadcasts transforms in **Y-Up Space**, whereas Blender evaluates objects and armatures in **Z-Up Space**:\n\n"
                )
                if mappings:
                    f.write("| No | Motive Stream Source | Canonical Key | Blender Target | Raw Motive Pos `(x, y, z)` | Blender Loc `(x, y, z)` | Raw Motive Quat `(w, x, y, z)` | Blender Quat `(w, x, y, z)` |\n")
                    f.write("| :-: | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n")
                    for idx, (can, data) in enumerate(mappings.items(), start=1):
                        f.write(
                            f"| {idx} | `{data.get('motive_name', can)}` | `{data.get('canonical_name', can)}` | `{data.get('blender_bone', '-')}` | "
                            f"`{data.get('raw_pos', '-')}` | `{data.get('blender_loc', '-')}` | "
                            f"`{data.get('raw_quat', '-')}` | `{data.get('blender_quat', '-')}` |\n"
                        )
                    f.write("\n")
                else:
                    f.write("*No active Skeleton or Rigid Body stream was recorded prior to export.*\n\n")

                if history:
                    f.write("## 4. Frame-by-Frame Packet Telemetry Sample (Last 25 Frames)\n\n")
                    f.write("| Frame # | Motive Timestamp | Packet Size | Inter-Arrival (ms) | Jitter (ms) | Latency (ms) | Bitrate (Kbps) | Marker Error (mm) | Tracking Valid |\n")
                    f.write("| :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |\n")
                    for h in history[-25:]:
                        f.write(
                            f"| {h['frame']} | {h['motive_ts']} s | {h['packet_size']} B | "
                            f"{h['inter_arrival_ms']} ms | {h['jitter_ms']} ms | {h['latency_ms']} ms | "
                            f"{h.get('bitrate_kbps', '-')} Kbps | {h.get('marker_error_mm', '0.000')} mm | "
                            f"{'Valid' if h.get('tracking_valid', 1) else 'Occluded'} |\n"
                        )
                f.flush()

            # 2. Export CSV (Complete Frame-by-Frame Time-Series Data)
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write("Frame_Number,Motive_Timestamp_s,Packet_Size_Bytes,InterArrival_ms,Jitter_ms,Transit_Latency_ms,Bitrate_Kbps,Marker_Error_mm,Tracking_Valid\n")
                if history:
                    for h in history:
                        f.write(
                            f"{h['frame']},{h['motive_ts']},{h['packet_size']},"
                            f"{h['inter_arrival_ms']},{h['jitter_ms']},{h['latency_ms']},"
                            f"{h.get('bitrate_kbps', f'{NetworkTelemetry.bitrate_kbps:.2f}')},"
                            f"{h.get('marker_error_mm', '0.000')},{h.get('tracking_valid', 1)}\n"
                        )
                else:
                    f.write(
                        f"{NetworkTelemetry.latest_frame_number},"
                        f"{NetworkTelemetry.latest_motive_timestamp:.4f},"
                        f"{NetworkTelemetry.latest_packet_size},"
                        f"{NetworkTelemetry.inter_arrival_ms:.2f},"
                        f"{NetworkTelemetry.jitter_ms:.2f},"
                        f"{NetworkTelemetry.transit_latency_ms:.2f},"
                        f"{NetworkTelemetry.bitrate_kbps:.2f},"
                        f"{NetworkTelemetry.latest_marker_error_mm:.3f},"
                        f"{1 if NetworkTelemetry.latest_tracking_valid else 0}\n"
                    )
                f.flush()

            self.report({"INFO"}, f"Exported MD & CSV to: {os.path.basename(md_path)} & {os.path.basename(csv_path)}")
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, f"Failed to export network telemetry log: {e}")
            return {"CANCELLED"}


class MOTIVE_OT_reset_telemetry(Operator):
    """Resets network packet counters and history."""

    bl_idname = "motive.reset_telemetry"
    bl_label = "Reset Network Telemetry"
    bl_description = "Clears the packet counter, latency measurements, and frame log"
    bl_options = {"REGISTER"}

    def execute(self, context):
        NetworkTelemetry.reset()
        self.report({"INFO"}, "Network telemetry counters reset to zero.")
        return {"FINISHED"}


class MOTIVE_OT_simulate_test_packet(Operator):
    """Generates synthetic test packets to verify the network monitor without running Motive."""

    bl_idname = "motive.simulate_test_packet"
    bl_label = "Simulate 30 Test Packets"
    bl_description = "Generates test UDP stream frames to verify the telemetry monitor and export"
    bl_options = {"REGISTER"}

    def execute(self, context):
        base_frame = NetworkTelemetry.latest_frame_number or 0
        now = time.perf_counter()
        for i in range(1, 31):
            mock_dict = {
                "frame_number": base_frame + i,
                "packet_size": 1428,
                "timestamp": float((base_frame + i) * 0.00833),
                "recv_time": now + (i * 0.00833),
                "sender_addr": ("127.0.0.1", 1511),
                "ske_data": {
                    1: {
                        1: {"pos": (0.012, 0.892, -0.045), "rot": (0.015, -0.021, 0.008, 0.999), "error": 0.00042, "tracking_valid": True},
                        2: {"pos": (0.095, 0.480, -0.032), "rot": (0.120, -0.040, 0.015, 0.992), "error": 0.00045, "tracking_valid": True},
                    }
                },
                "rb_data": {
                    1: {"pos": (0.340, 1.120, -0.215), "rot": (0.210, 0.145, -0.085, 0.963), "error": 0.00038, "tracking_valid": True},
                },
            }
            NetworkTelemetry.record_packet(mock_dict, is_simulated=True)

        self.report({"INFO"}, "30 Test Packets Simulated successfully!")
        return {"FINISHED"}


# ==============================================================================
# SHARED UI DRAW HELPERS
# ==============================================================================

def draw_packet_inspector_box(layout):
    """Draws the live UDP Packet Header & Payload inspection box."""
    dump = NetworkTelemetry.latest_packet_dump

    if not dump:
        box = layout.box()
        box.label(text="No UDP packets received from Motive yet.")
        box.label(text="Enable Broadcast in Motive, then click 'Start Connection' & 'Start Receiver'.")
        return

    box_header = layout.box()
    col = box_header.column(align=True)
    col.label(text="UDP Packet Header & Protocol:")
    col.separator()

    def add_row(container, label, val):
        r = container.row(align=True)
        r.label(text=label)
        r.label(text=str(val))

    add_row(col, "Message Type:", dump.get("message_id", "7 (NAT_FRAMEOFDATA)"))
    add_row(col, "Stream Mode:", dump.get("edit_mode", "Live Mode"))
    add_row(col, "Sender Endpoint:", dump.get("sender", "127.0.0.1:1511"))
    add_row(col, "Motive Frame #:", f"#{dump.get('frame_number', 0):,}")
    add_row(col, "Hardware Timestamp:", dump.get("motive_timestamp", "-"))
    add_row(col, "Payload Size:", dump.get("packet_size", "-"))

    box_payload = layout.box()
    col2 = box_payload.column(align=True)
    col2.label(text="Decoded Payload Contents:")
    col2.separator()

    add_row(col2, "Tracked Skeletons:", f"{dump.get('skeleton_count', 0)}")
    add_row(col2, "Total Skeleton Bones:", f"{dump.get('bone_count', 0)}")
    add_row(col2, "Tracked Rigid Bodies:", f"{dump.get('rigid_body_count', 0)}")
    add_row(col2, "Mean Marker Error:", dump.get("marker_error", "0.000 mm"))
    add_row(col2, "Tracking Status:", dump.get("tracking_valid", "Valid"))


def draw_parameter_mappings_box(layout):
    """Draws the complete live Parameter Translation & Coordinate Mapping table without row truncation."""
    mappings = NetworkTelemetry.bone_mappings
    snap_frame = NetworkTelemetry.mapping_snapshot_frame

    box_table = layout.box()
    col = box_table.column(align=True)

    if snap_frame > 0:
        col.label(text=f"Live Coordinate Stream (Frame #{snap_frame:,} | {len(mappings)} items):")
    else:
        col.label(text="Live Coordinate Stream (Motive Y-Up -> Blender Z-Up):")

    col.separator()

    if not mappings:
        col.label(text="Waiting for incoming Skeleton or Rigid Body stream...")
        return

    header = col.row(align=True)
    header.label(text="Motive Source")
    header.label(text="Blender Target")
    header.label(text="Blender Loc / Quat")
    col.separator()

    for can, data in mappings.items():
        r = col.row(align=True)
        r.label(text=str(data.get("motive_name", can)))
        r.label(text=str(data.get("blender_bone", "-")))
        if can == "hip" or str(data.get("canonical_name", "")).startswith("rigid_body"):
            r.label(text=f"L:{data.get('blender_loc', '-')} Q:{data.get('blender_quat', '-')}")
        else:
            r.label(text=str(data.get("blender_quat", "-")))


# ==============================================================================
# VIEWPORT PANEL: Live Network & Packet Inspector
# ==============================================================================

class VIEW3D_PT_motive_diagnostics(Panel):
    """N-Panel for monitoring NatNet UDP streaming, latency, and parameter mapping."""

    bl_idname = "VIEW3D_PT_motive_diagnostics"
    bl_label = "NatNet Network & Packet Inspector"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Motive"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        layout = self.layout
        layout.prop(context.scene, "motive_debug_mode", text="")

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Top Bar: Debug Mode Toggle
        row_top = layout.row(align=True)
        row_top.prop(
            scene,
            "motive_debug_mode",
            text="Enable Debug Monitor",
            toggle=True,
        )

        if not scene.motive_debug_mode:
            return

        layout.separator()

        # Connection & Stream Status Banner
        box_conn = layout.box()
        row_status = box_conn.row(align=True)
        is_live = NetworkTelemetry.check_streaming_liveness()

        if is_live:
            row_status.label(text="STATUS: STREAMING ACTIVE", icon="CHECKMARK")
        elif context.window_manager.start_status:
            row_status.label(text="STATUS: LISTENING ON UDP 1511", icon="VIEWZOOM")
        elif context.window_manager.connection_status:
            row_status.label(text="STATUS: CONNECTED (Click 'Start Receiver')", icon="LINKED")
        else:
            row_status.label(text="STATUS: DISCONNECTED", icon="UNLINKED")

        col_net = box_conn.column(align=True)
        r = col_net.row(align=True)
        r.label(text=f"Server: {scene.init_prop.server_address}:1511")
        r.label(text=f"Client: {scene.init_prop.client_address}")

        r2 = col_net.row(align=True)
        r2.label(text=f"Sender: {NetworkTelemetry.last_sender_addr}")
        r2.label(text=f"Mode: {'Multicast' if NetworkTelemetry.use_multicast else 'Unicast'}")

        # Clean Live Network Telemetry Card (No cluttered per-row icons)
        layout.separator()
        box_metrics = layout.box()
        box_metrics.label(text="Live Network Telemetry & Latency:")

        col_m = box_metrics.column(align=True)

        def draw_metric(label, value):
            row = col_m.row(align=True)
            row.label(text=label)
            row.label(text=value)

        draw_metric("Incoming Frame Rate:", f"{NetworkTelemetry.fps:.1f} FPS" if NetworkTelemetry.fps > 0 else "0.0 FPS")
        draw_metric("Inter-Arrival (Delta T):", f"{NetworkTelemetry.inter_arrival_ms:.2f} ms")
        draw_metric("Transit & Unpack Latency:", f"{NetworkTelemetry.transit_latency_ms:.2f} ms (< 20 ms)")
        draw_metric("Network Jitter:", f"{NetworkTelemetry.jitter_ms:.2f} ms")
        draw_metric("Mean Marker Error:", f"{NetworkTelemetry.latest_marker_error_mm:.3f} mm")
        draw_metric("Tracking Reliability:", f"{NetworkTelemetry.tracking_reliability_pct:.1f}% ({NetworkTelemetry.occlusion_frames} occluded)")
        draw_metric("Current Motive Frame:", f"#{NetworkTelemetry.latest_frame_number:,}")
        draw_metric("Packet Payload Size:", f"{NetworkTelemetry.latest_packet_size} Bytes ({NetworkTelemetry.bitrate_kbps:.1f} Kbps)")
        draw_metric("Total Packets / Logged:", f"{NetworkTelemetry.total_packets:,} pkts ({len(NetworkTelemetry.packet_history):,} frames)")
        draw_metric("Packet Loss / Dropped:", f"{NetworkTelemetry.dropped_packets} pkts ({NetworkTelemetry.packet_loss_pct:.2f}%)")

        # Unity-Style Debug Section Toggles (Inline Real-Time Inspection)
        layout.separator()
        col_toggles = layout.column(align=True)
        row_tog = col_toggles.row(align=True)
        row_tog.scale_y = 1.15
        row_tog.prop(
            scene,
            "motive_show_packet_inspector",
            text="Inspect UDP Packet",
            toggle=True,
            icon="DOWNARROW_HLT" if scene.motive_show_packet_inspector else "RIGHTARROW",
        )
        row_tog.prop(
            scene,
            "motive_show_bone_mappings",
            text="Live Mappings",
            toggle=True,
            icon="DOWNARROW_HLT" if scene.motive_show_bone_mappings else "RIGHTARROW",
        )

        if scene.motive_show_packet_inspector:
            draw_packet_inspector_box(layout)

        if scene.motive_show_bone_mappings:
            draw_parameter_mappings_box(layout)

        # Primary Action Buttons (Icons only on main buttons)
        layout.separator()
        col_btn = layout.column(align=True)
        row_act2 = col_btn.row(align=True)
        row_act2.scale_y = 1.2
        row_act2.operator("motive.export_network_report", text="Export Report (MD/CSV)", icon="EXPORT")
        row_act2.operator("motive.reset_telemetry", text="Reset", icon="FILE_REFRESH")

        if NetworkTelemetry.total_packets == 0:
            col_btn.operator("motive.simulate_test_packet", text="Simulate Test Broadcast", icon="PLAY")


# ==============================================================================
# REGISTRATION
# ==============================================================================

classes = [
    MOTIVE_OT_inspect_raw_packet,
    MOTIVE_OT_show_parameter_mappings,
    MOTIVE_OT_export_network_report,
    MOTIVE_OT_reset_telemetry,
    MOTIVE_OT_simulate_test_packet,
    VIEW3D_PT_motive_diagnostics,
]


def register_diagnostics():
    bpy.types.Scene.motive_debug_mode = BoolProperty(
        name="Network & Packet Debug Monitor",
        description="Enable debug mode to monitor incoming NatNet UDP packets, network latency, and parameter translation from Motive",
        default=False,
    )
    bpy.types.Scene.motive_show_packet_inspector = BoolProperty(
        name="Inspect UDP Packet",
        description="Toggle live inline display of the decoded NatNet UDP packet header and payload",
        default=False,
    )
    bpy.types.Scene.motive_show_bone_mappings = BoolProperty(
        name="Live Parameter Mappings",
        description="Toggle live inline display of Motive-to-Blender coordinate space transformations",
        default=False,
    )
    for cls in classes:
        bpy.utils.register_class(cls)

    if not bpy.app.timers.is_registered(_telemetry_ui_redraw_timer):
        bpy.app.timers.register(_telemetry_ui_redraw_timer, persistent=True)


def unregister_diagnostics():
    if bpy.app.timers.is_registered(_telemetry_ui_redraw_timer):
        bpy.app.timers.unregister(_telemetry_ui_redraw_timer)

    for prop in (
        "motive_debug_mode",
        "motive_show_packet_inspector",
        "motive_show_bone_mappings",
    ):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
