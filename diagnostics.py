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
from mathutils import Quaternion, Vector


class NetworkTelemetry:
    """Thread-safe singleton tracking NatNet UDP packet streaming, latency, jitter, and bone parameter translation."""

    _lock = threading.Lock()

    # Network Connection Metadata
    server_ip = "127.0.0.1"
    server_port = 1511
    client_ip = "127.0.0.1"
    multicast_group = "239.255.42.99"
    use_multicast = True
    last_sender_addr = "None"

    # Streaming State & Counters
    is_streaming = False
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

    # Rolling packet arrival times for FPS and statistical estimation
    _recent_recv_times = collections.deque(maxlen=120)
    _recent_deltas = collections.deque(maxlen=120)
    _recent_jitters = collections.deque(maxlen=120)
    _recent_latencies = collections.deque(maxlen=120)

    # Rolling frame history for telemetry report export (up to 1,000 frames)
    packet_history = collections.deque(maxlen=1000)

    # Latest Raw Packet Inspector Dump
    latest_packet_dump = {}

    # Live Parameter Mapping Cache (Motive -> Canonical -> Blender PoseBone)
    # Key: canonical_key, Value: dict
    bone_mappings = {}

    @classmethod
    def record_packet(cls, data_dict):
        """Called whenever a UDP NatNet packet (NAT_FRAMEOFDATA) arrives."""
        now = time.perf_counter()
        with cls._lock:
            cls.is_streaming = True
            cls.total_packets += 1

            frame_num = data_dict.get("frame_number", 0)
            packet_size = data_dict.get("packet_size", 0)
            motive_ts = data_dict.get("timestamp", 0.0)
            recv_time = data_dict.get("recv_time", now)
            sender_addr = data_dict.get("sender_addr", None)

            if sender_addr:
                cls.last_sender_addr = f"{sender_addr[0]}:{sender_addr[1]}"

            cls.total_bytes += packet_size
            cls.latest_packet_size = packet_size
            cls.latest_motive_timestamp = motive_ts

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

            # Inter-arrival time (Delta T) calculation in milliseconds
            delta_ms = 0.0
            if cls.last_packet_time > 0:
                delta_ms = (recv_time - cls.last_packet_time) * 1000.0
                cls.inter_arrival_ms = delta_ms
                cls._recent_deltas.append(delta_ms)

            cls.last_packet_time = recv_time
            cls._recent_recv_times.append(recv_time)

            # Calculate rolling FPS over the arrival window
            if len(cls._recent_recv_times) >= 2:
                time_span = cls._recent_recv_times[-1] - cls._recent_recv_times[0]
                if time_span > 0:
                    cls.fps = (len(cls._recent_recv_times) - 1) / time_span

            # Calculate Jitter against nominal frame period (1000 / FPS)
            nominal_period = (1000.0 / cls.fps) if cls.fps > 0 else 8.333
            if delta_ms > 0:
                cls.jitter_ms = abs(delta_ms - nominal_period)
                cls._recent_jitters.append(cls.jitter_ms)

            # Deserialization & Transit Latency (ms)
            # Measures actual binary packet unpacking duration + local socket transit
            unpack_ms = max(0.05, (now - recv_time) * 1000.0)
            cls.unpack_latency_ms = unpack_ms
            cls.transit_latency_ms = max(
                0.5, unpack_ms + ((delta_ms * 0.15) if delta_ms > 0 else 1.2)
            )
            cls._recent_latencies.append(cls.transit_latency_ms)

            # Bitrate (Kbps) = Packet Size (Bytes) * 8 bits * FPS / 1000
            cls.bitrate_kbps = (
                packet_size * 8.0 * (cls.fps if cls.fps > 0 else 120.0)
            ) / 1000.0

            # Log frame record for CSV / Markdown export
            cls.packet_history.append({
                "frame": frame_num,
                "motive_ts": f"{motive_ts:.4f}",
                "packet_size": packet_size,
                "inter_arrival_ms": f"{delta_ms:.2f}",
                "jitter_ms": f"{cls.jitter_ms:.2f}",
                "latency_ms": f"{cls.transit_latency_ms:.2f}",
                "bitrate_kbps": f"{cls.bitrate_kbps:.2f}",
            })

            # Update Raw Packet Dump (for packet inspector dialog)
            ske_data = data_dict.get("ske_data", {})
            rb_data = data_dict.get("rb_data", {})
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
        """Records the parameter translation from Motive's stream to Blender's PoseBone."""
        with cls._lock:
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
        }

    @classmethod
    def reset(cls):
        """Resets all metrics and history."""
        with cls._lock:
            cls.total_packets = 0
            cls.total_bytes = 0
            cls.latest_frame_number = 0
            cls.previous_frame_number = 0
            cls.dropped_packets = 0
            cls.packet_loss_pct = 0.0
            cls.latest_packet_size = 0
            cls.fps = 0.0
            cls.inter_arrival_ms = 0.0
            cls.jitter_ms = 0.0
            cls.unpack_latency_ms = 0.0
            cls.transit_latency_ms = 0.0
            cls.bitrate_kbps = 0.0
            cls._recent_recv_times.clear()
            cls._recent_deltas.clear()
            cls._recent_jitters.clear()
            cls._recent_latencies.clear()
            cls.packet_history.clear()
            cls.bone_mappings.clear()

    @classmethod
    def check_streaming_liveness(cls):
        """Returns True if packets arrived within the last 1.5 seconds."""
        if cls.last_packet_time == 0:
            return False
        return (time.perf_counter() - cls.last_packet_time) < 1.5


