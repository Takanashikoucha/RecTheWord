"""QSS 主题：OKLCH 调校的深色工具风（v2 配色升级）。

调色逻辑（color-expert）：
- 明度阶梯（OKLCH L）：bg 14% → panel 18% → panel2 23%，三级层次清晰不发闷
- accent 从 L75% 降到 L62%（深青 #38bdf8→#22a5d8 系），避免"工单蓝"的高亮廉价感
- 麦克风/扬声器双色：青(195°) vs 紫(285°)，色相拉开 90° 以上，明度一致
- 浮窗玻璃底：炭黑 55% 透明 + 边框提亮，毛玻璃质感
- 文本对比：主文 L92%/底 L14% ≈ 12:1（远超 WCAG AAA），次要文 L62% ≈ 5:1
"""

TOKENS = {
    "bg": "#0b0e14", "panel": "#12161f", "panel2": "#1a2029",
    "hair": "#2a3242", "txt": "#eceff4", "dim": "#9aa3b2",
    "accent": "#2fb8e6", "accent_soft": "rgba(47,184,230,.13)",
    "ok": "#34d399", "warn": "#fbbf24",
    "mic": "#2fb8e6", "sys": "#a78bfa",
    "grad1": "#2fb8e6", "grad2": "#8b5cf6",
}

