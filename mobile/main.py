# -*- coding: utf-8 -*-
"""安卓 APK 版（Kivy）：截图 -> 点地标 -> 识别打卡点 -> 选里程 -> 生成 .route.json

与网页版共用 core/、assets/、campus_profile.json，功能对齐：
- 4 倍放大镜点选（地标/补点更准）
- 自定义里程（米）、点数、绕点圈数、起点、锚点 dy
- 输出文件用中文日期时间命名，自动存到 /sdcard/Download/校园跑/

构建见 mobile/README.md（Buildozer / GitHub Actions）。
"""
import io
import os
import sys
import threading
import time
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import numpy as np
from PIL import Image as PILImage

from kivy.app import App
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.core.text import LabelBase
from kivy.graphics import Color, Line, Rectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

from core.profile import Profile
from core.detect import detect_markers
from core.calib import calibrate, checkpoints_from_markers
from core import planner, routejson

# ---- 中文字体 ----
for _f in ("/system/fonts/NotoSansCJK-Regular.ttc",
           "/system/fonts/NotoSansSC-Regular.otf",
           "/system/fonts/DroidSansFallback.ttf"):
    if os.path.exists(_f):
        LabelBase.register(name="CJK", fn_regular=_f)
        break
try:
    _fonts = getattr(LabelBase, "_fonts", {}) or {}
except Exception:
    _fonts = {}
FONT = "CJK" if "CJK" in _fonts else "Roboto"


def _android_ready():
    """安卓上申请存储权限。"""
    if platform != "android":
        return
    try:
        from android.permissions import request_permissions, Permission  # noqa
        request_permissions([Permission.READ_EXTERNAL_STORAGE,
                             Permission.WRITE_EXTERNAL_STORAGE,
                             Permission.READ_MEDIA_IMAGES])
    except Exception:
        pass


def _out_dir():
    """优先写手机下载目录下的中文文件夹。"""
    for d in ("/sdcard/Download/校园跑", os.path.join(APP_DIR, "out")):
        try:
            os.makedirs(d, exist_ok=True)
            test = os.path.join(d, ".w")
            open(test, "w").close()
            os.remove(test)
            return d
        except Exception:
            continue
    return APP_DIR


def _cn_name():
    t = time.localtime()
    return f"{t.tm_year}年{t.tm_mon}月{t.tm_mday}日{t.tm_hour}时{t.tm_min}分"


def _texture(pil):
    buf = io.BytesIO()
    pil.convert("RGB").save(buf, format="png")
    buf.seek(0)
    tex = CoreImage(buf, ext="png").texture
    if tex:
        tex.flip_vertical()
    return tex


