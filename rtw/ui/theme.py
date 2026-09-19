"""QSS 主题：OKLCH 调校的暖白纸感亮色工具风（v3 配色升级）。

调色逻辑（color-expert，全部经 OKLCH→sRGB 转换 + WCAG 脚本实测）：
- 明度阶梯（OKLCH L）：bg 97.5% → panel 94.5% → panel2 90.5%，暖白底（H≈66-70 低饱和暖底），
  三级层次清晰、纸面阅读感，不发闷也不刺眼。
- 文本对比：主文 #251c15 / 底 #fcf6ee ≈ 15.6:1（远超 WCAG AAA）；
  次要文 #64584f / 底 ≈ 6.4:1（AA）。
- 麦克风/扬声器双色：青(205°) vs 紫(295°)，色相拉开 90°，明度一致；
  青 accent #008390 白字 4.52:1（AA），紫 #6e59a6 白字 5.77:1（AA）。
- 浮窗玻璃底：暖白 88% 透明 + 暖灰描边，纸面悬浮质感。
- 派生色（hover/shade）遵循「在基色上 OKLCH 调 L」原则，不手挑无关 hex。
"""

TOKENS = {
    "bg": "#fcf6ee", "panel": "#f3ebe3", "panel2": "#e8ded3",
    "hair": "#d6cabf", "txt": "#251c15", "dim": "#64584f",
    "accent": "#008390", "accent_soft": "#d8f5f8",
    "ok": "#2a8646", "warn": "#b27a00",
    "mic": "#008390", "sys": "#6e59a6",
    "grad1": "#008390", "grad2": "#6e59a6",
    # 派生（OKLCH 调 L 得到，非手挑）
    "hover": "#efe6dc",        # panel2 调 L 略升 → 按钮 hover
    "press": "#e2d6c8",        # panel2 调 L 略降 → 按压
    "scroll": "#cabfab",       # hair 调 L 略降 → 滚动条 handle
    "scroll_hover": "#b8a892", # hair 调 L 再降 → 滚动条 hover
}

