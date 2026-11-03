#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 桌面版：截图 -> 地标点选 -> 自动规划 4km 路线 -> 导出 .route.json

运行：  python windows/app.py
依赖：  pip install pillow numpy
"""
import os
import sys
import json
import threading

import numpy as np
from PIL import Image, ImageTk

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.profile import Profile
from core.detect import detect_markers
from core.calib import calibrate, checkpoints_from_markers
from core import planner, routejson

OUT_DIR = os.path.join(ROOT, "out")


class App:
    def __init__(self, root):
        self.root = root
        root.title("校园跑 4km 路线规划器 · 桂林航天工业学院（南校区）")
        root.geometry("1320x880")
        self.profile = Profile()
        self.image = None          # np array
        self.pil = None
        self.tk_img = None
        self.canvas_scale = 1.0
        self.canvas_off = (0, 0)
        self.markers = []
        self.landmark_points = []  # [(name,(px,py))]
        self.result = None
        self.mode = "idle"         # idle / pick
        self.current_landmark = None
        self._build_ui()

    # ---------------- UI ----------------
    def _build_ui(self):
        left = tk.Frame(self.root, width=380)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=6)
        right = tk.Frame(self.root)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=6, pady=6)

        tk.Button(left, text="① 打开截图", height=2, command=self.open_image).pack(fill=tk.X, pady=2)
        tk.Label(left, text="② 选择地标 → 在右侧图上点击其位置（至少 2 个，建议 3 个）",
                 wraplength=360, justify=tk.LEFT, fg="#555").pack(fill=tk.X, pady=(6, 2))

        self.lb_landmarks = tk.Listbox(left, height=9)
        self.lb_landmarks.pack(fill=tk.X, pady=2)
        for l in self.profile.landmarks:
            self.lb_landmarks.insert(tk.END, l["name"])

        row = tk.Frame(left)
        row.pack(fill=tk.X, pady=2)
        tk.Button(row, text="开始点选", command=self.start_pick).pack(side=tk.LEFT, expand=True, fill=tk.X)
        tk.Button(row, text="清空地标", command=self.clear_landmarks).pack(side=tk.LEFT, expand=True, fill=tk.X)

        self.lb_picked = tk.Listbox(left, height=5)
        self.lb_picked.pack(fill=tk.X, pady=2)

        tk.Button(left, text="③ 识别打卡点", height=2, command=self.detect).pack(fill=tk.X, pady=(8, 2))
        rowm = tk.Frame(left)
        rowm.pack(fill=tk.X, pady=2)
        tk.Label(rowm, text="手动补点").pack(side=tk.LEFT)
        self.manual_digit = tk.StringVar(value="1")
        ttk.Combobox(rowm, textvariable=self.manual_digit, values=[str(i) for i in range(1, 6)],
                     width=4, state="readonly").pack(side=tk.LEFT, padx=4)
        tk.Button(rowm, text="在图上点该点", command=self.start_manual_marker).pack(side=tk.LEFT)
        tk.Button(left, text="④ 生成路线（约 10-20 秒）", height=2, command=self.plan).pack(fill=tk.X, pady=2)
        tk.Button(left, text="⑤ 打开输出目录", command=lambda: self.open_out()).pack(fill=tk.X, pady=2)

        row2 = tk.Frame(left)
        row2.pack(fill=tk.X, pady=(8, 0))
        tk.Label(row2, text="里程").pack(side=tk.LEFT)
        self.km_var = tk.StringVar(value="自动")
        self.km_combo = ttk.Combobox(row2, textvariable=self.km_var, width=10,
                                     state="readonly", values=["自动"])
        self.km_combo.pack(side=tk.LEFT, padx=4)
        tk.Button(row2, text="计算可选路程", command=self.calc_km).pack(side=tk.LEFT)
        rowp = tk.Frame(left)
        rowp.pack(fill=tk.X, pady=(2, 0))
        tk.Label(rowp, text="绕点圈数").pack(side=tk.LEFT)
        self.passes_var = tk.StringVar(value="1")
        ttk.Combobox(rowp, textvariable=self.passes_var, width=3, state="readonly",
                     values=["0", "1", "2", "3"]).pack(side=tk.LEFT, padx=4)
        self.sat_var = tk.BooleanVar(value=False)
        tk.Checkbutton(left, text="预览用卫星底图", variable=self.sat_var).pack(anchor=tk.W)
        row3 = tk.Frame(left)
        row3.pack(fill=tk.X, pady=(2, 0))
        tk.Label(row3, text="点数").pack(side=tk.LEFT)
        self.points_var = tk.StringVar(value="150")
        tk.Entry(row3, textvariable=self.points_var, width=5).pack(side=tk.LEFT)

        self.status = tk.Text(left, height=12, state=tk.DISABLED)
        self.status.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self.canvas = tk.Canvas(right, bg="#222", cursor="crosshair")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Configure>", lambda e: self.redraw())

    def log(self, msg):
        self.status.configure(state=tk.NORMAL)
        self.status.insert(tk.END, msg + "\n")
        self.status.see(tk.END)
        self.status.configure(state=tk.DISABLED)
        self.root.update_idletasks()

    # ---------------- 显示 ----------------
    def open_image(self):
        path = filedialog.askopenfilename(
            title="选择 App 截图",
            filetypes=[("图片", "*.jpg *.jpeg *.png *.bmp"), ("所有文件", "*.*")])
        if not path:
            return
        self.pil = Image.open(path).convert("RGB")
        self.image = np.asarray(self.pil).astype(np.uint8)
        self.markers = []
        self.landmark_points = []
        self.lb_picked.delete(0, tk.END)
        self.lb_picked.insert(tk.END, f"截图: {os.path.basename(path)}")
        self.result = None
        self.redraw()
        self.log(f"[open] {path}  {self.pil.size[0]}x{self.pil.size[1]}")

    def redraw(self):
        c = self.canvas
        c.delete("all")
        if self.pil is None:
            c.create_text(20, 20, anchor="nw", fill="#aaa",
                          text="用「① 打开截图」载入 App 的打卡点界面截图")
            return
        cw = max(1, c.winfo_width())
        ch = max(1, c.winfo_height())
        iw, ih = self.pil.size
        s = min(cw / iw, ch / ih)
        self.canvas_scale = s
        ox = (cw - iw * s) / 2
        oy = (ch - ih * s) / 2
        self.canvas_off = (ox, oy)
        img = self.pil.resize((max(1, int(iw * s)), max(1, int(ih * s))), Image.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(img)
        c.create_image(ox, oy, anchor="nw", image=self.tk_img)
        # 地标点
        for name, (px, py) in self.landmark_points:
            cx, cy = ox + px * s, oy + py * s
            c.create_line(cx - 9, cy, cx + 9, cy, fill="#00c853", width=2)
            c.create_line(cx, cy - 9, cx, cy + 9, fill="#00c853", width=2)
            c.create_text(cx + 11, cy - 11, text=name, fill="#00e676", anchor="sw",
                          font=("Microsoft YaHei", 10))
        # 打卡点标记
        for m in self.markers:
            cx, cy = ox + m["px"] * s, oy + m["py"] * s
            r = max(8, 20 * s)
            c.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#2979ff", width=2)
            c.create_text(cx, cy - r - 2, text=f"{m['digit']}", fill="#2979ff", anchor="s",
                          font=("Microsoft YaHei", 11, "bold"))

    def on_canvas_click(self, ev):
        if self.image is None:
            return
        if self.mode == "marker":
            px = (ev.x - self.canvas_off[0]) / self.canvas_scale
            py = (ev.y - self.canvas_off[1]) / self.canvas_scale
            dg = int(self.manual_digit.get())
            self.markers = [m for m in self.markers if m["digit"] != dg]
            self.markers.append(dict(px=px, py=py, digit=dg, score=1.0, color="manual"))
            self.markers.sort(key=lambda m: m["digit"])
            self.log(f"[marker] 已手动补 {dg} 号点 ({px:.0f},{py:.0f})，当前 {len(self.markers)} 个点")
            self.mode = "idle"
            self.redraw()
            return
        sel = self.lb_landmarks.curselection()
        if self.mode != "pick" or not sel:
            self.log("[hint] 先在左侧选中地标，再点「开始点选」")
            return
        name = self.profile.landmarks[sel[0]]["name"]
        ox, oy = self.canvas_off
        px = (ev.x - ox) / self.canvas_scale
        py = (ev.y - oy) / self.canvas_scale
        if not (0 <= px <= self.pil.size[0] and 0 <= py <= self.pil.size[1]):
            return
        self.landmark_points = [p for p in self.landmark_points if p[0] != name] + [(name, (px, py))]
        self.refresh_picked()
        self.mode = "pick"
        self.redraw()

    def refresh_picked(self):
        self.lb_picked.delete(0, tk.END)
        for name, (px, py) in self.landmark_points:
            self.lb_picked.insert(tk.END, f"{name}: ({px:.0f}, {py:.0f})")
        self.lb_picked.insert(tk.END, f"共 {len(self.landmark_points)} 个地标")

    def start_pick(self):
        if self.image is None:
            messagebox.showinfo("提示", "先打开截图")
            return
        if not self.lb_landmarks.curselection():
            messagebox.showinfo("提示", "先在列表里选中一个地标")
            return
        self.mode = "pick"
        self.log("[pick] 在右侧截图上点击该地标的位置")

    def clear_landmarks(self):
        self.landmark_points = []
        self.refresh_picked()
        self.redraw()

    # ---------------- 识别 / 规划 ----------------
    def start_manual_marker(self):
        if self.image is None:
            messagebox.showinfo("提示", "先打开截图")
            return
        self.mode = "marker"
        self.log(f"[marker] 请在图上点击 {self.manual_digit.get()} 号打卡点")

    def detect(self):
        if self.image is None:
            messagebox.showinfo("提示", "先打开截图")
            return
        try:
            self.markers = detect_markers(self.image, self.profile.templates, self.profile.marker_colors)
        except Exception as e:  # noqa
            messagebox.showerror("识别失败", str(e))
            return
        self.log(f"[detect] 识别到 {len(self.markers)} 个打卡点: " +
                 ", ".join(f"{m['digit']}({m['score']:.2f})" for m in self.markers))
        if len(self.markers) != 5:
            self.log("[warn] 需要完整 5 个点（可能被气泡遮挡），可重新截图或稍后手动补点")
        self.redraw()

    def calc_km(self):
        """计算本校区可规划的公里数（填进里程下拉框）。"""
        if self.image is None:
            messagebox.showinfo("提示", "先打开截图")
            return
        if len(self.markers) != 5:
            messagebox.showinfo("提示", "先点「③ 识别打卡点」，且需完整 5 个点")
            return
        if len(self.landmark_points) < 2:
            messagebox.showinfo("提示", "至少点选 2 个地标")
            return

        clicks = list(self.landmark_points)

        def work():
            try:
                calib = calibrate(self.profile, clicks)
                cps = checkpoints_from_markers(self.profile, calib, self.markers)
                res = planner.plan(self.profile, [(c["lon"], c["lat"]) for c in cps],
                                   auto=True, target_points=150,
                                   pass_laps=int(self.passes_var.get() or 0),
                                   pass_radius_m=12.0, log=lambda *a: None)
                opts = res.get("available_km", [])
                sug = res["target_m"] / 1000.0
                self.root.after(0, lambda: self._fill_km(opts, sug))
            except Exception as e:  # noqa
                err = str(e)
                self.root.after(0, lambda: messagebox.showerror("计算失败", err))

        self.log("[km] 计算可规划路程中…")
        threading.Thread(target=work, daemon=True).start()

    def _fill_km(self, opts, sug):
        values = ["自动"] + [f"{k:g}" for k in opts]
        self.km_combo.configure(values=values)
        self.km_var.set(f"{sug:g}" if sug in opts else "自动")
        self.log("[km] 可规划路程: " + (" / ".join(f"{k:g}" for k in opts) if opts else "无")
                 + f" km，默认自动 {sug:g} km")

    def plan(self):
        if self.image is None:
            messagebox.showinfo("提示", "先打开截图")
            return
        if len(self.markers) != 5:
            messagebox.showinfo("提示", "先点「③ 识别打卡点」，且需完整识别 5 个点")
            return
        if len(self.landmark_points) < 2:
            messagebox.showinfo("提示", "至少点选 2 个地标（建议 3 个，用于校验）")
            return
        km_sel = self.km_var.get()
        if km_sel in ("自动", ""):
            target = None
        else:
            target = float(km_sel) * 1000.0
        auto = target is None
        points = max(70, min(200, int(self.points_var.get() or 150)))
        laps = int(self.passes_var.get() or 0)
        self.log("[plan] 计算中，请稍候…")
        self.root.config(cursor="watch")

        def work():
            try:
                calib = calibrate(self.profile, self.landmark_points)
                cps = checkpoints_from_markers(self.profile, calib, self.markers)
                res = planner.plan(self.profile, [(c["lon"], c["lat"]) for c in cps],
                                   target_m=target, auto=auto, target_points=points,
                                   pass_laps=laps, pass_radius_m=12.0, log=self.log)
                self.result = (calib, cps, res)
                self.root.after(0, lambda: self._show_result(cps, res))
            except Exception as e:  # noqa
                err = str(e)
                self.root.after(0, lambda: messagebox.showerror("规划失败", err))
            finally:
                self.root.after(0, lambda: self.root.config(cursor=""))

        threading.Thread(target=work, daemon=True).start()

    def _show_result(self, cps, res):
        extra = (f"，本校区可规划: {' / '.join(f'{k:g}' for k in res['available_km'])} km"
                 if res.get("available_km") else "")
        self.log(f"[route] 里程 {res['length_m']:.1f} m（目标 {res['target_m']/1000:.1f} km），"
                 f"点数 {res['point_count']}（70~200），打卡点精确经过{extra}")
        os.makedirs(OUT_DIR, exist_ok=True)
        route = routejson.build_route_dict(res["route_wgs"])
        route_path = os.path.join(OUT_DIR, route["name"] + ".route.json")
        routejson.save_route(route, route_path)
        preview_path = os.path.join(OUT_DIR, route["name"] + "_preview.png")
        basemap = "sat" if self.sat_var.get() else "vector"
        routejson.render_preview(self.profile, res["route_ds"], res["checkpoints"], preview_path,
                                 title=f"校园跑路线 · {self.profile.data['name']} · {res['length_m']:.0f} m",
                                 start_pt=res["route_ds"][0], end_pt=res["route_ds"][-1],
                                 basemap=basemap)
        self.log(f"[out] {route_path}")
        self.log(f"[out] {preview_path}")
        # 预览窗口
        win = tk.Toplevel(self.root)
        win.title("路线预览")
        im = Image.open(preview_path)
        im.thumbnail((1100, 1100))
        tkimg = ImageTk.PhotoImage(im)
        lab = tk.Label(win, image=tkimg)
        lab.image = tkimg
        lab.pack()
        tk.Label(win, text=f"里程 {res['length_m']:.0f} m · {res['point_count']} 点 ｜ 文件: {route_path}",
                 font=("Microsoft YaHei", 10)).pack(pady=4)

    def open_out(self):
        os.makedirs(OUT_DIR, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(OUT_DIR)  # noqa
        else:
            self.log(OUT_DIR)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