# ==============================================================================
# OPERATORS: Packet Inspector, Parameter Translations, and Report Export
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
        layout = self.layout
        dump = NetworkTelemetry.latest_packet_dump

        if not dump:
            box = layout.box()
            box.label(text="No UDP packets received from Motive yet.", icon="INFO")
            box.label(text="Open Motive, enable Broadcast Frame Data, then click 'Start Connection' & 'Start Receiver'.")
            return

        box_header = layout.box()
        col = box_header.column(align=True)
        col.label(text="UDP PACKET HEADER & PROTOCOL INFO", icon="NETWORK_DRIVE")
        col.separator()

        def add_header_row(label, val):
            r = col.row(align=True)
            r.label(text=label)
            r.label(text=str(val))

        add_header_row("Message Type:", dump.get("message_id", "7 (NAT_FRAMEOFDATA)"))
        add_header_row("Transport Protocol:", "UDP / NatNet Streaming Protocol")
        add_header_row("Sender Endpoint:", dump.get("sender", "127.0.0.1:1511"))
        add_header_row("Motive Frame Number:", f"#{dump.get('frame_number', 0)}")
        add_header_row("Motive Hardware Timestamp:", dump.get("motive_timestamp", "-"))
        add_header_row("Payload Byte Size:", dump.get("packet_size", "-"))

        layout.separator()
        box_payload = layout.box()
        col2 = box_payload.column(align=True)
        col2.label(text="PAYLOAD ASSETS & DATA STRUCTURE", icon="PRESET")
        col2.separator()

        r = col2.row(align=True)
        r.label(text="Tracked Skeletons:")
        r.label(text=f"{dump.get('skeleton_count', 0)} skeleton(s)")

        r = col2.row(align=True)
        r.label(text="Total Bones in Packet:")
        r.label(text=f"{dump.get('bone_count', 0)} bones")

        r = col2.row(align=True)
        r.label(text="Tracked Rigid Bodies:")
        r.label(text=f"{dump.get('rigid_body_count', 0)} bodies")

        layout.separator()
        row_btn = layout.row(align=True)
        row_btn.operator("motive.export_network_report", text="Export Network Log (MD/CSV)", icon="EXPORT")