MAIN_QSS = f"""
* {{ font-family: "Segoe UI","Microsoft YaHei","PingFang SC",sans-serif; }}
/* 注意：不用全局 QWidget{{background}} —— 那会让透明浮窗的子控件回退成不透明底。
   背景按具体窗口/面板显式设置（见下方 #centralRoot / #topbar / #leftPanel 等）。 */
QWidget {{ color: {TOKENS['txt']}; font-size: 13px; }}
QMainWindow {{ background: {TOKENS['bg']}; }}
#centralRoot {{ background: {TOKENS['bg']}; }}

/* ---- 顶栏 ---- */
#topbar {{ background: {TOKENS['panel']}; border-bottom: 1px solid {TOKENS['hair']}; }}
#brandLabel {{ font-weight: 600; font-size: 15px; }}
#logoBox {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
             stop:0 {TOKENS['grad1']}, stop:1 {TOKENS['grad2']}); border-radius: 8px; }}
#logoText {{ color: #ffffff; font-weight: 800; font-size: 13px; }}
#sessionPillLive {{ background: rgba(214,64,64,.12); border: 1px solid rgba(214,64,64,.35);
               border-radius: 999px; padding: 4px 12px; color: #c0392b; font-weight: 600; }}
#sessionPillPaused {{ background: rgba(178,122,0,.12); border: 1px solid rgba(178,122,0,.35);
               border-radius: 999px; padding: 4px 12px; color: #9a6600; font-weight: 600; }}
#sessionPillStopped {{ background: {TOKENS['panel2']}; border: 1px solid {TOKENS['hair']};
               border-radius: 999px; padding: 4px 12px; color: {TOKENS['dim']}; }}
#sessionPillIdle {{ background: transparent; border: 1px dashed {TOKENS['hair']};
               border-radius: 999px; padding: 4px 12px; color: {TOKENS['dim']}; }}
#timerLabel {{ font-family: Consolas,monospace; }}

/* ---- 按钮 ---- */
QPushButton {{ background: {TOKENS['panel2']}; border: 1px solid {TOKENS['hair']};
             color: {TOKENS['txt']}; padding: 7px 14px; border-radius: 9px; }}
QPushButton:hover {{ border-color: {TOKENS['scroll']}; background: {TOKENS['hover']}; }}
QPushButton:checked {{ background: {TOKENS['accent_soft']}; border-color: {TOKENS['accent']};
                      color: {TOKENS['accent']}; font-weight: 600; }}
QPushButton:disabled {{ opacity: .45; }}
QPushButton#primaryBtn {{ background: {TOKENS['accent']}; border: none;
                         color: #ffffff; font-weight: 600; }}
QPushButton#primaryBtn:hover {{ background: #0a9aa8; }}
QPushButton#playBtn {{ background: {TOKENS['accent']}; border: none; color: #ffffff;
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
    border-radius: 8px; padding: 6px 10px; color: {TOKENS['txt']}; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border-color: {TOKENS['accent']}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {TOKENS['panel2']}; border: 1px solid {TOKENS['hair']};
    selection-background-color: {TOKENS['accent_soft']}; selection-color: {TOKENS['accent']}; }}
QCheckBox {{ spacing: 8px; color: {TOKENS['txt']}; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px;
    border: 1px solid {TOKENS['hair']}; background: {TOKENS['bg']}; }}
QCheckBox::indicator:checked {{ background: {TOKENS['accent']}; border-color: {TOKENS['accent']}; }}

/* ---- 字幕舞台 ---- */
#stage {{ background: qradialgradient(cx:0.5,cy:1.2,rx:0.9,ry:0.9,
             stop:0 #f3ebe3, stop:0.65 {TOKENS['bg']}); }}
#subHeader {{ background: {TOKENS['panel']}; border-bottom: 1px solid {TOKENS['hair']}; }}
#tabBase QPushButton {{ background: transparent; border: none; color: {TOKENS['dim']};
    padding: 6px 14px; border-radius: 8px; }}
#tabBase QPushButton:checked {{ background: {TOKENS['panel2']}; color: {TOKENS['txt']};
    font-weight: 600; }}
#latencyLbl {{ color: {TOKENS['dim']}; font-family: Consolas,monospace; font-size: 12px; }}
#latVal {{ color: {TOKENS['ok']}; font-weight: 600; }}

/* ---- 字幕行 ---- */
QFrame#subLine {{ background: transparent; border-radius: 10px; margin: 3px 0; }}
QFrame#subLine:hover {{ background: rgba(0,0,0,.03); }}
#tagMic {{ background: {TOKENS['accent_soft']}; color: {TOKENS['mic']};
          border-radius: 7px; font-weight: 700; font-size: 11px; padding: 4px 0; }}
#tagSys {{ background: #efebff; color: {TOKENS['sys']};
          border-radius: 7px; font-weight: 700; font-size: 11px; padding: 4px 0; }}
#srcText {{ font-size: 20px; font-weight: 500; color: {TOKENS['txt']}; }}
#srcTextInterim {{ font-size: 20px; color: {TOKENS['dim']}; }}
#trText {{ font-size: 14px; color: {TOKENS['dim']}; }}
#tsText {{ font-family: Consolas,monospace; font-size: 11px; color: #9a8f82; }}

/* ---- 底部 dock ---- */
#dock {{ background: {TOKENS['panel']}; border-top: 1px solid {TOKENS['hair']}; }}
#statK {{ color: {TOKENS['dim']}; font-size: 11px; letter-spacing: .8px; }}
#statV {{ font-family: Consolas,monospace; font-size: 13.5px; font-weight: 600; color: {TOKENS['txt']}; }}

/* ---- 状态 toast ---- */
#toast {{ background: {TOKENS['panel2']}; border: 1px solid {TOKENS['hair']};
         border-radius: 10px; padding: 8px 14px; }}
#toastMsg {{ font-size: 12.5px; color: {TOKENS['txt']}; }}
#toastEta {{ color: {TOKENS['dim']}; font-size: 11px; font-family: Consolas,monospace; }}

/* ---- 滚动条 ---- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {TOKENS['scroll']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {TOKENS['scroll_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QProgressBar {{ background: {TOKENS['hair']}; border: none; border-radius: 2px;
               text-align: center; height: 4px; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
               stop:0 {TOKENS['grad1']}, stop:1 {TOKENS['grad2']}); border-radius: 2px; }}
"""

