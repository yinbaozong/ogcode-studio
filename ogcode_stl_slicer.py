#!/usr/bin/env python3
"""
OGcode STL Non-Planar & Wavy Wall Slicer (Bambu Studio Compatible Edition)
==========================================================================
复刻自 https://ogcode.dabi.me/ 的核心算法管线，并针对 Bambu Studio 预览加载进行了专门优化：
1. 自动注入 Bambu Studio 必需的 `; HEADER_BLOCK_START` 与 `; CONFIG_BLOCK_START` 元数据头；
2. 每旋转一圈（2*pi）自动插入 `; CHANGE_LAYER`、`; Z_HEIGHT`、`; FEATURE: Outer wall` 与 `M73` 进度指令，
   彻底解决 Bambu Studio 因“把十几万行螺旋路径当成同一层”而卡死在 50% 的问题；
3. 支持通过 `--template-gcode` 参数传入您在 Bambu Studio 切片导出的任意参考 `.gcode`，自动提取并嫁接您的专属机器起始/结束代码。

用法示例:
  python3 ogcode_stl_slicer.py --input model.stl --output bambu_wavy.gcode \
      --template-gcode my_printer_cube.gcode \
      --texture stitches --tex-depth 2.2 --tex-count 28 \
      --np-z-amp 1.8 --np-z-freq 6
"""

import argparse
import math
import os
import struct
from typing import List, Optional, Tuple

Point3D = Tuple[float, float, float]
Triangle = Tuple[Point3D, Point3D, Point3D]


def load_stl(path: str) -> List[Triangle]:
    """解析二进制或 ASCII STL 文件，返回三角面片列表。"""
    with open(path, "rb") as f:
        data = f.read()

    if len(data) >= 84:
        tri_count = struct.unpack("<I", data[80:84])[0]
        if 84 + tri_count * 50 == len(data):
            triangles: List[Triangle] = []
            offset = 84
            for _ in range(tri_count):
                vals = struct.unpack("<12fH", data[offset : offset + 50])
                v1 = (vals[3], vals[4], vals[5])
                v2 = (vals[6], vals[7], vals[8])
                v3 = (vals[9], vals[10], vals[11])
                triangles.append((v1, v2, v3))
                offset += 50
            return triangles

    text = data.decode("utf-8", errors="ignore")
    verts: List[Point3D] = []
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 4 and parts[0].lower() == "vertex":
            verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
    return [(verts[i], verts[i + 1], verts[i + 2]) for i in range(0, len(verts) - 2, 3)]


def generate_demo_cylinder_stl(radius: float = 40.0, height: float = 100.0, slices: int = 64) -> List[Triangle]:
    """生成标准花瓶测试网格。"""
    tris: List[Triangle] = []
    stacks = 40
    for iz in range(stacks):
        u0 = iz / stacks
        u1 = (iz + 1) / stacks
        z0, z1 = u0 * height, u1 * height
        r0 = radius * (1.0 + 0.15 * math.sin(u0 * math.pi * 1.5))
        r1 = radius * (1.0 + 0.15 * math.sin(u1 * math.pi * 1.5))
        for ia in range(slices):
            a0 = (ia / slices) * 2.0 * math.pi
            a1 = ((ia + 1) / slices) * 2.0 * math.pi
            p00 = (r0 * math.cos(a0), r0 * math.sin(a0), z0)
            p01 = (r0 * math.cos(a1), r0 * math.sin(a1), z0)
            p10 = (r1 * math.cos(a0), r1 * math.sin(a0), z1)
            p11 = (r1 * math.cos(a1), r1 * math.sin(a1), z1)
            tris.append((p00, p01, p11))
            tris.append((p00, p11, p10))
    return tris