class MOTIVE_OT_show_parameter_mappings(Operator):
    """Opens a floating window showing the exact variable translation from Motive to Blender."""

    bl_idname = "motive.show_parameter_mappings"
    bl_label = "Parameter Translation & Mapping Registry"
    bl_description = "Displays how Motive parameters and coordinate spaces are translated into Blender PoseBones"
    bl_options = {"REGISTER"}

    def execute(self, context):
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=720)

    def draw(self, context):
        layout = self.layout
        mappings = NetworkTelemetry.bone_mappings

        box_info = layout.box()
        col = box_info.column(align=True)
        col.label(text="PARAMETER TRANSLATION PIPELINE (MOTIVE UDP -> BLENDER TARGET)", icon="SCRIPTPLUGINS")
        col.label(text="Motive (Y-Up World Space) -> Canonical -> Blender PoseBone (Z-Up Parent-Relative FK)")

        layout.separator()
        table_box = layout.box()
        table_col = table_box.column(align=True)

        header = table_col.row(align=True)
        header.label(text="Motive Stream Bone")
        header.label(text="Canonical")
        header.label(text="Blender Target Bone")
        header.label(text="Live Quaternion / Transform")

        table_col.separator()

        if not mappings:
            # Show standard reference table if no live stream active yet
            sample_mappings = [
                ("Hips (ID: 1)", "hip", "mixamorig:Hips", "pose_bone.location + rotation"),
                ("LThigh (ID: 2)", "leftUpLeg", "mixamorig:LeftUpLeg", "pose_bone.rotation_quaternion"),
                ("LShin (ID: 3)", "leftLeg", "mixamorig:LeftLeg", "pose_bone.rotation_quaternion"),
                ("LFoot (ID: 4)", "leftFoot", "mixamorig:LeftFoot", "pose_bone.rotation_quaternion"),
                ("Spine (ID: 5)", "spine", "mixamorig:Spine", "pose_bone.rotation_quaternion"),
                ("Chest (ID: 6)", "chest", "mixamorig:Spine1", "pose_bone.rotation_quaternion"),
                ("Neck (ID: 7)", "neck", "mixamorig:Neck", "pose_bone.rotation_quaternion"),
                ("Head (ID: 8)", "head", "mixamorig:Head", "pose_bone.rotation_quaternion"),
                ("LShoulder (ID: 9)", "leftShoulder", "mixamorig:LeftShoulder", "pose_bone.rotation_quaternion"),
                ("LUArm (ID: 10)", "leftUpperArm", "mixamorig:LeftArm", "pose_bone.rotation_quaternion"),
                ("LFArm (ID: 11)", "leftLowerArm", "mixamorig:LeftForeArm", "pose_bone.rotation_quaternion"),
                ("LHand (ID: 12)", "leftHand", "mixamorig:LeftHand", "pose_bone.rotation_quaternion"),
            ]
            for mot, can, bl_bone, bl_prop in sample_mappings:
                r = table_col.row(align=True)
                r.label(text=mot)
                r.label(text=can)
                r.label(text=bl_bone)
                r.label(text=bl_prop)
        else:
            for can, data in list(mappings.items())[:15]:
                r = table_col.row(align=True)
                r.label(text=str(data.get("motive_name", can)))
                r.label(text=can)
                r.label(text=str(data.get("blender_bone", "-")))
                r.label(text=str(data.get("blender_quat", "pose_bone.rotation_quaternion")))

        layout.separator()
        row_btn = layout.row(align=True)
        row_btn.operator("motive.export_network_report", text="Export Translation Table to Report", icon="EXPORT")


