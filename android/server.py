#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安卓（Termux / Pydroid）网页版：手机浏览器打开本机网页，上传截图、点 2 个地标，生成 4km 路线。

用法（Termux）：
  pkg install python numpy
  pip install pillow
  cd campus_route_tool
  python android/server.py
  然后用手机浏览器访问  http://127.0.0.1:8765
"""
import argparse
import base64
import io
import json
import math
import os
import sys
import time
import traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote, quote, parse_qs

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.profile import Profile
from core.detect import detect_markers
from core.calib import calibrate, checkpoints_from_markers
from core import planner, routejson

OUT_DIR = os.path.join(ROOT, "out")
os.makedirs(OUT_DIR, exist_ok=True)
PROFILE = Profile()

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>校园跑 4km 路线规划</title>
<style>
:root{--main:#1e88e5}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,'Microsoft YaHei',sans-serif;background:#f5f6f8;color:#222}
header{background:var(--main);color:#fff;padding:10px 14px;font-size:17px}
header small{display:block;font-size:12px;opacity:.85}
.card{background:#fff;margin:8px;border-radius:10px;padding:10px;box-shadow:0 1px 3px rgba(0,0,0,.12)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
button{background:var(--main);color:#fff;border:0;border-radius:8px;padding:9px 14px;font-size:15px}
button.ghost{background:#e3eefb;color:#1565c0}
button:disabled{opacity:.5}
select,input[type=number]{padding:8px;border:1px solid #ccd;border-radius:8px;font-size:15px;background:#fff}
#wrap{position:relative;margin-top:8px}
#cv{width:100%;display:block;background:#222;border-radius:8px;touch-action:manipulation}
#msg{font-size:13px;color:#666;white-space:pre-wrap;margin-top:6px}
#done{display:none}
#preview{width:100%;border-radius:8px;margin-top:8px}
.tag{display:inline-block;background:#e8f0fe;color:#1565c0;border-radius:6px;padding:2px 6px;margin:2px;font-size:12px}
#zoombox{display:none;position:fixed;left:50%;top:46%;transform:translate(-50%,-50%);z-index:9;
  background:#fff;padding:8px;border-radius:12px;box-shadow:0 6px 24px rgba(0,0,0,.35)}
#zcv{width:300px;height:300px;display:block;border-radius:8px;image-rendering:pixelated}
.zfoot{text-align:center;font-size:12px;color:#666;margin-top:6px}
.zfoot button{margin-left:6px;padding:4px 10px;font-size:12px}
</style></head>
<body>
<header>校园跑 4km 路线规划<small>__CAMPUS__ · 半自动：点 2 个地标</small></header>

<div class="card">
  <div class="row">
    <input type="file" id="file" accept="image/*">
    <button class="ghost" id="resetBtn">清空重来</button>
    <label>目标里程 <input type="number" id="target" value="4000" step="50" style="width:90px"> m</label>
  </div>
</div>

<div class="card" id="step2" style="display:none">
  <div class="row">
    <select id="lm"></select>
    <button class="ghost" id="pickBtn">在图上点选该地标</button>
    <button class="ghost" id="clearBtn">清空</button>
  </div>
  <div id="tags"></div>
  <div class="row" style="margin-top:4px">
    <label>手动补点 <select id="mkdigit"><option>1</option><option>2</option><option>3</option><option>4</option><option>5</option></select></label>
    <button class="ghost" id="mkBtn">在图上点该点位</button>
    <button class="ghost" id="mkClear">清空手动点</button>
  </div>
  <div class="row" style="margin-top:6px">
    <label>里程
      <select id="km"><option value="auto">自动</option><option value="custom">自定义</option></select> km
    </label>
    <label>自定义 <input type="number" id="customKm" value="1000" step="50" min="200" max="8000" style="width:84px"> m</label>
    <label>底图
      <select id="base"><option value="vector">矢量</option><option value="sat">卫星</option></select>
    </label>
    <label>绕点圈数
      <select id="passes"><option>0</option><option selected>1</option><option>2</option></select>
    </label>
    <button id="kmBtn" class="ghost">计算可选路程</button>
    <button id="detectBtn" class="ghost">识别打卡点</button>
    <button id="planBtn" disabled>生成路线</button>
  </div>
  <div class="row" style="margin-top:6px">
    <label>点数 <input type="number" id="points" value="150" min="70" max="200" step="10" style="width:76px"></label>
    <label>起点 <select id="start"><option value="">默认（1号点附近）</option></select></label>
    <input type="text" id="startxy" placeholder="或 lon,lat" style="width:120px">
  </div>
  <div class="row" style="margin-top:4px">
    <label>锚点 dy <input type="number" id="ady" value="52" step="5" style="width:70px"></label>
    <label>dx <input type="number" id="adx" value="0" step="1" style="width:60px"></label>
    <span class="tag">打卡点锚点：数字圆圈下方，默认 dy=40（1080 宽）</span>
  </div>
  <div id="wrap"><canvas id="cv"></canvas></div>
  <div id="msg"></div>
  <div id="zoombox">
    <canvas id="zcv" width="300" height="300"></canvas>
    <div class="zfoot">在放大图上点准确位置 <button id="zcancel" class="ghost">取消</button></div>
  </div>
</div>

<div class="card" id="done">
  <div id="result"></div>
  <img id="preview">
  <div class="row" style="margin-top:8px">
    <a id="dl"><button>下载路线 .route.json</button></a>
    <a id="dlmap"><button>下载地图 PNG</button></a>
  </div>
</div>

<script>
const CAMPUS = "__CAMPUS__";
let landmarks = [], picks = [], img = null, imgW = 0, imgH = 0, b64 = "";
let picking = false, markers = [];
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');

fetch('/api/profile').then(r => r.json()).then(d => {
  landmarks = d.landmarks;
  const sel = document.getElementById('lm');
  const st = document.getElementById('start');
  landmarks.forEach(l => { const o = document.createElement('option'); o.textContent = l.name; sel.appendChild(o); });
  landmarks.forEach(l => { const o = document.createElement('option'); o.textContent = l.name; o.value = l.name; st.appendChild(o); });
  restoreState();
});

// ---- 用 Blob 下载，避免浏览器跳到文件页后返回丢失进度 ----
function saveFile(url, name){
  fetch(url).then(r => r.blob()).then(b => {
    const a = document.createElement('a');
    const u = URL.createObjectURL(b);
    a.href = u; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(u), 10000);
  }).catch(e => msg('下载失败：' + e));
}

function extraOpts(){
  const st = (document.getElementById('startxy').value || '').trim() || document.getElementById('start').value;
  return {
    route_points: parseInt(document.getElementById('points').value || '150'),
    start: st,
    anchor_dx: parseFloat(document.getElementById('adx').value || '0'),
    anchor_dy: parseFloat(document.getElementById('ady').value || '40')
  };
}

document.getElementById('file').onchange = e => {
  const f = e.target.files[0]; if (!f) return;
  const fr = new FileReader();
  fr.onload = () => {
    b64 = fr.result;
    img = new Image();
    img.onload = () => {
      imgW = img.naturalWidth; imgH = img.naturalHeight;
      document.getElementById('step2').style.display = 'block';
      draw(); saveState();
      msg('截图已载入：' + imgW + 'x' + imgH + '\n先选地标，点“在图上点选该地标”，再点图片上对应位置（至少 2 个）。');
    };
    img.src = fr.result;
  };
  fr.readAsDataURL(f);
};

document.getElementById('resetBtn').onclick = () => {
  try { localStorage.removeItem('campusState'); } catch (e) {}
  location.reload();
};

document.getElementById('pickBtn').onclick = () => { picking = true; msg('请在图片上点击该地标的位置…'); };
document.getElementById('clearBtn').onclick = () => { picks = []; renderTags(); draw(); saveState(); };

function msg(t){ document.getElementById('msg').textContent = t; }

function renderTags(){
  const box = document.getElementById('tags');
  box.innerHTML = picks.map(p => `<span class="tag">${p.name} (${Math.round(p.x)},${Math.round(p.y)})</span>`).join(' ');
  const haveDigits = new Set([...markers.map(m => m.digit), ...manualMarks.map(m => m.digit)]);
  document.getElementById('planBtn').disabled = !(haveDigits.size === 5 && picks.length >= 2);
}

function draw(){
  if (!img) return;
  const W = Math.min(cv.parentElement.clientWidth, 1000);
  const scale = W / imgW;
  cv.width = W; cv.height = Math.round(imgH * scale);
  ctx.drawImage(img, 0, 0, cv.width, cv.height);
  ctx.strokeStyle = '#00c853'; ctx.lineWidth = 2;
  picks.forEach(p => {
    const x = p.x * scale, y = p.y * scale;
    ctx.beginPath(); ctx.moveTo(x-10,y); ctx.lineTo(x+10,y); ctx.moveTo(x,y-10); ctx.lineTo(x,y+10); ctx.stroke();
    ctx.fillStyle = '#00c853'; ctx.font = '12px sans-serif'; ctx.fillText(p.name, x+12, y-10);
  });
  manualMarks.forEach(m => {
    const x = m.px * scale, y = m.py * scale;
    ctx.strokeStyle = '#ff9800'; ctx.beginPath(); ctx.arc(x, y, 14*scale+6, 0, 7); ctx.stroke();
    ctx.fillStyle = '#ff9800'; ctx.font = 'bold 14px sans-serif'; ctx.fillText(m.digit, x-5, y-14);
  });
  markers.forEach(m => {
    const x = m.px * scale, y = m.py * scale;
    ctx.strokeStyle = '#2979ff'; ctx.beginPath(); ctx.arc(x, y, 18*scale+6, 0, 7); ctx.stroke();
    ctx.fillStyle = '#2979ff'; ctx.font = 'bold 14px sans-serif'; ctx.fillText(m.digit, x-5, y-14);
  });
}
window.onresize = draw;

// ---- 进度保存：返回/刷新后自动恢复截图、地标、打卡点和结果 ----
let lastResult = null;
function saveState(){
  if (!b64) return;
  const el = id => document.getElementById(id);
  try {
    localStorage.setItem('campusState', JSON.stringify({
      b64, picks, manualMarks, markers, lastResult,
      points: el('points').value, ady: el('ady').value, adx: el('adx').value,
      start: el('start').value, startxy: el('startxy').value,
      km: el('km').value, customKm: el('customKm').value
    }));
  } catch (e) {}
}

function showResult(d){
  lastResult = d;
  document.getElementById('done').style.display = 'block';
  document.getElementById('preview').src = d.preview_url + '?t=' + Date.now();
  document.getElementById('dl').href = d.route_url;
  document.getElementById('dl').download = d.route_name + '.route.json';
  document.getElementById('dlmap').href = d.preview_url;
  document.getElementById('dlmap').download = d.preview_name || '路线图.png';
  document.getElementById('result').innerHTML =
    (d.note ? `<div style="color:#c62828;background:#ffebee;padding:6px;border-radius:6px;margin-bottom:6px">${d.note}</div>` : '') +
    `<b>里程 ${d.length_m.toFixed(1)} m</b>（目标 ${d.target_km.toFixed(1)} km，共 ${d.points} 点` +
    (d.available_km && d.available_km.length ? `，本校区可规划 ${d.available_km.join(' / ')} km` : '') + `）<br>` +
    d.checkpoints.map(c => `CP${c.digit}: ${c.lon.toFixed(6)}, ${c.lat.toFixed(6)}（贴合 ${c.pass_m} m）`).join('<br>') +
    `<br><span style="color:#666;font-size:12px">${d.route_name}</span>`;
}

document.getElementById('dl').onclick = e => {
  e.preventDefault();
  if (lastResult) saveFile(lastResult.route_url, lastResult.route_name + '.route.json');
};
document.getElementById('dlmap').onclick = e => {
  e.preventDefault();
  if (lastResult) saveFile(lastResult.preview_url, lastResult.preview_name || '路线图.png');
};

function restoreState(){
  let s = null;
  try { s = JSON.parse(localStorage.getItem('campusState') || 'null'); } catch (e) {}
  if (!s || !s.b64) return;
  b64 = s.b64; picks = s.picks || []; manualMarks = s.manualMarks || [];
  markers = s.markers || [];
  const el = id => document.getElementById(id);
  el('points').value = s.points || '150';
  el('ady').value = s.ady || '52';
  el('adx').value = s.adx || '0';
  el('startxy').value = s.startxy || '';
  if (s.start) { el('start').value = s.start; }
  el('customKm').value = s.customKm || '1000';
  if (s.km) { el('km').value = s.km; }
  img = new Image();
  img.onload = () => {
    imgW = img.naturalWidth; imgH = img.naturalHeight;
    document.getElementById('step2').style.display = 'block';
    draw(); renderTags();
    if (s.lastResult) { showResult(s.lastResult); }
    msg('已恢复上次的截图与选点，可直接继续');
  };
  img.src = b64;
}

let mkPicking = false;
document.getElementById('mkBtn').onclick = () => { mkPicking = true; msg('请点击图上该序号的打卡点位置'); };
document.getElementById('mkClear').onclick = () => { manualMarks = []; draw(); renderTags(); saveState(); };
let manualMarks = [];

// ---- 放大镜：先粗点，再在 4 倍放大图上点精确位置 ----
let zoomState = null;
const zoomBox = document.getElementById('zoombox');
const zcv = document.getElementById('zcv'), zctx = zcv.getContext('2d');
const ZOOM_R = 55;
function openZoom(sx, sy, cb){
  zoomState = {sx, sy, cb};
  zctx.imageSmoothingEnabled = false;
  const x0 = Math.max(0, Math.min(imgW - ZOOM_R*2, sx - ZOOM_R));
  const y0 = Math.max(0, Math.min(imgH - ZOOM_R*2, sy - ZOOM_R));
  zoomState.off = {x0, y0};
  zctx.clearRect(0,0,300,300);
  zctx.drawImage(img, x0, y0, ZOOM_R*2, ZOOM_R*2, 0, 0, 300, 300);
  zctx.strokeStyle = '#ff1744'; zctx.lineWidth = 1;
  zctx.beginPath(); zctx.moveTo(150,0); zctx.lineTo(150,300);
  zctx.moveTo(0,150); zctx.lineTo(300,150); zctx.stroke();
  zoomBox.style.display = 'block';
}
zcv.addEventListener('click', e => {
  if (!zoomState) return;
  const rect = zcv.getBoundingClientRect();
  const px = (e.clientX - rect.left) / rect.width * 300;
  const py = (e.clientY - rect.top) / rect.height * 300;
  const sx = zoomState.off.x0 + px / 300 * ZOOM_R*2;
  const sy = zoomState.off.y0 + py / 300 * ZOOM_R*2;
  const cb = zoomState.cb;
  zoomBox.style.display = 'none'; zoomState = null;
  cb(sx, sy);
});
document.getElementById('zcancel').onclick = () => { zoomBox.style.display='none'; zoomState=null; };

cv.addEventListener('click', e => {
  if (!img) return;
  const rect = cv.getBoundingClientRect();
  const scale = imgW / cv.width;
  const x = (e.clientX - rect.left) * scale, y = (e.clientY - rect.top) * scale;
  if (mkPicking) {
    const dg = parseInt(document.getElementById('mkdigit').value);
    openZoom(x, y, (sx, sy) => {
      manualMarks = manualMarks.filter(m => m.digit !== dg).concat([{digit: dg, px: sx, py: sy}]);
      mkPicking = false;
      renderTags(); draw(); saveState();
      msg('已手动补 ' + dg + ' 号点 (' + sx.toFixed(0) + ',' + sy.toFixed(0) + ')，会精确覆盖自动识别结果');
    });
    return;
  }
  if (!picking) return;
  const name = document.getElementById('lm').value;
  openZoom(x, y, (sx, sy) => {
    picks = picks.filter(p => p.name !== name).concat([{name, x: sx, y: sy}]);
    renderTags(); draw(); saveState();
    msg('已点选 ' + name + ' (' + sx.toFixed(0) + ',' + sy.toFixed(0) + ')，继续选下一个地标（至少 2 个，建议 3 个）');
  });
});

function accLine(d){
  if (!d || !d.calib_residual_m || !d.calib_residual_m.length) return '';
  const res = Math.max.apply(null, d.calib_residual_m.map(Number));
  let s = '\n校准残差 ' + res.toFixed(1) + ' m';
  if (d.cp_snap_m && d.cp_snap_m.length) s += '，打卡点离路网 ' + d.cp_snap_m.map(v => v == null ? '-' : v).join(' / ') + ' m';
  if (res > 10) s += '\n⚠ 残差偏大：建议再点 1 个地标（共 3 个），或用「手动补点」逐点校正';
  return s;
}

document.getElementById('kmBtn').onclick = async () => {  if (!b64) { msg('先上传截图'); return; }
  if (picks.length < 2) { msg('先点选至少 2 个地标'); return; }
  msg('计算可规划路程…');
  try {
    const r = await fetch('/api/km', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(Object.assign({image: b64, points: picks, markers: manualMarks}, extraOpts()))});
    const d = await r.json();
    if (!d.ok) { msg('计算失败：' + d.error); return; }
    const sel = document.getElementById('km');
    sel.innerHTML = '<option value="auto">自动（推荐 ' + d.suggested_km + '）</option>'
      + d.options.map(k => `<option${k === d.suggested_km ? ' selected' : ''}>${k}</option>`).join('')
      + '<option value="custom">自定义</option>';
    msg('可规划路程: ' + d.options.join(' / ') + ' km\n默认自动选择 ' + d.suggested_km
        + ' km，可下拉改选' + accLine(d));
  } catch (e) { msg('出错：' + e); }
};

document.getElementById('detectBtn').onclick = async () => {
  if (!b64) return;
  msg('识别中…');
  const r = await fetch('/api/detect', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(Object.assign({image: b64}, extraOpts()))});
  const d = await r.json();
  if (!d.ok) { msg('识别失败：' + d.error); return; }
  markers = d.markers;
  renderTags(); draw(); saveState();
  msg('识别到 ' + markers.length + ' 个打卡点：' + markers.map(m => m.digit + '号(' + m.score.toFixed(2) + ')').join('、')
      + (markers.length === 5 ? '\n锚点 dy=' + (d.anchor ? d.anchor.dy : '?') + 'px；不满意可用「手动补点」覆盖单个点位'
                             : '\n点数不足，请重新截图（避免气泡遮挡）'));
};

document.getElementById('planBtn').onclick = async () => {
  msg('规划中（约 10-30 秒）…');
  document.getElementById('planBtn').disabled = true;
  try {
    const kmSel = document.getElementById('km').value;
    let target = null;
    if (kmSel === 'custom') target = parseFloat(document.getElementById('customKm').value || '0') || null;
    else if (kmSel !== 'auto') target = parseFloat(kmSel) * 1000;
    const r = await fetch('/api/plan', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(Object.assign({image: b64, points: picks, target, markers: manualMarks,
                            basemap: document.getElementById('base').value,
                            passes: parseInt(document.getElementById('passes').value || '1')}, extraOpts()))});
    const d = await r.json();
    if (!d.ok) { msg('规划失败：' + d.error); return; }
    markers = d.markers; draw();
    showResult(d);
    saveState();
    setTimeout(() => saveFile(d.route_url, d.route_name + '.route.json'), 600);
    msg((d.note ? d.note + '\n' : '') + '完成，路线文件已自动保存（.route.json）；进度已记住，返回后不用重选' + accLine(d));
  } catch (e) { msg('出错：' + e); }
  finally { renderTags(); }
};
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "CampusRoute/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[http] " + (fmt % args) + "\n")

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            html = PAGE.replace("__CAMPUS__", PROFILE.data["name"])
            self._send(200, html, "text/html; charset=utf-8")
        elif p == "/api/profile":
            self._send(200, {"campus": PROFILE.data["name"],
                             "landmarks": [{"name": l["name"]} for l in PROFILE.landmarks],
                             "target": PROFILE.route["target_m"]})
        elif p.startswith("/files/"):
            name = unquote(p[len("/files/"):])
            path = os.path.join(OUT_DIR, name)
            if not os.path.abspath(path).startswith(os.path.abspath(OUT_DIR)) or not os.path.isfile(path):
                self._send(404, {"ok": False, "error": "not found"})
                return
            ctype = "image/png" if name.endswith(".png") else "application/json; charset=utf-8"
            with open(path, "rb") as f:
                data = f.read()
            q = parse_qs(urlparse(self.path).query)
            attach = name.endswith(".json") or q.get("dl", [""])[0] == "1"
            hdr = {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name)} if attach else None
            self._send(200, data, ctype, headers=hdr)
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def _read_body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            req = self._read_body()
            img = decode_image(req.get("image", ""))
            if p == "/api/detect":
                markers = detect_markers(img, PROFILE.templates, PROFILE.marker_colors,
                                         anchor=_anchor_from(req))
                self._send(200, {"ok": True, "markers": markers, "anchor": _anchor_from(req)})
            elif p == "/api/km":
                self._do_km(img, req)
            elif p == "/api/plan":
                self._do_plan(img, req)
            else:
                self._send(404, {"ok": False, "error": "not found"})
        except Exception as e:  # noqa
            traceback.print_exc()
            self._send(200, {"ok": False, "error": str(e)})

    def _do_km(self, img, req):
        """计算可规划的公里数列表（不生成路线文件）。"""
        points = req.get("points") or []
        if len(points) < 2:
            self._send(200, {"ok": False, "error": "至少需要 2 个地标点"})
            return
        clicks = [(p["name"], (float(p["x"]), float(p["y"]))) for p in points]
        markers = detect_markers(img, PROFILE.templates, PROFILE.marker_colors, anchor=_anchor_from(req))
        markers = _merge_manual(markers, req.get("markers") or [])
        digits = sorted(m["digit"] for m in markers)
        if digits != [1, 2, 3, 4, 5]:
            self._send(200, {"ok": False, "error": f"未能识别 5 个打卡点（识别到 {len(markers)} 个）"})
            return
        calib = calibrate(PROFILE, clicks, verbose=False)
        cps = checkpoints_from_markers(PROFILE, calib, markers)
        laps = max(0, int(req.get("passes", 1)))
        pt_count = max(70, min(200, int(req.get("route_points") or 150)))
        res = planner.plan(PROFILE, [(c["lon"], c["lat"]) for c in cps],
                           auto=True, target_points=pt_count, pass_laps=laps,
                           pass_radius_m=12.0, start_gcj=_start_from(req), log=lambda *a: None)
        self._send(200, {"ok": True, "options": res["available_km"],
                         "suggested_km": round(res["target_m"] / 1000.0, 2),
                         "calib_residual_m": [round(r, 1) for r in calib["residuals_m"]],
                         "cp_snap_m": _cp_snap_m(cps)})

    def _do_plan(self, img, req):
        points = req.get("points") or []
        if len(points) < 2:
            self._send(200, {"ok": False, "error": "至少需要 2 个地标点"})
            return
        tgt = req.get("target")
        auto = tgt in (None, "", "auto")
        target = None if auto else float(tgt)
        clicks = [(p["name"], (float(p["x"]), float(p["y"]))) for p in points]
        markers = detect_markers(img, PROFILE.templates, PROFILE.marker_colors, anchor=_anchor_from(req))
        markers = _merge_manual(markers, req.get("markers") or [])
        digits = sorted(m["digit"] for m in markers)
        if digits != [1, 2, 3, 4, 5]:
            self._send(200, {"ok": False, "error": f"未能识别 5 个打卡点（识别到 {len(markers)} 个），请重新截图"})
            return
        calib = calibrate(PROFILE, clicks, verbose=False)
        cps = checkpoints_from_markers(PROFILE, calib, markers)
        pt_count = max(70, min(200, int(req.get("route_points") or 150)))
        laps = max(0, int(req.get("passes", 1)))
        res = planner.plan(PROFILE, [(c["lon"], c["lat"]) for c in cps], target_m=target,
                           auto=auto, target_points=pt_count, pass_laps=laps,
                           pass_radius_m=12.0, start_gcj=_start_from(req), log=lambda *a: None)
        now = time.localtime()
        cn_name = f"{now.tm_year}年{now.tm_mon}月{now.tm_mday}日{now.tm_hour}时{now.tm_min}分"
        route = routejson.build_route_dict(res["route_wgs"], name=cn_name)
        route_name = route["name"]
        exact = abs(res["length_m"] - res["target_m"]) <= 2.0
        min_km = res["available_km"][0] if res.get("available_km") else None
        note = ""
        if not auto and not exact:
            below = res["length_m"] > res["target_m"] + 2.0     # 目标比最短路线还短
            note = (f"目标 {res['target_m']:.0f} m 无法精确达到："
                    + ("必须按 1→2→3→4→5 经过全部 5 个打卡点，" if below else "")
                    + f"实际生成 {res['length_m']:.0f} m")
            if below:
                note += f"（本校区最短约 {res['length_m'] / 1000.0:.1f} km）"
            else:
                note += f"（本校区最长约 {res['length_m'] / 1000.0:.1f} km）"
        route_path = os.path.join(OUT_DIR, route_name + ".route.json")
        routejson.save_route(route, route_path)
        basemap = "sat" if req.get("basemap") == "sat" else "vector"
        preview_name = route_name + ("（卫星）.png" if basemap == "sat" else ".png")
        preview_path = os.path.join(OUT_DIR, preview_name)
        routejson.render_preview(PROFILE, res["route_ds"], res["checkpoints"], preview_path,
                                 title=f"校园跑路线 · {PROFILE.data['name']} · {res['length_m']:.0f} m",
                                 start_pt=res["route_ds"][0], end_pt=res["route_ds"][-1],
                                 basemap=basemap)
        self._send(200, {
            "ok": True, "length_m": round(res["length_m"], 1), "points": res["point_count"],
            "target_km": round(res["target_m"] / 1000.0, 2),
            "available_km": res.get("available_km", []),
            "markers": markers,
            "checkpoints": [{"digit": c["digit"], "lon": round(c["lon"], 6), "lat": round(c["lat"], 6),
                             "pass_m": res["checkpoint_pass_m"][i]}
                            for i, c in enumerate(cps)],
            "calib_residual_m": [round(r, 1) for r in calib["residuals_m"]],
            "cp_snap_m": _cp_snap_m(cps),
            "exact": exact, "note": note, "min_km": min_km,
            "route_name": route_name,
            "route_url": f"/files/{quote(route_name + '.route.json')}",
            "preview_url": f"/files/{quote(preview_name)}",
            "preview_name": preview_name,
        })


def decode_image(data):
    if "," in data:
        data = data.split(",", 1)[1]
    raw = base64.b64decode(data)
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    return np.asarray(im).astype(np.uint8)


def _anchor_from(req):
    """前端可覆盖打卡点锚点偏移（数字圆圈中心 -> 尾巴尖）。"""
    a = dict(PROFILE.marker_anchor)
    for k in ("dx", "dy"):
        v = req.get("anchor_" + k)
        if v is not None and v != "":
            try:
                a[k] = float(v)
            except Exception:
                pass
    return a


def _start_from(req):
    """起点：地标名 或 'lon,lat'；空=默认（1 号点附近）。"""
    s = (req.get("start") or "").strip().replace("，", ",")
    if not s:
        return None
    lm = {l["name"]: l for l in PROFILE.landmarks}
    if s in lm:
        return (lm[s]["lon"], lm[s]["lat"])
    try:
        lon, lat = [float(v) for v in s.split(",")]
        return (lon, lat)
    except Exception:
        return None


def _merge_manual(markers, manual):
    if not manual:
        return markers
    by = {m["digit"]: m for m in markers}
    for m in manual:
        dg = int(m["digit"])
        by[dg] = dict(px=float(m["px"]), py=float(m["py"]), ax=float(m["px"]), ay=float(m["py"]),
                      digit=dg, score=1.0, color="manual")
    return [by[k] for k in sorted(by)]


def _cp_snap_m(cps):
    """每个打卡点原始坐标到路网的吸附距离（米），越小越贴合真实点位。"""
    out = []
    for c in cps:
        x, y = PROFILE.gcj_to_ds(c["lon"], c["lat"])
        s = PROFILE.snap_ds(x, y)
        out.append(round(math.hypot(s[0] - x, s[1] - y) * PROFILE.m_per_px, 1) if s else None)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"校园跑路线规划器（安卓网页版）已启动")
    print(f"  手机浏览器访问: http://127.0.0.1:{args.port}")
    print(f"  输出目录: {OUT_DIR}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