# 浮窗：无边框 + WA_TranslucentBackground，样式内联在 widget 属性里
# 玻璃底：暖白 88% 透明 + 暖灰描边（纸面悬浮质感）
OVERLAY_QSS = f"""
QWidget {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar {{ background: transparent; }}
QFrame#ovWin {{ background: rgba(252,246,238,.88); border: 1px solid rgba(0,0,0,.10);
               border-radius: 18px; }}
QFrame#ovWin[data-theme="solid"] {{ background: rgba(252,246,238,.96); }}
QFrame#ovWin[data-theme="outline"] {{ background: transparent;
               border: 1.5px solid rgba(0,0,0,.28); }}
QFrame#ovWin[data-theme="light"] {{ background: rgba(252,246,238,.92); }}
#ovDot {{ background: {TOKENS['ok']}; border-radius: 4px; }}
#ovTitle {{ color: rgba(37,28,21,.5); font-size: 11px; letter-spacing: 1.5px; }}
#ovMeta {{ color: rgba(37,28,21,.45); font-size: 11px; font-family: Consolas,monospace; }}
#ovMetaVal {{ color: rgba(37,28,21,.8); font-weight: 600; }}
QPushButton#cbtn {{ width: 26px; height: 26px; border-radius: 8px;
    border: 1px solid rgba(0,0,0,.12); background: rgba(0,0,0,.05);
    color: rgba(37,28,21,.6); padding: 0; font-size: 12px; }}
QPushButton#cbtn:hover {{ background: rgba(0,0,0,.12); color: {TOKENS['txt']}; }}
QFrame#ovLine {{ background: transparent; }}
#ovTagMic {{ background: {TOKENS['accent_soft']}; color: {TOKENS['mic']};
            border-radius: 6px; font-weight: 700; font-size: 10px; padding: 3px 0; }}
#ovTagSys {{ background: #efebff; color: {TOKENS['sys']};
            border-radius: 6px; font-weight: 700; font-size: 10px; padding: 3px 0; }}
#secMic {{ color: {TOKENS['mic']}; font-size: 11px; font-weight: 700;
         letter-spacing: 1px; margin-top: 4px; }}
#secSys {{ color: {TOKENS['sys']}; font-size: 11px; font-weight: 700;
         letter-spacing: 1px; margin-top: 4px; }}
#ovSrc {{ font-size: 22px; font-weight: 600; color: {TOKENS['txt']}; }}
#ovSrcInterim {{ font-size: 22px; color: {TOKENS['dim']}; }}
#ovTr {{ font-size: 14.5px; color: {TOKENS['dim']}; }}
#ovTs {{ font-family: Consolas,monospace; font-size: 10px; color: rgba(37,28,21,.35); }}
"""

# 浅色主题的文本颜色覆盖（暖白底，文本用暖炭）
LIGHT_OVERRIDE = """
#ovSrc, #ovSrcInterim { color: #251c15; }
#ovTr { color: #64584f; }
#ovTitle, #ovMeta { color: rgba(37,28,21,.5); }
#ovMetaVal { color: rgba(37,28,21,.8); }
"""

SPLASH_QSS = f"""
/* 注意：QDialog 上不认 qradialgradient（会画成纯黑，Qt 已知怪癖），
   改用垂直 qlineargradient（暖米 → 暖白，自上而下）。 */
QDialog {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
    stop:0 #f3ebe3, stop:1 {TOKENS['bg']}); }}
#splashLogo {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
    stop:0 {TOKENS['grad1']}, stop:1 {TOKENS['grad2']}); border-radius: 14px;
    color: #ffffff; font-weight: 800; font-size: 18px; }}
#splashTitle {{ font-size: 24px; font-weight: 700; color: {TOKENS['txt']}; }}
#splashTagline {{ color: {TOKENS['dim']}; font-size: 12px; }}
#splashStep {{ color: {TOKENS['dim']}; font-size: 14px; }}
#splashDetail {{ color: #9a8f82; font-size: 12px; font-family: Consolas,monospace; }}
#stepRow {{ background: transparent; }}
QProgressBar {{ background: {TOKENS['hair']}; border: none; border-radius: 3px;
               text-align: center; height: 6px; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
               stop:0 {TOKENS['grad1']}, stop:1 {TOKENS['grad2']}); border-radius: 3px; }}
QPushButton#retryBtn {{ background: {TOKENS['accent']}; border: none;
    color: #ffffff; font-weight: 600; padding: 8px 24px; border-radius: 8px; }}
QPushButton#retryBtn:hover {{ background: #0a9aa8; }}
QPushButton#quitBtn {{ background: {TOKENS['panel2']}; border: 1px solid {TOKENS['hair']};
    color: {TOKENS['dim']}; padding: 8px 24px; border-radius: 8px; }}
QPushButton#quitBtn:hover {{ color: {TOKENS['txt']}; border-color: {TOKENS['scroll']}; }}
"""