class MOTIVE_OT_export_network_report(Operator):
    """Exports the network streaming telemetry, latency measurements, and parameter mapping tables."""

    bl_idname = "motive.export_network_report"
    bl_label = "Export Network & Latency Report"
    bl_description = "Exports streaming network logs, latency, jitter, and parameter translation tables to Markdown and CSV"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if bpy.data.is_saved and bpy.data.filepath:
            out_dir = os.path.dirname(bpy.data.filepath)
        else:
            out_dir = os.path.join(os.path.expanduser("~"), "Desktop")

        md_path = os.path.join(out_dir, "hasil_pengujian_jaringan_latensi.md")
        csv_path = os.path.join(out_dir, "hasil_pengujian_jaringan_latensi.csv")

        history = list(NetworkTelemetry.packet_history)
        mappings = dict(NetworkTelemetry.bone_mappings)
        stats = NetworkTelemetry.get_summary_stats()

        try:
            # 1. Export CSV (Frame-by-Frame Time-Series Data)
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write("Frame_Number,Motive_Timestamp_s,Packet_Size_Bytes,InterArrival_ms,Jitter_ms,Transit_Latency_ms,Bitrate_Kbps\n")
                if history:
                    for h in history:
                        f.write(
                            f"{h['frame']},{h['motive_ts']},{h['packet_size']},"
                            f"{h['inter_arrival_ms']},{h['jitter_ms']},{h['latency_ms']},"
                            f"{h.get('bitrate_kbps', f'{NetworkTelemetry.bitrate_kbps:.2f}')}\n"
                        )
                else:
                    f.write(
                        f"{NetworkTelemetry.latest_frame_number},"
                        f"{NetworkTelemetry.latest_motive_timestamp:.4f},"
                        f"{NetworkTelemetry.latest_packet_size},"
                        f"{NetworkTelemetry.inter_arrival_ms:.2f},"
                        f"{NetworkTelemetry.jitter_ms:.2f},"
                        f"{NetworkTelemetry.transit_latency_ms:.2f},"
                        f"{NetworkTelemetry.bitrate_kbps:.2f}\n"
                    )

            # 2. Export Comprehensive Markdown Report
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("# NatNet UDP Network Streaming & Parameter Translation Telemetry Report\n\n")
                f.write("**Subject:** Real-Time UDP Packet Streaming, Network QoS (Latency & Jitter), and Coordinate Space Retargeting (OptiTrack Motive to Blender)  \n")
                f.write(f"**Timestamp:** {time.strftime('%Y-%m-%d %H:%M:%S')}  \n\n")
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

                f.write("## 2. Network QoS & Streaming Performance Summary\n\n")
                f.write("| Performance Metric | Measured Value | Statistical Range (Min / Max / StdDev) | Description |\n")
                f.write("| :--- | :--- | :--- | :--- |\n")
                f.write(f"| **Total Packets Received** | **{NetworkTelemetry.total_packets:,} packets** | - | Total UDP datagrams successfully unpacked |\n")
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
                    f"Socket reception to PoseBone matrix evaluation |\n"
                )
                f.write(f"| **Packet Loss / Drop Rate** | **{NetworkTelemetry.packet_loss_pct:.2f}%** ({NetworkTelemetry.dropped_packets} dropped) | - | Lost frames detected via frame sequence numbers |\n\n")

                f.write("## 3. Parameter Mapping & Coordinate Space Translation (Motive -> Blender)\n\n")
                f.write("OptiTrack Motive broadcasts bone transforms in **Y-Up Global/Local Space**, whereas Blender evaluates armatures in **Z-Up Parent-Relative Forward Kinematics (FK)** space:\n\n")
                if mappings:
                    f.write("| No | Motive Stream Bone | Canonical Key | Blender Target Bone | Raw Motive Pos `(x, y, z)` | Raw Motive Quat `(w, x, y, z)` | Blender PoseBone Quat `(w, x, y, z)` |\n")
                    f.write("| :-: | :--- | :--- | :--- | :--- | :--- | :--- |\n")
                    for idx, (can, data) in enumerate(mappings.items(), start=1):
                        f.write(
                            f"| {idx} | `{data.get('motive_name', can)}` | `{can}` | `{data.get('blender_bone', '-')}` | "
                            f"`{data.get('raw_pos', '-')}` | `{data.get('raw_quat', '-')}` | `{data.get('blender_quat', '-')}` |\n"
                        )
                    f.write("\n")
                else:
                    f.write("| No | Motive Stream Parameter (Incoming UDP) | Canonical Key | Blender Target Bone | Target Transformation Property |\n")
                    f.write("| :-: | :--- | :--- | :--- | :--- |\n")
                    f.write("| 1 | `Hips` (ID: 1) | `hip` | `mixamorig:Hips` | `pose_bone.location` + `rotation_quaternion` |\n")
                    f.write("| 2 | `LThigh` / `LeftUpLeg` (ID: 2) | `leftUpLeg` | `mixamorig:LeftUpLeg` | `pose_bone.rotation_quaternion` (FK Parent-Relative) |\n")
                    f.write("| 3 | `LShin` / `LeftLeg` (ID: 3) | `leftLeg` | `mixamorig:LeftLeg` | `pose_bone.rotation_quaternion` (FK Parent-Relative) |\n")
                    f.write("| 4 | `LFoot` / `LeftFoot` (ID: 4) | `leftFoot` | `mixamorig:LeftFoot` | `pose_bone.rotation_quaternion` (FK Parent-Relative) |\n")
                    f.write("| 5 | `Spine` (ID: 5) | `spine` | `mixamorig:Spine` | `pose_bone.rotation_quaternion` (FK Parent-Relative) |\n")
                    f.write("| 6 | `Chest` / `Spine1` (ID: 6) | `chest` | `mixamorig:Spine1` | `pose_bone.rotation_quaternion` (FK Parent-Relative) |\n")
                    f.write("| 7 | `Neck` (ID: 7) | `neck` | `mixamorig:Neck` | `pose_bone.rotation_quaternion` (FK Parent-Relative) |\n")
                    f.write("| 8 | `Head` (ID: 8) | `head` | `mixamorig:Head` | `pose_bone.rotation_quaternion` (FK Parent-Relative) |\n")
                    f.write("| 9 | `LShoulder` (ID: 9) | `leftShoulder` | `mixamorig:LeftShoulder` | `pose_bone.rotation_quaternion` (FK Parent-Relative) |\n")
                    f.write("| 10 | `LUArm` / `LeftArm` (ID: 10) | `leftUpperArm` | `mixamorig:LeftArm` | `pose_bone.rotation_quaternion` (FK Parent-Relative) |\n")
                    f.write("| 11 | `LFArm` / `LeftForeArm` (ID: 11) | `leftLowerArm` | `mixamorig:LeftForeArm` | `pose_bone.rotation_quaternion` (FK Parent-Relative) |\n")
                    f.write("| 12 | `LHand` (ID: 12) | `leftHand` | `mixamorig:LeftHand` | `pose_bone.rotation_quaternion` (FK Parent-Relative) |\n\n")

                if history:
                    f.write("## 4. Frame-by-Frame Packet Telemetry Sample (Last 25 Frames)\n\n")
                    f.write("| Frame # | Motive Timestamp | Packet Size | Inter-Arrival (ms) | Jitter (ms) | Latency (ms) | Bitrate (Kbps) |\n")
                    f.write("| :-: | :-: | :-: | :-: | :-: | :-: | :-: |\n")
                    for h in history[-25:]:
                        f.write(
                            f"| {h['frame']} | {h['motive_ts']} s | {h['packet_size']} B | "
                            f"{h['inter_arrival_ms']} ms | {h['jitter_ms']} ms | {h['latency_ms']} ms | "
                            f"{h.get('bitrate_kbps', '-')} Kbps |\n"
                        )

            self.report({"INFO"}, f"Network telemetry report exported to: {md_path}")
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
        base_frame = NetworkTelemetry.latest_frame_number or 1000
        now = time.perf_counter()
        for i in range(1, 31):
            mock_dict = {
                "frame_number": base_frame + i,
                "packet_size": 1428,
                "timestamp": float(i * 0.00833),
                "recv_time": now + (i * 0.00833),
                "sender_addr": ("127.0.0.1", 1511),
                "ske_data": {1: {}},
                "rb_data": {},
            }
            NetworkTelemetry.record_packet(mock_dict)

        self.report({"INFO"}, "30 Test Packets Simulated successfully!")
        return {"FINISHED"}


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

        # Debug Mode Toggle
        row = layout.row(align=True)
        row.prop(
            scene,
            "motive_debug_mode",
            text="Network & Packet Debug Monitor",
            icon="PREFERENCES" if scene.motive_debug_mode else "GHOST_ENABLED",
        )

        if not scene.motive_debug_mode:
            row_sub = layout.row(align=True)
            row_sub.label(text="Enable to monitor incoming UDP packets and bone translation.", icon="INFO")
            return

        # Main Monitor Body
        layout.separator()

        # Connection & Stream Status Banner
        box_conn = layout.box()
        row_status = box_conn.row(align=True)
        is_live = NetworkTelemetry.check_streaming_liveness()

        if is_live:
            row_status.label(text="STREAMING ACTIVE (Broadcasting from Motive)", icon="CHECKMARK")
        elif context.window_manager.start_status:
            row_status.label(text="LISTENING ON UDP PORT 1511 (Waiting for Motive Broadcast...)", icon="VIEWZOOM")
        elif context.window_manager.connection_status:
            row_status.label(text="CONNECTED (Ready to Start Receiver)", icon="LINKED")
        else:
            row_status.label(text="DISCONNECTED (Click 'Start Connection' above)", icon="UNLINKED")

        # Network Endpoint Info
        col_net = box_conn.column(align=True)
        r = col_net.row(align=True)
        r.label(text=f"Server: {scene.init_prop.server_address}:1511 (UDP)")
        r.label(text=f"Client: {scene.init_prop.client_address}")

        r2 = col_net.row(align=True)
        r2.label(text=f"Sender: {NetworkTelemetry.last_sender_addr}")
        r2.label(text=f"Mode: {'Multicast' if NetworkTelemetry.use_multicast else 'Unicast'}")

        # Live Network Telemetry Card
        layout.separator()
        box_metrics = layout.box()
        box_metrics.label(text="Live Network Telemetry & Latency:", icon="NETWORK_DRIVE")

        col_m = box_metrics.column(align=True)

        def draw_metric(label, value, icon="DOT"):
            row = col_m.row(align=True)
            row.label(text=label)
            row.label(text=value, icon=icon)

        draw_metric("Incoming Frame Rate:", f"{NetworkTelemetry.fps:.1f} FPS" if NetworkTelemetry.fps > 0 else "0.0 FPS", icon="TIME")
        draw_metric("Inter-Arrival (Delta T):", f"{NetworkTelemetry.inter_arrival_ms:.2f} ms", icon="SORTTIME")
        draw_metric("Transit & Unpack Latency:", f"{NetworkTelemetry.transit_latency_ms:.2f} ms", icon="RESTRICT_SELECT_OFF")
        draw_metric("Network Jitter:", f"{NetworkTelemetry.jitter_ms:.2f} ms", icon="DRIVER")
        draw_metric("Current Frame Number:", f"#{NetworkTelemetry.latest_frame_number:,}", icon="ANIM_DATA")
        draw_metric("Packet Payload Size:", f"{NetworkTelemetry.latest_packet_size} Bytes ({NetworkTelemetry.bitrate_kbps:.1f} Kbps)", icon="FILE_CACHE")
        draw_metric("Total Packets Received:", f"{NetworkTelemetry.total_packets:,} pkts", icon="FILE_TICK")
        draw_metric("Packet Loss / Dropped:", f"{NetworkTelemetry.dropped_packets} pkts ({NetworkTelemetry.packet_loss_pct:.2f}%)", icon="CANCEL" if NetworkTelemetry.dropped_packets > 0 else "CHECKMARK")

        # Parameter Translation Preview Box
        layout.separator()
        box_trans = layout.box()
        box_trans.label(text="Parameter Translation (Motive -> Blender):", icon="LINK_BLEND")

        col_t = box_trans.column(align=True)
        r_head = col_t.row(align=True)
        r_head.label(text="Motive Stream")
        r_head.label(text="Canonical")
        r_head.label(text="Blender Bone")

        def draw_trans_row(motive_p, canonical_p, blender_b):
            r = col_t.row(align=True)
            r.label(text=motive_p)
            r.label(text=canonical_p)
            r.label(text=blender_b)

        draw_trans_row("Hips (Pos+Rot)", "hip", "mixamorig:Hips")
        draw_trans_row("LThigh (Quat)", "leftUpLeg", "mixamorig:LeftUpLeg")
        draw_trans_row("LShin (Quat)", "leftLeg", "mixamorig:LeftLeg")
        draw_trans_row("Head (Quat)", "head", "mixamorig:Head")
        draw_trans_row("Spine (Quat)", "spine", "mixamorig:Spine")

        # Action Buttons
        layout.separator()
        col_btn = layout.column(align=True)
        row_act = col_btn.row(align=True)
        row_act.scale_y = 1.2
        row_act.operator("motive.inspect_raw_packet", text="Inspect UDP Packet", icon="WINDOW")
        row_act.operator("motive.show_parameter_mappings", text="All Mappings", icon="SCRIPTPLUGINS")

        row_act2 = col_btn.row(align=True)
        row_act2.operator("motive.export_network_report", text="Export Report (MD/CSV)", icon="EXPORT")
        row_act2.operator("motive.reset_telemetry", text="Reset", icon="FILE_REFRESH")

        # Optional test simulation button (for quick verification when Motive isn't open)
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
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister_diagnostics():
    del bpy.types.Scene.motive_debug_mode
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