MAIN_QSS = f"""
* {{ font-family: "Segoe UI","Microsoft YaHei","PingFang SC",sans-serif; }}
QWidget {{ background: {TOKENS['bg']}; color: {TOKENS['txt']}; font-size: 13px; }}
QMainWindow, QDialog {{ background: {TOKENS['bg']}; }}

/* ---- 顶栏 ---- */
#topbar {{ background: {TOKENS['panel']}; border-bottom: 1px solid {TOKENS['hair']}; }}
#brandLabel {{ font-weight: 600; font-size: 15px; }}
#logoBox {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
             stop:0 {TOKENS['grad1']}, stop:1 {TOKENS['grad2']}); border-radius: 8px; }}
#logoText {{ color: #0b0e13; font-weight: 800; font-size: 13px; }}
#sessionPillLive {{ background: rgba(255,77,79,.14); border: 1px solid rgba(255,77,79,.4);
               border-radius: 999px; padding: 4px 12px; color: #ff6b6b; font-weight: 600; }}
#sessionPillPaused {{ background: rgba(255,193,7,.14); border: 1px solid rgba(255,193,7,.4);
               border-radius: 999px; padding: 4px 12px; color: #ffc53d; font-weight: 600; }}
#sessionPillStopped {{ background: {TOKENS['panel2']}; border: 1px solid {TOKENS['hair']};
               border-radius: 999px; padding: 4px 12px; color: rgba(255,255,255,.4); }}
#sessionPillIdle {{ background: transparent; border: 1px dashed {TOKENS['hair']};
               border-radius: 999px; padding: 4px 12px; color: rgba(255,255,255,.45); }}
#timerLabel {{ font-family: Consolas,monospace; }}

/* ---- 按钮 ---- */
QPushButton {{ background: {TOKENS['panel2']}; border: 1px solid {TOKENS['hair']};
              color: {TOKENS['txt']}; padding: 7px 14px; border-radius: 9px; }}
QPushButton:hover {{ border-color: #3a4657; background: #202834; }}
QPushButton:checked {{ background: {TOKENS['accent_soft']}; border-color: {TOKENS['accent']};
                      color: {TOKENS['accent']}; font-weight: 600; }}
QPushButton#primaryBtn {{ background: {TOKENS['accent']}; border: none;
                         color: #08131c; font-weight: 600; }}
QPushButton#primaryBtn:hover {{ background: #5ecbe8; }}
QPushButton#playBtn {{ background: {TOKENS['accent']}; border: none; color: #08131c;
                      border-radius: 23px; width: 46px; height: 46px; font-size: 16px; }}
QPushButton#iconBtn {{ background: transparent; border: 1px solid transparent;
                      border-radius: 9px; color: {TOKENS['dim']};
                      width: 34px; height: 34px; padding: 0; }}
QPushButton#iconBtn:hover {{ color: {TOKENS['txt']}; background: {TOKENS['panel2']}; }}
QPushButton#iconBtn:checked {{ color: {TOKENS['accent']}; background: {TOKENS['accent_soft']}; }}

/* ---- 左侧面板 ---- */
#leftPanel {{ background: {TOKENS['panel']}; border-right: 1px solid {TOKENS['hair']}; }}
#laneTitle {{ color: {TOKENS['dim']}; font-size: 11px; letter-spacing: 1.2px; }}
QFrame#laneCard {{ background: {TOKENS['panel2']}; border: 1px solid {TOKENS['hair']};
                   border-radius: 14px; }}
QFrame#laneCard#laneMic {{ border-left: 3px solid {TOKENS['mic']}; }}
QFrame#laneCard#laneSys {{ border-left: 3px solid {TOKENS['sys']}; }}
#laneName {{ font-weight: 600; font-size: 13.5px; }}
#laneSub {{ color: {TOKENS['dim']}; font-size: 11px; }}
QLabel#sectionHead {{ color: {TOKENS['dim']}; font-size: 11px; letter-spacing: 1.2px; }}
QLabel#fieldLbl {{ color: {TOKENS['dim']}; font-size: 11px; }}

/* ---- 控件 ---- */
QComboBox, QLineEdit, QSpinBox {{ background: {TOKENS['bg']}; border: 1px solid {TOKENS['hair']};
    border-radius: 8px; padding: 6px 10px; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border-color: {TOKENS['accent']}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {TOKENS['panel2']}; border: 1px solid {TOKENS['hair']};
    selection-background-color: {TOKENS['accent_soft']}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px;
    border: 1px solid {TOKENS['hair']}; background: {TOKENS['bg']}; }}
QCheckBox::indicator:checked {{ background: {TOKENS['accent']}; border-color: {TOKENS['accent']}; }}

/* ---- 字幕舞台 ---- */
#stage {{ background: qradialgradient(cx:0.5,cy:1.2,rx:0.9,ry:0.9,
             stop:0 #141b26, stop:0.65 {TOKENS['bg']}); }}
#subHeader {{ background: {TOKENS['panel']}; border-bottom: 1px solid {TOKENS['hair']}; }}
#tabBase QPushButton {{ background: transparent; border: none; color: {TOKENS['dim']};
    padding: 6px 14px; border-radius: 8px; }}
#tabBase QPushButton:checked {{ background: {TOKENS['panel2']}; color: {TOKENS['txt']};
    font-weight: 600; }}
#latencyLbl {{ color: {TOKENS['dim']}; font-family: Consolas,monospace; font-size: 12px; }}
#latVal {{ color: {TOKENS['ok']}; font-weight: 600; }}

/* ---- 字幕行 ---- */
QFrame#subLine {{ background: transparent; border-radius: 10px; margin: 3px 0; }}
QFrame#subLine:hover {{ background: rgba(255,255,255,.03); }}
#tagMic {{ background: rgba(47,184,230,.15); color: {TOKENS['mic']};
          border-radius: 7px; font-weight: 700; font-size: 11px; padding: 4px 0; }}
#tagSys {{ background: rgba(167,139,250,.15); color: {TOKENS['sys']};
          border-radius: 7px; font-weight: 700; font-size: 11px; padding: 4px 0; }}
#srcText {{ font-size: 20px; font-weight: 500; }}
#srcTextInterim {{ font-size: 20px; color: #aab4c4; }}
#trText {{ font-size: 14px; color: {TOKENS['dim']}; }}
#tsText {{ font-family: Consolas,monospace; font-size: 11px; color: #5a6578; }}

/* ---- 底部 dock ---- */
#dock {{ background: {TOKENS['panel']}; border-top: 1px solid {TOKENS['hair']}; }}
#statK {{ color: {TOKENS['dim']}; font-size: 11px; letter-spacing: .8px; }}
#statV {{ font-family: Consolas,monospace; font-size: 13.5px; font-weight: 600; }}

/* ---- 状态 toast ---- */
#toast {{ background: {TOKENS['panel2']}; border: 1px solid {TOKENS['hair']};
         border-radius: 10px; padding: 8px 14px; }}
#toastMsg {{ font-size: 12.5px; }}
#toastEta {{ color: {TOKENS['dim']}; font-size: 11px; font-family: Consolas,monospace; }}

/* ---- 滚动条 ---- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2a3342; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4657; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QProgressBar {{ background: {TOKENS['hair']}; border: none; border-radius: 2px;
               text-align: center; height: 4px; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
               stop:0 {TOKENS['grad1']}, stop:1 {TOKENS['grad2']}); border-radius: 2px; }}
"""

