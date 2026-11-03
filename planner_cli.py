#!/usr/bin/env python3
"""命令行版：截图 -> 识别打卡点 -> 两点地标配准 -> 4km 路线 -> .route.json

用法示例（Windows / Termux 通用）：
  python planner_cli.py --screenshot shot.jpg \
      --landmark "图书馆:223,997" --landmark "体育场:704,1912" \
      --out out

  # 仅识别打卡点标记：
  python planner_cli.py --screenshot shot.jpg --detect-only

  # 查看可用地标：
  python planner_cli.py --list-landmarks
"""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.profile import Profile
from core.detect import detect_markers
from core.calib import calibrate, checkpoints_from_markers
from core import planner, routejson, geo


def load_image(path):
    im = Image.open(path).convert("RGB")
    return np.asarray(im), im


def parse_landmarks(items):
    out = []
    for it in items:
        name, xy = it.rsplit(":", 1)
        x, y = xy.split(",")
        out.append((name.strip(), (float(x), float(y))))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="校园跑 4km 路线规划器（桂林航天工业学院南校区）")
    ap.add_argument("--screenshot", "-s", help="App 截图路径")
    ap.add_argument("--landmark", "-l", action="append", default=[],
                    help='地标点选，格式 "地标名:像素x,像素y"，至少 2 个')
    ap.add_argument("--target", type=float, default=None, help="目标里程（米）；不填则自动选择")
    ap.add_argument("--km", type=float, default=None,
                    help="目标里程（公里），如 4 / 3.5；不填则自动选择")
    ap.add_argument("--list-km", action="store_true",
                    help="只计算本校区可规划的公里数列表（不生成路线）")
    ap.add_argument("--points", type=int, default=150, help="路线总点数（70~200，默认 150）")
    ap.add_argument("--marker", "-m", action="append", default=[],
                    help='手动补点（当点位被遮挡/显示成对勾时用），格式 "序号:像素x,像素y"，如 "1:420,1113"')
    ap.add_argument("--start", default=None,
                    help="路线起点：地标名 或 'lon,lat'（不填则从 1 号点附近开始）")
    ap.add_argument("--out", default="out", help="输出目录")
    ap.add_argument("--detect-only", action="store_true", help="只识别打卡点标记")
    ap.add_argument("--list-landmarks", action="store_true", help="列出可用地标")
    ap.add_argument("--profile", default=None, help="自定义校区档案 json")
    ap.add_argument("--passes", type=int, default=1,
                    help="每个打卡点绕圈圈数（0=不绕，默认 1，让路线在点位附近反复经过，避免打不上卡）")
    ap.add_argument("--pass-radius", type=float, default=12.0,
                    help="绕点半径（米，默认 12）")
    ap.add_argument("--anchor-dy", type=float, default=None,
                    help="打卡点锚点向下偏移（1080 宽截图坐标，默认读 profile 的 30）")
    ap.add_argument("--anchor-dx", type=float, default=None,
                    help="打卡点锚点横向偏移（默认 0）")
    ap.add_argument("--sat", action="store_true", help="同时生成卫星底图预览图")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    profile = Profile(args.profile)

    if args.list_landmarks:
        print(f"校区：{profile.data['name']}")
        for l in profile.landmarks:
            print(f"  {l['name']}  GCJ=({l['lon']:.6f}, {l['lat']:.6f})")
        return 0

    if not args.screenshot:
        ap.error("需要 --screenshot")

    img, _ = load_image(args.screenshot)
    anchor = dict(profile.marker_anchor)
    if args.anchor_dx is not None:
        anchor["dx"] = args.anchor_dx
    if args.anchor_dy is not None:
        anchor["dy"] = args.anchor_dy
    markers = detect_markers(img, profile.templates, profile.marker_colors, anchor=anchor)
    # 合并手动补点（手动优先）
    manual = []
    from PIL import Image as _PILImage
    _w = _PILImage.open(args.screenshot).size[0]
    _sc = _w / float(anchor.get("ref_width", 1080))
    _adx = float(anchor.get("dx", 0.0)) * _sc
    _ady = float(anchor.get("dy", 0.0)) * _sc
    for it in args.marker:
        dg, xy = it.split(":", 1)
        x, y = xy.split(",")
        manual.append(dict(px=float(x), py=float(y), ax=float(x) + _adx, ay=float(y) + _ady,
                           digit=int(dg), score=1.0, color="manual"))
    if manual:
        by = {m["digit"]: m for m in markers}
        for m in manual:
            by[m["digit"]] = m
        markers = [by[k] for k in sorted(by)]
    print(f"[detect] 识别到 {len(markers)} 个打卡点标记：")
    for m in markers:
        print(f"   {m['digit']} 号: 截图像素({m['px']:.0f},{m['py']:.0f})  置信度 {m['score']:.2f}  [{m['color']}]")

    if args.detect_only:
        return 0

    digits = sorted(m["digit"] for m in markers)
    if digits != [1, 2, 3, 4, 5]:
        print("[error] 未能完整识别 5 个打卡点（可能被弹窗/提示气泡遮挡）。"
              "请重新截图，或用 Windows 图形界面手动补点。")
        return 2

    clicks = parse_landmarks(args.landmark)
    if len(clicks) < 2:
        print("[error] 至少需要 2 个地标点，例如："
              '--landmark "图书馆:223,997" --landmark "体育场:704,1912"')
        print("        可先用 --list-landmarks 查看地标清单")
        return 2

    calib = calibrate(profile, clicks, verbose=not args.quiet)
    cps = checkpoints_from_markers(profile, calib, markers)
    print("[cp] 打卡点 GCJ 坐标：")
    for c in cps:
        print(f"   {c['digit']} 号: {c['lon']:.7f}, {c['lat']:.7f}")

    start_gcj = None
    if args.start:
        lm = {l["name"]: l for l in profile.landmarks}
        if args.start in lm:
            start_gcj = (lm[args.start]["lon"], lm[args.start]["lat"])
        else:
            lon_s, lat_s = args.start.split(",")
            start_gcj = (float(lon_s), float(lat_s))
    points = max(70, min(200, int(args.points)))
    if args.list_km:
        res = planner.plan(profile, [(c["lon"], c["lat"]) for c in cps],
                           auto=True, start_gcj=start_gcj, target_points=150,
                           pass_laps=max(0, args.passes), pass_radius_m=args.pass_radius,
                           log=lambda *a: None)
        print("可规划公里数: " + " / ".join(f"{k:g}" for k in res["available_km"]) + " km")
        print("默认(自动): %.1f km" % (res["target_m"] / 1000.0))
        return 0

    target_m = args.target
    if args.km is not None:
        target_m = args.km * 1000.0
    auto = target_m is None
    result = planner.plan(profile, [(c["lon"], c["lat"]) for c in cps],
                          target_m=target_m, auto=auto, start_gcj=start_gcj,
                          target_points=points,
                          pass_laps=max(0, args.passes), pass_radius_m=args.pass_radius,
                          log=print if not args.quiet else (lambda *a: None))
    if result.get("available_km"):
        print("[km] 本校区可规划: " + " / ".join(f"{k:g}" for k in result["available_km"]) + " km")
    length = result["length_m"]
    print(f"[route] 实际里程 {length:.1f} m，目标 {result['target_m']/1000:.1f} km，"
          f"点数 {result['point_count']}（要求 70~200），打卡点精确经过（0 m）")

    os.makedirs(args.out, exist_ok=True)
    route = routejson.build_route_dict(result["route_wgs"])
    route_path = os.path.join(args.out, route["name"] + ".route.json")
    routejson.save_route(route, route_path)
    preview_path = os.path.join(args.out, route["name"] + "_preview.png")
    routejson.render_preview(profile, result["route_ds"], result["checkpoints"], preview_path,
                             title=f"校园跑路线 · {profile.data['name']} · {length:.0f} m",
                             start_pt=result["route_ds"][0], end_pt=result["route_ds"][-1])
    print(f"[out] {route_path}")
    print(f"[out] {preview_path}")
    if args.sat:
        sat_path = os.path.join(args.out, route["name"] + "_preview_sat.png")
        routejson.render_preview(profile, result["route_ds"], result["checkpoints"], sat_path,
                                 title=f"校园跑路线（卫星底图）· {profile.data['name']} · {length:.0f} m",
                                 start_pt=result["route_ds"][0], end_pt=result["route_ds"][-1],
                                 basemap="sat")
        print(f"[out] {sat_path}")
    print(json.dumps({"length_m": round(length, 1), "target_km": result["target_m"]/1000,
                      "points": result["point_count"],
                      "route_json": route_path,
                      "preview": preview_path,
                      "checkpoints": [{"digit": c["digit"], "lon": round(c["lon"], 6),
                                       "lat": round(c["lat"], 6)} for c in cps]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