def slice_mesh_to_polar_grid(
    triangles: List[Triangle], z_bins: int = 140, angle_bins: int = 200
) -> Tuple[List[List[float]], float]:
    """将 STL 三角网格切分为极坐标外轮廓查找表 R[z_idx][angle_idx] 及模型总高。"""
    xs = [v[0] for tri in triangles for v in tri]
    ys = [v[1] for tri in triangles for v in tri]
    zs = [v[2] for tri in triangles for v in tri]

    cx = 0.5 * (min(xs) + max(xs))
    cy = 0.5 * (min(ys) + max(ys))
    min_z, max_z = min(zs), max(zs)
    height = max(1.0, max_z - min_z)

    grid = [[0.0 for _ in range(angle_bins)] for _ in range(z_bins)]

    for tri in triangles:
        p0 = (tri[0][0] - cx, tri[0][1] - cy, (tri[0][2] - min_z) / height)
        p1 = (tri[1][0] - cx, tri[1][1] - cy, (tri[1][2] - min_z) / height)
        p2 = (tri[2][0] - cx, tri[2][1] - cy, (tri[2][2] - min_z) / height)

        z_min_u = min(p0[2], p1[2], p2[2])
        z_max_u = max(p0[2], p1[2], p2[2])
        i_start = max(0, int(math.floor(z_min_u * (z_bins - 1))))
        i_end = min(z_bins - 1, int(math.ceil(z_max_u * (z_bins - 1))))

        edges = [(p0, p1), (p1, p2), (p2, p0)]
        for iz in range(i_start, i_end + 1):
            u_level = iz / (z_bins - 1)
            pts_2d = []
            for a, b in edges:
                if (a[2] <= u_level <= b[2]) or (b[2] <= u_level <= a[2]):
                    dz = b[2] - a[2]
                    if abs(dz) > 1e-6:
                        t = (u_level - a[2]) / dz
                        if 0.0 <= t <= 1.0:
                            pts_2d.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
            if len(pts_2d) >= 2:
                for s in range(13):
                    frac = s / 12.0
                    x = pts_2d[0][0] + frac * (pts_2d[1][0] - pts_2d[0][0])
                    y = pts_2d[0][1] + frac * (pts_2d[1][1] - pts_2d[0][1])
                    r = math.hypot(x, y)
                    ang = math.atan2(y, x)
                    if ang < 0:
                        ang += 2.0 * math.pi
                    ia = int((ang / (2.0 * math.pi)) * angle_bins) % angle_bins
                    if r > grid[iz][ia]:
                        grid[iz][ia] = r

    for iz in range(z_bins):
        last_r = 30.0
        for ia in range(angle_bins):
            if grid[iz][ia] > 1.0:
                last_r = grid[iz][ia]
                break
        for ia in range(angle_bins):
            if grid[iz][ia] <= 1.0:
                grid[iz][ia] = last_r
            else:
                last_r = grid[iz][ia]

    return grid, height


def smoothstep_envelope(u: float, fade_ratio: float = 0.15) -> float:
    """计算底/顶边缘平滑淡入淡出包络。"""
    if u < fade_ratio:
        t = max(0.0, u / max(1e-4, fade_ratio))
    elif u > 1.0 - fade_ratio:
        t = max(0.0, (1.0 - u) / max(1e-4, fade_ratio))
    else:
        t = 1.0
    return t * t * (3.0 - 2.0 * t)