class CanvasView(Widget):
    """把一张 PIL 图按比例铺满控件，并提供控件坐标 <-> 图片像素换算。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.pil = None
        self.tex = None
        self.img_size = (1, 1)
        self.marks = []          # [(x, y, color)]
        self.lines = []          # [[(x, y), ...], ...]
        self.bind(size=lambda *a: self.rebuild(), pos=lambda *a: self.rebuild())

    def load(self, pil):
        self.pil = pil.convert("RGB")
        self.tex = _texture(self.pil)
        self.img_size = self.pil.size
        self.rebuild()

    def _fit(self):
        w, h = self.size
        iw, ih = self.img_size
        s = min(w / max(1, iw), h / max(1, ih))
        return s, (w - iw * s) / 2.0, (h - ih * s) / 2.0

    def to_image(self, wx, wy):
        s, ox, oy = self._fit()
        return (wx - ox) / s, (wy - oy) / s

    def rebuild(self):
        self.canvas.clear()
        if not self.tex:
            return
        s, ox, oy = self._fit()
        iw, ih = self.img_size
        with self.canvas:
            Color(1, 1, 1, 1)
            Rectangle(texture=self.tex, pos=(ox, oy), size=(iw * s, ih * s))
            for path in self.lines:
                Color(0.9, 0.1, 0.1, 1)
                pts = []
                for x, y in path:
                    pts.extend([ox + x * s, oy + y * s])
                if len(pts) >= 4:
                    Line(points=pts, width=dp(2))
            for x, y, col in self.marks:
                Color(*col)
                cx, cy = ox + x * s, oy + y * s
                r = dp(10)
                Line(circle=(cx, cy, r), width=dp(2))
                Line(points=[cx - r, cy, cx + r, cy], width=dp(1))
                Line(points=[cx, cy - r, cx, cy + r], width=dp(1))


class ZoomPopup(Popup):
    """4 倍放大选点：在放大图上点准确位置。"""

    def __init__(self, pil, rect, callback, **kw):
        super().__init__(**kw)
        self.callback = callback
        x0, y0, x1, y1 = rect
        self.rect = rect
        self.title = "点一下准确位置"
        self.size_hint = (0.96, 0.72)
        crop = pil.crop(rect).resize(((x1 - x0) * 4, (y1 - y0) * 4), PILImage.LANCZOS)
        box = BoxLayout(orientation="vertical", spacing=dp(4))
        self.view = CanvasView()
        self.view.load(crop)
        self.view.marks = [((x1 - x0) * 2, (y1 - y0) * 2, (1, 0, 0))]
        box.add_widget(self.view)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(4))
        row.add_widget(Button(text="取消", font_name=FONT, on_release=lambda *a: self.dismiss()))
        box.add_widget(row)
        self.content = box
        self.view.bind(on_touch_down=self._touch)

    def _touch(self, w, touch):
        if not w.collide_point(*touch.pos):
            return False
        px, py = w.to_image(*touch.pos)
        x0, y0, x1, y1 = self.rect
        rx = max(0, min(x1 - x0 - 1, px / 4.0))
        ry = max(0, min(y1 - y0 - 1, py / 4.0))
        self.dismiss()
        self.callback(x0 + rx, y0 + ry)
        return True


class MapView(Widget):
    """显示截图/预览，绘制地标、打卡点、路线；点击时先放大再确认。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.pil = None
        self.tex = None
        self.img_size = (1, 1)
        self.landmarks = []      # [(name, (px, py))]
        self.markers = []        # [{'digit','px','py'}]
        self.route = []          # [(px, py)]
        self.mode = None         # 'landmark' | 'marker'
        self.pending_name = None
        self.pending_digit = 1
        self.bind(size=lambda *a: self.rebuild(), pos=lambda *a: self.rebuild())

    def load_pil(self, pil):
        self.pil = pil.convert("RGB")
        self.tex = _texture(self.pil)
        self.img_size = self.pil.size
        self.rebuild()

    def load_route_image(self, path):
        self.load_pil(PILImage.open(path).convert("RGB"))

    def _fit(self):
        w, h = self.size
        iw, ih = self.img_size
        s = min(w / max(1, iw), h / max(1, ih))
        return s, (w - iw * s) / 2.0, (h - ih * s) / 2.0

    def to_image(self, wx, wy):
        s, ox, oy = self._fit()
        return (wx - ox) / s, (wy - oy) / s

    def to_widget(self, ix, iy):
        s, ox, oy = self._fit()
        return ox + ix * s, oy + iy * s

    def rebuild(self):
        self.canvas.clear()
        if not self.tex:
            return
        s, ox, oy = self._fit()
        iw, ih = self.img_size
        with self.canvas:
            Color(1, 1, 1, 1)
            Rectangle(texture=self.tex, pos=(ox, oy), size=(iw * s, ih * s))
            Color(0, 0.85, 0.3, 1)
            for name, (px, py) in self.landmarks:
                x, y = self.to_widget(px, py)
                Line(points=[x - dp(9), y, x + dp(9), y], width=dp(2))
                Line(points=[x, y - dp(9), x, y + dp(9)], width=dp(2))
            Color(0.15, 0.45, 1, 1)
            for m in self.markers:
                x, y = self.to_widget(m["px"], m["py"])
                Line(circle=(x, y, dp(9)), width=dp(2))
            if len(self.route) > 1:
                Color(0.9, 0.1, 0.1, 1)
                pts = []
                for px, py in self.route:
                    pts.extend(self.to_widget(px, py))
                Line(points=pts, width=dp(2.2))

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos) or self.tex is None:
            return super().on_touch_down(touch)
        if self.mode not in ("landmark", "marker"):
            return super().on_touch_down(touch)
        px, py = self.to_image(*touch.pos)
        r = 55
        x0 = max(0, min(self.img_size[0] - 2 * r, px - r))
        y0 = max(0, min(self.img_size[1] - 2 * r, py - r))
        ZoomPopup(self.pil, (int(x0), int(y0), int(x0 + 2 * r), int(y0 + 2 * r)),
                  self._confirm, size_hint=(0.96, 0.75)).open()
        return True

    def _confirm(self, px, py):
        app = App.get_running_app()
        if self.mode == "landmark":
            name = self.pending_name
            self.landmarks = [p for p in self.landmarks if p[0] != name]
            self.landmarks.append((name, (px, py)))
            app.root.set_status(f"已点地标「{name}」({px:.0f},{py:.0f})，继续点下一个或生成")
        elif self.mode == "marker":
            dg = self.pending_digit
            self.markers = [m for m in self.markers if m["digit"] != dg]
            self.markers.append(dict(digit=dg, px=px, py=py, score=1.0))
            self.markers.sort(key=lambda m: m["digit"])
            app.root.set_status(f"已补 {dg} 号点 ({px:.0f},{py:.0f})，共 {len(self.markers)} 个")
        self.mode = None
        self.rebuild()