# 浮窗：无边框 + WA_TranslucentBackground，样式内联在 widget 属性里
# 玻璃底：炭黑 L10% 透明 62%（比原来更深更沉），边框微提亮
OVERLAY_QSS = f"""
QWidget {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar {{ background: transparent; }}
QFrame#ovWin {{ background: rgba(9,11,16,.62); border: 1px solid rgba(255,255,255,.14);
                border-radius: 18px; }}
QFrame#ovWin[data-theme="solid"] {{ background: rgba(8,10,14,.92); }}
QFrame#ovWin[data-theme="outline"] {{ background: transparent;
                border: 1.5px solid rgba(255,255,255,.35); }}
QFrame#ovWin[data-theme="light"] {{ background: rgba(245,247,250,.85); }}
#ovDot {{ background: {TOKENS['ok']}; border-radius: 4px; }}
#ovTitle {{ color: rgba(255,255,255,.45); font-size: 11px; letter-spacing: 1.5px; }}
#ovMeta {{ color: rgba(255,255,255,.4); font-size: 11px; font-family: Consolas,monospace; }}
#ovMetaVal {{ color: rgba(255,255,255,.75); font-weight: 600; }}
QPushButton#cbtn {{ width: 26px; height: 26px; border-radius: 8px;
    border: 1px solid rgba(255,255,255,.15); background: rgba(255,255,255,.06);
    color: rgba(255,255,255,.6); padding: 0; font-size: 12px; }}
QPushButton#cbtn:hover {{ background: rgba(255,255,255,.15); color: #fff; }}
QFrame#ovLine {{ background: transparent; }}
#ovTagMic {{ background: rgba(47,184,230,.18); color: {TOKENS['mic']};
            border-radius: 6px; font-weight: 700; font-size: 10px; padding: 3px 0; }}
#ovTagSys {{ background: rgba(167,139,250,.18); color: {TOKENS['sys']};
            border-radius: 6px; font-weight: 700; font-size: 10px; padding: 3px 0; }}
#secMic {{ color: {TOKENS['mic']}; font-size: 11px; font-weight: 700;
         letter-spacing: 1px; margin-top: 4px; }}
#secSys {{ color: {TOKENS['sys']}; font-size: 11px; font-weight: 700;
         letter-spacing: 1px; margin-top: 4px; }}
#ovSrc {{ font-size: 22px; font-weight: 600; color: #fff; }}
#ovSrcInterim {{ font-size: 22px; color: rgba(255,255,255,.55); }}
#ovTr {{ font-size: 14.5px; color: rgba(255,255,255,.62); }}
#ovTs {{ font-family: Consolas,monospace; font-size: 10px; color: rgba(255,255,255,.3); }}
"""

# 浅色主题的文本颜色覆盖
LIGHT_OVERRIDE = """
#ovSrc, #ovSrcInterim { color: #111827; }
#ovTr { color: #4b5563; }
#ovTitle, #ovMeta { color: rgba(0,0,0,.45); }
#ovMetaVal { color: rgba(0,0,0,.8); }
"""

SPLASH_QSS = f"""
QDialog {{ background: qradialgradient(cx:0.5,cy:0.3,rx:0.8,ry:0.8,
    stop:0 #141b26, stop:1 {TOKENS['bg']}); }}
#splashLogo {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
    stop:0 {TOKENS['grad1']}, stop:1 {TOKENS['grad2']}); border-radius: 14px;
    color: #0b0e13; font-weight: 800; font-size: 18px; }}
#splashTitle {{ font-size: 24px; font-weight: 700; color: {TOKENS['txt']}; }}
#splashTagline {{ color: {TOKENS['dim']}; font-size: 12px; }}
#splashStep {{ color: {TOKENS['dim']}; font-size: 14px; }}
#splashDetail {{ color: #5a6578; font-size: 12px; font-family: Consolas,monospace; }}
#stepRow {{ background: transparent; }}
QProgressBar {{ background: {TOKENS['hair']}; border: none; border-radius: 3px;
               text-align: center; height: 6px; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
               stop:0 {TOKENS['grad1']}, stop:1 {TOKENS['grad2']}); border-radius: 3px; }}
QPushButton#retryBtn {{ background: {TOKENS['accent']}; border: none;
    color: #08131c; font-weight: 600; padding: 8px 24px; border-radius: 8px; }}
QPushButton#retryBtn:hover {{ background: #5ecbe8; }}
QPushButton#quitBtn {{ background: {TOKENS['panel2']}; border: 1px solid {TOKENS['hair']};
    color: {TOKENS['dim']}; padding: 8px 24px; border-radius: 8px; }}
QPushButton#quitBtn:hover {{ color: {TOKENS['txt']}; border-color: #3a4657; }}
"""