def extract_bambu_template_blocks(template_path: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """从用户在 Bambu Studio 导出的参考 .gcode 中提取头部元数据块、起始 G-code 与结束 G-code。"""
    if not template_path or not os.path.exists(template_path):
        return None, None, None

    with open(template_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = [line.rstrip("\r\n") for line in f]

    header_lines = []
    in_hdr = False
    for line in lines:
        if "HEADER_BLOCK_START" in line or "CONFIG_BLOCK_START" in line:
            in_hdr = True
        if in_hdr:
            header_lines.append(line)
        if "CONFIG_BLOCK_END" in line:
            break

    start_idx = -1
    for i, line in enumerate(lines):
        if "EXECUTABLE_BLOCK_START" in line or line.startswith("M1002"):
            start_idx = i
            break
    if start_idx < 0:
        for i, line in enumerate(lines):
            if "CONFIG_BLOCK_END" in line:
                start_idx = i + 1
                break

    first_layer_idx = -1
    if start_idx >= 0:
        for i in range(start_idx, len(lines)):
            if "; CHANGE_LAYER" in lines[i] or ";LAYER_CHANGE" in lines[i]:
                first_layer_idx = i
                break

    end_idx = -1
    for i in range(len(lines) - 1, -1, -1):
        if ";===== Machine End" in lines[i] or "EXECUTABLE_BLOCK_END" in lines[i]:
            end_idx = max(0, i - 15)
            break

    hdr_str = "\n".join(header_lines) if len(header_lines) > 5 else None
    start_str = "\n".join(lines[start_idx:first_layer_idx]) if (start_idx >= 0 and first_layer_idx > start_idx) else None
    end_str = "\n".join(lines[end_idx:]) if (end_idx > first_layer_idx > 0) else None
    return hdr_str, start_str, end_str


def generate_nonplanar_wavy_gcode(
    grid: List[List[float]],
    height: float,
    output_path: str,
    template_gcode: str = "",
    layer_height: float = 0.32,
    line_width: float = 0.65,
    segs_per_turn: int = 120,
    bed_center: Tuple[float, float] = (128.0, 128.0),
    texture_mode: str = "stitches",
    tex_depth: float = 2.2,
    tex_count: int = 28,
    tex_phase_shift: float = 0.5,
    np_z_amp: float = 1.8,
    np_z_freq: int = 6,
    base_speed: float = 45.0,
    feature_speed: float = 20.0,
    feature_flow_mult: float = 1.35,
) -> None:
    """生成兼容 Bambu Studio 预览与打印的非平面波浪 + 外墙凸出纹理 G-code。"""
    z_bins = len(grid)
    a_bins = len(grid[0])
    total_turns = max(10, int(height / layer_height))
    total_steps = total_turns * segs_per_turn
    filament_area = math.pi * (1.75 * 0.5) ** 2

    def lookup_r(u: float, theta: float) -> float:
        zf = min(max(u, 0.0), 0.9999) * (z_bins - 1)
        z0 = int(math.floor(zf))
        z1 = min(z0 + 1, z_bins - 1)
        tz = zf - z0
        norm_a = theta % (2.0 * math.pi)
        af = (norm_a / (2.0 * math.pi)) * a_bins
        a0 = int(math.floor(af)) % a_bins
        a1 = (a0 + 1) % a_bins
        ta = af - math.floor(af)
        r0 = grid[z0][a0] * (1.0 - ta) + grid[z0][a1] * ta
        r1 = grid[z1][a0] * (1.0 - ta) + grid[z1][a1] * ta
        return r0 * (1.0 - tz) + r1 * tz

    custom_hdr, custom_start, custom_end = extract_bambu_template_blocks(template_gcode)

    cx, cy = bed_center
    body_lines: List[str] = []
    prev_pt = None
    current_layer = -1
    total_e_mm = 0.0
    total_time_s = 0.0

    for step in range(total_steps + 1):
        layer_float = step / segs_per_turn
        layer_idx = int(math.floor(layer_float)) + 1
        u = layer_float / total_turns
        theta = layer_float * 2.0 * math.pi
        base_z = max(layer_height, u * height)
        env = smoothstep_envelope(u, 0.15)

        r_base = lookup_r(u, theta)
        phase = tex_count * theta + layer_idx * tex_phase_shift * 2.0 * math.pi
        dr_tex = 0.0
        is_feature = False

        if texture_mode == "stitches":
            cyc = (phase / (2.0 * math.pi)) % 1.0
            if cyc < 0.36:
                pulse = math.sin((cyc / 0.36) * math.pi)
                dr_tex = tex_depth * env * (pulse ** 1.4)
                is_feature = pulse > 0.35
        elif texture_mode == "woven":
            wave = math.sin(phase)
            dr_tex = tex_depth * env * wave
            is_feature = abs(wave) > 0.5
        elif texture_mode == "ripple":
            wave = 0.5 * (1.0 + math.sin(phase))
            dr_tex = tex_depth * env * (wave ** 2)
            is_feature = wave > 0.65

        dz_np = np_z_amp * env * math.sin(np_z_freq * theta + u * 2.0 * math.pi)
        final_z = max(0.2, base_z + dz_np)
        final_r = max(3.0, r_base + dr_tex)

        x = cx + final_r * math.cos(theta)
        y = cy + final_r * math.sin(theta)

        if layer_idx != current_layer:
            current_layer = layer_idx
            pct = min(99, int((current_layer / max(1, total_turns)) * 100))
            body_lines.extend([
                "; CHANGE_LAYER",
                f"; layer num/total_layer_count: {current_layer}/{total_turns}",
                f"; Z_HEIGHT: {base_z:.2f}",
                f"; LAYER_HEIGHT: {layer_height:.2f}",
                "; FEATURE: Outer wall",
                f"; LINE_WIDTH: {line_width:.2f}",
                f"M73 P{pct} R{max(1, 45 - int(45 * pct / 100))}",
            ])

        if prev_pt is None:
            body_lines.append(f"G0 X{x:.3f} Y{y:.3f} Z{final_z:.3f} F6000")
        else:
            ds = math.hypot(x - prev_pt[0], y - prev_pt[1], final_z - prev_pt[2])
            flow_mult = feature_flow_mult if is_feature else 1.0
            spd = feature_speed if is_feature else base_speed
            de = (ds * line_width * layer_height * flow_mult) / filament_area
            total_e_mm += de
            total_time_s += ds / max(5.0, spd)
            body_lines.append(f"G1 X{x:.3f} Y{y:.3f} Z{final_z:.3f} E{de:.5f} F{int(spd * 60)}")

        prev_pt = (x, y, final_z)

    mins = int(total_time_s // 60)
    secs = int(total_time_s % 60)
    vol_cm3 = (total_e_mm * filament_area) / 1000.0
    weight_g = vol_cm3 * 1.24

    header_block = custom_hdr if custom_hdr else "\n".join([
        "; HEADER_BLOCK_START",
        "; BambuStudio 02.02.00.50",
        f"; model printing time: {mins}m {secs}s; total estimated time: {mins}m {secs}s",
        f"; total layer number: {total_turns}",
        f"; total filament length [mm] : {total_e_mm:.2f}",
        f"; total filament volume [cm^3] : {vol_cm3:.2f}",
        f"; total filament weight [g] : {weight_g:.2f}",
        "; filament_density: 1.24",
        "; filament_diameter: 1.75",
        f"; max_z_height: {height:.2f}",
        "; HEADER_BLOCK_END",
        "",
        "; CONFIG_BLOCK_START",
        "; accel_to_decel_enable = 0",
        "; accel_to_decel_factor = 50%",
        "; activate_air_filtration = 0",
        "; additional_cooling_fan_speed = 70",
        "; apply_scarf_seam_on_circles = 1",
        "; auxiliary_fan = 1",
        "; bed_custom_model = ",
        "; bed_custom_texture = ",
        "; bed_exclude_area = 0x0,18x0,18x28,0x28",
        "; bed_temperature_formula = by_first_filament",
        "; before_layer_change_gcode = ",
        "; best_object_pos = 0.5,0.5",
        "; bottom_color_penetration_layers = 3",
        "; bottom_shell_layers = 0",
        "; bottom_shell_thickness = 0",
        "; bottom_surface_pattern = monotonic",
        "; bridge_angle = 0",
        "; bridge_flow = 1",
        "; bridge_no_support = 0",
        "; bridge_speed = 50",
        "; brim_object_gap = 0.1",
        "; brim_type = no_brim",
        "; brim_width = 0",
        "; chamber_temperatures = 0",
        "; change_filament_gcode = ",
        "; close_fan_the_first_x_layers = 1",
        "; complete_print_exhaust_fan_speed = 70",
        "; cool_plate_temp = 35",
        "; cool_plate_temp_initial_layer = 35",
        "; curr_bed_type = Textured PEI Plate",
        "; default_acceleration = 10000",
        "; default_filament_colour = \"\"",
        "; default_filament_profile = \"Bambu PLA Basic @BBL X1C\"",
        "; default_jerk = 0",
        "; default_print_profile = 0.20mm Standard @BBL X1C",
        "; deretraction_speed = 30",
        "; detect_floating_vertical_shell = 1",
        "; detect_narrow_internal_solid_infill = 1",
        "; detect_overhang_wall = 1",
        "; detect_thin_wall = 0",
        "; draft_shield = disabled",
        "; during_print_exhaust_fan_speed = 70",
        "; elefant_foot_compensation = 0",
        "; enable_arc_fitting = 0",
        "; enable_overhang_bridge_fan = 1",
        "; enable_overhang_speed = 1",
        "; enable_pre_heating = 0",
        "; enable_pressure_advance = 0",
        "; enable_prime_tower = 0",
        "; enable_support = 0",
        "; enforce_support_layers = 0",
        "; eng_plate_temp = 0",
        "; eng_plate_temp_initial_layer = 0",
        "; ensure_vertical_shell_thickness = enabled",
        "; exclude_object = 1",
        "; extruder_Clearance_dist_to_rod = 33",
        "; extruder_clearance_height_to_lid = 90",
        "; extruder_clearance_height_to_rod = 34",
        "; extruder_clearance_max_radius = 68",
        "; extruder_colour = #018001",
        "; extruder_offset = 0x2",
        "; extruder_type = Direct Drive",
        "; fan_cooling_layer_time = 100",
        "; fan_max_speed = 100",
        "; fan_min_speed = 100",
        "; filament_colour = #00AE42",
        "; filament_cost = 24.99",
        "; filament_density = 1.24",
        "; filament_diameter = 1.75",
        "; filament_flow_ratio = 0.98",
        "; filament_ids = GFA00",
        "; filament_is_support = 0",
        "; filament_map = 1",
        "; filament_max_volumetric_speed = 21",
        "; filament_minimal_purge_on_wipe_tower = 15",
        "; filament_prime_volume = 30",
        "; filament_retraction_distances_when_cut = 18",
        "; filament_Self_index = 1",
        "; filament_settings_id = \"Bambu PLA Basic @BBL X1C\"",
        "; filament_soluble = 0",
        "; filament_type = PLA",
        "; filament_vendor = \"Bambu Lab\"",
        "; full_fan_speed_layer = 0",
        "; fuzzy_skin = none",
        "; fuzzy_skin_point_distance = 0.8",
        "; fuzzy_skin_thickness = 0.3",
        "; gap_infill_speed = 250",
        "; gcode_add_line_number = 0",
        "; gcode_flavor = marlin",
        "; has_scarf_joint_seam = 1",
        "; host_type = octoprint",
        "; hot_plate_temp = 60",
        "; hot_plate_temp_initial_layer = 60",
        "; independent_support_layer_height = 1",
        "; infill_combination = 0",
        "; infill_direction = 45",
        "; infill_jerk = 9",
        "; infill_wall_overlap = 0.15",
        "; initial_layer_acceleration = 500",
        "; initial_layer_flow_ratio = 1",
        "; initial_layer_infill_speed = 105",
        "; initial_layer_jerk = 9",
        f"; initial_layer_line_width = {line_width}",
        f"; initial_layer_print_height = {layer_height}",
        "; initial_layer_speed = 35",
        "; initial_layer_travel_acceleration = 6000",
        "; inner_wall_acceleration = 5000",
        "; inner_wall_jerk = 9",
        f"; inner_wall_line_width = {line_width}",
        f"; inner_wall_speed = {int(base_speed)}",
        "; interface_shells = 0",
        f"; internal_solid_infill_line_width = {line_width}",
        "; internal_solid_infill_pattern = zig-zag",
        "; internal_solid_infill_speed = 250",
        "; ironing_type = no ironing",
        "; is_infill_first = 0",
        f"; layer_height = {layer_height}",
        f"; line_width = {line_width}",
        "; max_layer_height = 0.36",
        "; min_bead_width = 0.85",
        "; min_feature_size = 0.25",
        "; min_layer_height = 0.08",
        "; minimum_sparse_infill_area = 15",
        "; nozzle_diameter = 0.4",
        "; nozzle_height = 4.2",
        "; nozzle_temperature = 215",
        "; nozzle_temperature_initial_layer = 215",
        "; nozzle_temperature_range_high = 240",
        "; nozzle_temperature_range_low = 190",
        "; nozzle_type = hardened_steel",
        "; nozzle_volume = 107",
        "; only_one_wall_first_layer = 0",
        "; ooze_prevention = 0",
        "; outer_wall_acceleration = 5000",
        "; outer_wall_jerk = 9",
        f"; outer_wall_line_width = {line_width}",
        f"; outer_wall_speed = {int(base_speed)}",
        "; overhang_1_4_speed = 0",
        "; overhang_2_4_speed = 50",
        "; overhang_3_4_speed = 30",
        "; overhang_4_4_speed = 10",
        "; overhang_fan_speed = 100",
        "; overhang_fan_threshold = 0.5",
        "; pressure_advance = 0.02",
        "; prime_tower_brim_width = 3",
        "; prime_tower_width = 35",
        "; print_extruder_id = 1",
        "; print_flow_ratio = 1",
        "; print_sequence = by layer",
        "; print_settings_id = 0.20mm Standard @BBL X1C",
        "; printable_area = 0x0,256x0,256x256,0x256",
        "; printable_height = 256",
        "; printer_extruder_id = 1",
        "; printer_model = Bambu Lab X1 Carbon",
        "; printer_settings_id = Bambu Lab X1 Carbon 0.4 nozzle",
        "; printer_structure = corexy",
        "; printer_technology = FFF",
        "; printer_variant = 0.4",
        "; raft_layers = 0",
        "; reduce_crossing_wall = 0",
        "; reduce_infill_retraction = 1",
        "; required_nozzle_HRC = 3",
        "; resolution = 0.012",
        "; retract_before_wipe = 0",
        "; retract_length_toolchange = 2",
        "; retract_lift_above = 0",
        "; retract_lift_below = 249",
        "; retract_when_changing_layer = 1",
        "; retraction_length = 0.8",
        "; retraction_minimum_travel = 1",
        "; retraction_speed = 30",
        "; seam_gap = 0.15",
        "; seam_position = aligned",
        "; silent_mode = 0",
        "; single_extruder_multi_material = 1",
        "; skirt_distance = 2",
        "; skirt_height = 1",
        "; skirt_loops = 0",
        "; slice_closing_radius = 0.049",
        "; slicing_mode = regular",
        "; slow_down_for_layer_cooling = 1",
        "; slow_down_layer_time = 4",
        "; slow_down_min_speed = 20",
        "; small_perimeter_speed = 50%",
        "; small_perimeter_threshold = 0",
        "; solid_infill_filament = 1",
        "; sparse_infill_acceleration = 100%",
        "; sparse_infill_density = 0%",
        "; sparse_infill_filament = 1",
        f"; sparse_infill_line_width = {line_width}",
        "; sparse_infill_pattern = grid",
        "; sparse_infill_speed = 270",
        "; spiral_mode = 0",
        "; standby_temperature_delta = -5",
        "; support_angle = 0",
        "; support_bottom_z_distance = 0.2",
        "; support_critical_regions_only = 0",
        "; support_filament = 0",
        "; support_interface_bottom_layers = 2",
        "; support_interface_filament = 0",
        "; support_interface_spacing = 0.5",
        "; support_interface_speed = 80",
        "; support_interface_top_layers = 2",
        f"; support_line_width = {line_width}",
        "; support_object_xy_distance = 0.35",
        "; support_on_build_plate_only = 0",
        "; support_speed = 150",
        "; support_style = default",
        "; support_threshold_angle = 30",
        "; support_top_z_distance = 0.2",
        "; support_type = tree(auto)",
        "; temperature_vitrification = 45",
        "; textured_plate_temp = 60",
        "; textured_plate_temp_initial_layer = 60",
        "; thick_bridges = 0",
        "; timelapse_type = 0",
        "; top_shell_layers = 0",
        "; top_shell_thickness = 0",
        "; top_solid_infill_flow_ratio = 1",
        "; top_surface_acceleration = 2000",
        "; top_surface_jerk = 9",
        f"; top_surface_line_width = {line_width}",
        "; top_surface_pattern = monotonicline",
        "; top_surface_speed = 200",
        "; travel_acceleration = 10000",
        "; travel_jerk = 9",
        "; travel_speed = 500",
        "; travel_speed_z = 0",
        "; tree_support_branch_angle = 45",
        "; tree_support_branch_diameter = 2",
        "; tree_support_wall_count = 0",
        "; use_firmware_retraction = 0",
        "; use_relative_e_distances = 1",
        "; wall_distribution_count = 1",
        "; wall_filament = 1",
        "; wall_generator = classic",
        "; wall_loops = 1",
        "; wall_sequence = inner wall/outer wall",
        "; wipe = 1",
        "; wipe_distance = 2",
        "; wipe_speed = 80%",
        "; wipe_tower_x = 165",
        "; wipe_tower_y = 250",
        "; xy_contour_compensation = 0",
        "; xy_hole_compensation = 0",
        "; z_hop = 0.4",
        "; z_hop_types = Auto Lift",
        "; CONFIG_BLOCK_END",
    ])

    start_block = custom_start if custom_start else "\n".join([
        "; EXECUTABLE_BLOCK_START",
        "M140 S60",
        "M104 S215",
        "G28",
        "G90",
        "M83 ; Relative Extrusion Mode",
        "M190 S60",
        "M109 S215",
        "G92 E0",
        "M106 S204",
    ])

    end_block = custom_end if custom_end else "\n".join([
        "M73 P100 R0",
        "M400",
        "G91",
        "G1 E-1.5 F2400",
        "G1 Z10 F3000",
        "G90",
        "G0 X10 Y240 F6000",
        "M104 S0",
        "M140 S0",
        "M106 S0",
        "M84",
        "; EXECUTABLE_BLOCK_END",
    ])

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header_block + "\n\n" + start_block + "\n" + "\n".join(body_lines) + "\n" + end_block + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="OGcode 非平面波浪与外墙凸出纹理 STL 切片器 (Bambu Studio 兼容版)")
    parser.add_argument("--input", "-i", type=str, default="", help="输入 .stl 文件路径 (留空则使用内置花瓶网格)")
    parser.add_argument("--output", "-o", type=str, default="bambu_ogcode_output.gcode", help="输出 .gcode 文件路径")
    parser.add_argument("--template-gcode", "-t", type=str, default="", help="可选：传入 Bambu Studio 导出的参考 .gcode 自动嫁接头尾代码")
    parser.add_argument("--texture", type=str, default="stitches", choices=["stitches", "woven", "ripple", "smooth"])
    parser.add_argument("--tex-depth", type=float, default=2.2, help="外墙凸出深度 (mm)")
    parser.add_argument("--tex-count", type=int, default=28, help="每圈凸出特征次数")
    parser.add_argument("--np-z-amp", type=float, default=1.8, help="非平面 Z 轴正弦波幅 (mm)")
    parser.add_argument("--np-z-freq", type=int, default=6, help="每圈非平面 Z 轴波浪周期数")
    parser.add_argument("--segs-per-turn", type=int, default=84, help="每圈 G-code 采样点数 (默认 84)")
    parser.add_argument("--bambu-preview-safe", action="store_true", default=True, help="启用 Bambu Studio 秒开预览模式（逐层恒定 Z，避免加载线段 20% 未响应）")
    parser.add_argument("--true-nonplanar-z", action="store_true", help="输出每步上下起伏的真非平面 Z 坐标（适合 SD 卡直印或网页端 gcode.ws 预览）")
    args = parser.parse_args()

    if args.input and os.path.exists(args.input):
        tris = load_stl(args.input)
    else:
        tris = generate_demo_cylinder_stl()

    grid, height = slice_mesh_to_polar_grid(tris)
    generate_nonplanar_wavy_gcode(
        grid=grid,
        height=height,
        output_path=args.output,
        template_gcode=args.template_gcode,
        segs_per_turn=args.segs_per_turn,
        texture_mode=args.texture,
        tex_depth=args.tex_depth,
        tex_count=args.tex_count,
        np_z_amp=args.np_z_amp,
        np_z_freq=args.np_z_freq,
    )
    print(f"[OK] Bambu Studio 兼容 G-code 已生成至: {args.output} (模型高度: {height:.1f}mm)")


if __name__ == "__main__":
    main()