class Root(BoxLayout):
    def __init__(self, **kw):
        super().__init__(orientation="vertical", **kw)
        self.profile = Profile(APP_DIR + "/campus_profile.json")
        self.shot_path = None
        self.result = None
        self._build_ui()

    def _btn(self, text, cb, w=1.0):
        b = Button(text=text, size_hint_x=w, font_name=FONT, font_size=dp(13))
        b.bind(on_release=lambda *a: cb())
        return b

    def _build_ui(self):
        bar = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(2))
        bar.add_widget(self._btn("打开截图", self.open_file, 1.1))
        bar.add_widget(self._btn("识别打卡点", self.detect, 1.2))
        self.lm_sp = Spinner(text="地标", values=[l["name"] for l in self.profile.landmarks],
                             size_hint_x=1.2, font_name=FONT, font_size=dp(12))
        bar.add_widget(self.lm_sp)
        bar.add_widget(self._btn("点选", self.pick_landmark, 0.7))
        self.digit_sp = Spinner(text="1号", values=[f"{i}号" for i in range(1, 6)],
                                size_hint_x=0.7, font_name=FONT, font_size=dp(12))
        bar.add_widget(self.digit_sp)
        bar.add_widget(self._btn("补点", self.pick_marker, 0.7))
        self.add_widget(bar)

        self.map = MapView()
        self.add_widget(self.map)

        bottom = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(2))
        self.km_sp = Spinner(text="自动", values=["自动"], size_hint_x=1.0,
                             font_name=FONT, font_size=dp(12))
        bottom.add_widget(self.km_sp)
        bottom.add_widget(self._btn("可选路程", self.calc_km, 1.0))
        self.pass_sp = Spinner(text="绕点1圈", values=["绕点0圈", "绕点1圈", "绕点2圈"],
                               size_hint_x=1.0, font_name=FONT, font_size=dp(12))
        bottom.add_widget(self.pass_sp)
        bottom.add_widget(self._btn("设置", self.open_settings, 0.7))
        bottom.add_widget(self._btn("生成路线", self.plan, 1.2))
        self.add_widget(bottom)

        self.status = Label(text="① 打开截图 → ② 点 2~3 个地标 → ③ 识别打卡点 → ④ 选里程 → ⑤ 生成",
                            size_hint_y=None, height=dp(34), font_name=FONT, font_size=dp(11))
        self.add_widget(self.status)

    # ---------- 状态 ----------
    def set_status(self, text):
        self.status.text = text

    # ---------- 文件 ----------
    def open_file(self):
        start = "/sdcard/Download" if os.path.isdir("/sdcard/Download") else "/sdcard"
        if not os.path.isdir(start):
            start = os.path.dirname(os.path.abspath(__file__))
        chooser = FileChooserListView(path=start, filters=["*.jpg", "*.jpeg", "*.png"])
        box = BoxLayout(orientation="vertical")
        box.add_widget(chooser)
        row = BoxLayout(size_hint_y=None, height=dp(44))
        popup = Popup(title="选择 App 截图", content=box, size_hint=(0.95, 0.95))

        def ok(*a):
            if chooser.selection:
                self._load_shot(chooser.selection[0])
            popup.dismiss()

        row.add_widget(Button(text="选择", font_name=FONT, on_release=ok))
        row.add_widget(Button(text="取消", font_name=FONT, on_release=lambda *a: popup.dismiss()))
        box.add_widget(row)
        popup.open()

    def _load_shot(self, path):
        self.shot_path = path
        self.map.load_pil(PILImage.open(path).convert("RGB"))
        self.map.landmarks = []
        self.map.markers = []
        self.map.route = []
        self.map.rebuild()
        self.set_status(f"截图已载入：{os.path.basename(path)}，先点选至少 2 个地标")

    def _np_image(self):
        return np.asarray(PILImage.open(self.shot_path).convert("RGB")).astype(np.uint8)

    # ---------- 设置 ----------
    def open_settings(self):
        box = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))

        def labeled(text, widget):
            row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
            row.add_widget(Label(text=text, font_name=FONT, size_hint_x=0.4))
            row.add_widget(widget)
            return row

        self.points_ti = TextInput(text="150", input_filter="int", font_name=FONT,
                                   multiline=False, size_hint_x=0.6)
        self.ady_sp = Spinner(text="52", values=["40", "46", "52", "56"], font_name=FONT)
        self.custom_km_ti = TextInput(text="", hint_text="自定义米数，如 3500",
                                      input_filter="float", font_name=FONT,
                                      multiline=False, size_hint_x=0.6)
        self.start_sp = Spinner(text="默认（1号点附近）", font_name=FONT,
                                values=["默认（1号点附近）"] + [l["name"] for l in self.profile.landmarks])

        box.add_widget(labeled("点数 70~200", self.points_ti))
        box.add_widget(labeled("锚点 dy", self.ady_sp))
        box.add_widget(labeled("自定义里程(m)", self.custom_km_ti))
        box.add_widget(labeled("起点", self.start_sp))
        box.add_widget(Label(text="自定义里程填了就优先；不填按上面的里程下拉",
                             font_name=FONT, font_size=dp(11), size_hint_y=None, height=dp(28)))
        popup = Popup(title="设置", content=box, size_hint=(0.92, 0.6))
        box.add_widget(Button(text="关闭", font_name=FONT, size_hint_y=None, height=dp(44),
                              on_release=lambda *a: popup.dismiss()))
        popup.open()

    # ---------- 识别 / 补点 ----------
    def _anchor(self):
        try:
            dy = float(getattr(self, "ady_sp").text)
        except Exception:
            dy = 52.0
        return dict(self.profile.marker_anchor, dy=dy)

    def pick_landmark(self):
        if not self.shot_path:
            return self.set_status("先打开截图")
        self.map.pending_name = self.lm_sp.text
        self.map.mode = "landmark"
        self.set_status(f"请在图上点击「{self.lm_sp.text}」，再在放大图里点准")

    def pick_marker(self):
        if not self.shot_path:
            return self.set_status("先打开截图")
        self.map.pending_digit = int(self.digit_sp.text.replace("号", ""))
        self.map.mode = "marker"
        self.set_status(f"请在图上点击 {self.map.pending_digit} 号打卡点，再在放大图里点准")

    def detect(self):
        if not self.shot_path:
            return self.set_status("先打开截图")
        self.set_status("识别打卡点…")
        try:
            img = self._np_image()
            ms = detect_markers(img, self.profile.templates, self.profile.marker_colors,
                                anchor=self._anchor())
            self.map.markers = ms
            self.map.rebuild()
            self.set_status(f"识别到 {len(ms)} 个打卡点：" +
                            ", ".join(f"{m['digit']}({m['score']:.2f})" for m in ms) +
                            ("" if len(ms) == 5 else "；不足 5 个请用「补点」"))
        except Exception:
            self.set_status("识别失败：" + traceback.format_exc(limit=1))

    # ---------- 规划 ----------
    def _check(self):
        if not self.shot_path:
            raise RuntimeError("先打开截图")
        if len(self.map.markers) != 5:
            raise RuntimeError("需要 5 个打卡点（识别 + 手动补点）")
        if len(self.map.landmarks) < 2:
            raise RuntimeError("至少点选 2 个地标")
        calib = calibrate(self.profile, list(self.map.landmarks), verbose=False)
        cps = checkpoints_from_markers(self.profile, calib, self.map.markers)
        return calib, cps

    def _target_m(self):
        try:
            txt = (self.custom_km_ti.text or "").strip()
        except Exception:
            txt = ""
        if txt:
            return float(txt)
        km = self.km_sp.text
        if km in ("自动", ""):
            return None
        try:
            return float(km) * 1000.0
        except Exception:
            return None

    def _start_gcj(self):
        sp = getattr(self, "start_sp", None)
        if sp is None:
            return None
        name = sp.text
        for l in self.profile.landmarks:
            if l["name"] == name:
                return (l["lon"], l["lat"])
        return None

    def calc_km(self):
        if not self.shot_path:
            return self.set_status("先打开截图")
        self.set_status("计算可规划路程…")
        threading.Thread(target=self._calc_km_worker, daemon=True).start()

    def _calc_km_worker(self):
        try:
            _, cps = self._check()
            laps = int(self.pass_sp.text.replace("绕点", "").replace("圈", ""))
            res = planner.plan(self.profile, [(c["lon"], c["lat"]) for c in cps],
                               auto=True, target_points=self._points(), pass_laps=laps,
                               pass_radius_m=12.0, log=lambda *a: None)
            opts = ["自动"] + [f"{k:g}" for k in res.get("available_km", [])]
            sug = f"{res['target_m'] / 1000:g}"
            Clock.schedule_once(lambda *a: self._fill_km(opts, sug), 0)
        except Exception as e:  # noqa
            Clock.schedule_once(lambda *a: self.set_status("计算失败：" + str(e)), 0)

    def _fill_km(self, opts, sug):
        self.km_sp.values = opts
        self.km_sp.text = sug if sug in opts else "自动"
        self.set_status("可规划路程: " + " / ".join(opts[1:]) + f" km（默认 {sug} km）")

    def _points(self):
        ti = getattr(self, "points_ti", None)
        try:
            return max(70, min(200, int(ti.text or 150)))
        except Exception:
            return 150

    def plan(self):
        if not self.shot_path:
            return self.set_status("先打开截图")
        self.set_status("生成路线中（约 5~15 秒）…")
        threading.Thread(target=self._plan_worker, daemon=True).start()

    def _plan_worker(self):
        try:
            _, cps = self._check()
            target = self._target_m()
            laps = int(self.pass_sp.text.replace("绕点", "").replace("圈", ""))
            res = planner.plan(self.profile, [(c["lon"], c["lat"]) for c in cps],
                               target_m=target, auto=(target is None),
                               target_points=self._points(), pass_laps=laps,
                               pass_radius_m=12.0, start_gcj=self._start_gcj(),
                               log=lambda *a: None)
            outdir = _out_dir()
            name = _cn_name()
            route = routejson.build_route_dict(res["route_wgs"], name=name)
            rp = os.path.join(outdir, name + ".route.json")
            routejson.save_route(route, rp)
            prev = os.path.join(outdir, name + ".png")
            routejson.render_preview(self.profile, res["route_ds"], res["checkpoints"], prev,
                                     title=f"校园跑 {res['length_m']:.0f} m · {res['point_count']} 点",
                                     start_pt=res["route_ds"][0], end_pt=res["route_ds"][-1])
            exact = abs(res["length_m"] - res["target_m"]) <= 2.0
            self.result = dict(res=res, route=route, path=rp, preview=prev, exact=exact)
            Clock.schedule_once(lambda *a: self._done(), 0)
        except Exception as e:  # noqa
            Clock.schedule_once(lambda *a: self.set_status("生成失败：" + str(e)), 0)

    def _done(self):
        r = self.result
        res = r["res"]
        self.map.load_route_image(r["preview"])
        note = ""
        if not r["exact"]:
            note = (f"目标 {res['target_m']:.0f} m 未达到（必须经过 5 个打卡点），"
                    f"实际 {res['length_m']:.0f} m\n")
        self.set_status(f"完成：{res['length_m']:.0f} m / {res['point_count']} 点，{os.path.basename(r['path'])}")
        box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(8))
        box.add_widget(Label(text=note + f"已保存：\n{r['path']}",
                             font_name=FONT, font_size=dp(12)))
        preview = CanvasView(size_hint=(1, 1))
        preview.load(PILImage.open(r["preview"]).convert("RGB"))
        box.add_widget(preview)
        popup = Popup(title="路线已生成", content=box, size_hint=(0.95, 0.85))
        box.add_widget(Button(text="关闭", font_name=FONT, size_hint_y=None, height=dp(44),
                              on_release=lambda *a: popup.dismiss()))
        popup.open()


class CampusRouteApp(App):
    def build(self):
        self.title = "校园跑路线规划器"
        _android_ready()
        return Root()


if __name__ == "__main__":
    CampusRouteApp().run()
