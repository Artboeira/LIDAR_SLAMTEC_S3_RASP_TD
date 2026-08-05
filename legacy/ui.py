"""
LidarMapper — Control Panel.

Janela CustomTkinter que gerencia o pipeline (`main.py`) como subprocess.

Etapas implementadas até aqui:
  A — esqueleto: botões Start/Stop + log viewer + lifecycle do subprocess
  B — status ao vivo: parsing do stdout do main e indicadores coloridos
      (LIDAR, baseline, tracks, UDP, calibração)

Tudo roda em processo separado do pipeline — o front fala com o backend só
via stdin/stdout/sinais. main.py e calibrate.py continuam standalone.
"""
from __future__ import annotations

import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time

import customtkinter as ctk

import config as cfg_mod
import paths

HERE = os.path.dirname(os.path.abspath(__file__))
CALIB_PATH = paths.CALIB_PATH
DISPATCHER_PATH = os.path.join(HERE, "lidarmapper.py")

# (key, label, mode, needs_calib)
TOOLS = [
    ("test_viz", "📷 Test Viz", "test_viz", False),
    ("test_tracker", "🎯 Test Tracker", "test_tracker", True),
    ("test_calib", "📐 Test Calib", "test_calib", True),
]


def _spawn_cmd(mode: str) -> list[str]:
    """Monta o comando pra spawn de um subprograma (main/calibrate/test_*).

    Quando empacotado (PyInstaller), `sys.executable` aponta pro próprio
    .exe e os módulos `.py` ficam embutidos — então usamos o dispatcher
    via argv: `LidarMapper.exe <mode>`.

    Em dev, rodamos `python lidarmapper.py <mode>` (ou `python <mode>.py`
    como fallback se o dispatcher não existir, mas ele sempre existe).
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, mode]
    return [sys.executable, "-u", DISPATCHER_PATH, mode]


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

C_OK = "#5fdb8c"
C_WAIT = "#e7c75a"
C_IDLE = "#7a7a85"
C_FAIL = "#e36868"
C_INFO = "#8ab6ff"

RE_CALIB = re.compile(r"calib:\s*(\S+)\s+tela\s+(\d+)x(\d+)")
RE_LIDAR_OK = re.compile(r"LIDAR:\s*conectado @\s*(\S+)")
RE_LIDAR_FAIL = re.compile(r"LIDAR n[ãa]o iniciou:\s*(.+)")
RE_BASELINE_START = re.compile(r"baseline\s+([\d.]+)s")
RE_BASELINE_DONE = re.compile(r"baseline pronto \((\d+)/(\d+) bins\)")
RE_BASELINE_PARTIAL = re.compile(r"baseline\.\.\.\s*(\d+)/(\d+) bins")
RE_UDP_START = re.compile(r"UDP\s*->\s*(\S+)\s+rate=(\S+)\s*Hz")
RE_STATS = re.compile(
    r"meas/s=\s*(\d+).*?scans/s=\s*([\d.]+).*?fg=\s*(\d+).*?tracks=(\d+)"
    r".*?pub/s=\s*([\d.]+).*?desync=(\d+).*?recon=(\d+)"
)
RE_NO_CALIB = re.compile(r"sem calibra[cç]ão em\s*(\S+)")
RE_ENCERRADO = re.compile(r"encerrado\.")


def empty_state() -> dict:
    # RECONSTRUÇÃO: pycdas imprime 0 e 0.0 igual; os campos de taxa usam a
    # constante float (meas_s/scans_s/pub_s/udp_rate = 0.0), contadores int.
    return {
        "running": False,
        "ended": False,
        "lidar_state": "idle",
        "lidar_port": "—",
        "lidar_err": "",
        "baseline_state": "idle",
        "bins": 0,
        "bins_total": 720,
        "meas_s": 0.0,
        "scans_s": 0.0,
        "fg": 0,
        "tracks": 0,
        "pub_s": 0.0,
        "desync": 0,
        "recon": 0,
        "udp_endpoint": "—",
        "udp_rate": 0.0,
        "calib_loaded": False,
        "calib_name": "—",
        "calib_w": 0,
        "calib_h": 0,
        "calib_mtime": "",
    }


def parse_log_line(line: str, state: dict) -> None:
    """Aplica regexes na linha e atualiza `state` in-place."""
    m = RE_LIDAR_OK.search(line)
    if m:
        state["lidar_state"] = "ok"
        state["lidar_port"] = m.group(1)
        return
    m = RE_LIDAR_FAIL.search(line)
    if m:
        state["lidar_state"] = "fail"
        state["lidar_err"] = m.group(1).strip()
        return
    m = RE_BASELINE_START.search(line)
    if m:
        state["baseline_state"] = "learning"
        state["bins"] = 0
        return
    m = RE_BASELINE_DONE.search(line)
    if m:
        state["baseline_state"] = "ready"
        state["bins"] = int(m.group(1))
        state["bins_total"] = int(m.group(2))
        return
    m = RE_BASELINE_PARTIAL.search(line)
    if m:
        state["baseline_state"] = "learning"
        state["bins"] = int(m.group(1))
        state["bins_total"] = int(m.group(2))
        return
    m = RE_UDP_START.search(line)
    if m:
        state["udp_endpoint"] = m.group(1)
        try:
            state["udp_rate"] = float(m.group(2))
        except ValueError:
            pass
        return
    m = RE_CALIB.search(line)
    if m:
        state["calib_loaded"] = True
        state["calib_name"] = m.group(1)
        state["calib_w"] = int(m.group(2))
        state["calib_h"] = int(m.group(3))
        return
    m = RE_NO_CALIB.search(line)
    if m:
        state["calib_loaded"] = False
        return
    m = RE_STATS.search(line)
    if m:
        state["meas_s"] = float(m.group(1))
        state["scans_s"] = float(m.group(2))
        state["fg"] = int(m.group(3))
        state["tracks"] = int(m.group(4))
        state["pub_s"] = float(m.group(5))
        state["desync"] = int(m.group(6))
        state["recon"] = int(m.group(7))
        return
    if RE_ENCERRADO.search(line):
        state["ended"] = True
        return


class StatusRow(ctk.CTkFrame):
    def __init__(self, master, name: str):
        super().__init__(master, fg_color="transparent")
        self.dot = ctk.CTkLabel(
            self, text="●", text_color=C_IDLE, width=14,
            font=ctk.CTkFont(size=18))
        self.dot.grid(row=0, column=0, padx=(6, 8), pady=2, sticky="w")
        self.name = ctk.CTkLabel(
            self, text=name, anchor="w", width=80,
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"))
        self.name.grid(row=0, column=1, padx=(0, 12), sticky="w")
        self.value = ctk.CTkLabel(
            self, text="—", anchor="w",
            font=ctk.CTkFont(family="Consolas", size=13))
        self.value.grid(row=0, column=2, sticky="w")
        self.grid_columnconfigure(2, weight=1)

    def set(self, text: str, color: str = C_OK) -> None:
        self.dot.configure(text_color=color)
        self.value.configure(text=text)


class SettingsPanel(ctk.CTkFrame):
    """Editor visual do config.yaml. Tabs por seção; Save grava, Apply
    grava e (se on_apply suportar) reinicia o pipeline."""

    def __init__(self, master, cfg: cfg_mod.Config, on_save, on_apply,
                 log_callback):
        super().__init__(master)
        self.cfg = cfg
        self.on_save = on_save
        self.on_apply = on_apply
        self.log = log_callback
        self.fields = {}
        self._build()

    def _entry(self, parent, value, width: int = 150, nullable: bool = False):
        w = ctk.CTkEntry(parent, width=width,
                         placeholder_text="(auto)" if nullable else "")
        if value is not None:
            w.insert(0, str(value))
        return w

    def _switch(self, parent, value: bool):
        w = ctk.CTkSwitch(parent, text="")
        if value:
            w.select()
            return w
        w.deselect()
        return w

    def _choice(self, parent, value, choices: list[str], width: int = 150):
        w = ctk.CTkOptionMenu(parent, values=choices, width=width)
        if value in choices:
            w.set(value)
            return w
        w.set(choices[0])
        return w

    def _add_row(self, parent, row: int, label: str, widget, hint: str = ""):
        ctk.CTkLabel(
            parent, text=label, anchor="w", width=180,
            font=ctk.CTkFont(family="Consolas", size=12),
        ).grid(row=row, column=0, sticky="w", padx=(8, 4), pady=4)
        widget.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        if hint:
            ctk.CTkLabel(
                parent, text=hint, anchor="w", text_color="#888",
                font=ctk.CTkFont(family="Consolas", size=11),
            ).grid(row=row, column=2, sticky="w", padx=(8, 4))

    def _register_field(self, key: str, widget, kind: str) -> None:
        self.fields[key] = {"widget": widget, "kind": kind}

    def _build(self) -> None:
        ctk.CTkLabel(
            self, text="Settings",
            font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 4))
        tabs = ctk.CTkTabview(self, height=240)
        tabs.pack(fill="x", padx=10, pady=(0, 4))
        for name in ("Sensor", "Processing", "ROI", "Tracker", "Screen", "UDP"):
            tabs.add(name)
        self._build_sensor(tabs.tab("Sensor"))
        self._build_processing(tabs.tab("Processing"))
        self._build_roi(tabs.tab("ROI"))
        self._build_tracker(tabs.tab("Tracker"))
        self._build_screen(tabs.tab("Screen"))
        self._build_udp(tabs.tab("UDP"))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=10, pady=(2, 8))
        self.btn_save = ctk.CTkButton(
            btns, text="💾 Save", width=120, height=32,
            command=self._on_save_clicked)
        self.btn_save.pack(side="right", padx=4)
        self.btn_apply = ctk.CTkButton(
            btns, text="↻ Apply (Save + Restart)", width=200, height=32,
            fg_color="#4d7d4d", hover_color="#5e9258",
            command=self._on_apply_clicked)
        self.btn_apply.pack(side="right", padx=4)
        self.btn_reload = ctk.CTkButton(
            btns, text="Reload from file", width=140, height=32,
            fg_color="#3a3a3a", hover_color="#555",
            command=self._on_reload_clicked)
        self.btn_reload.pack(side="left", padx=4)

    def _build_sensor(self, p) -> None:
        c = self.cfg.sensor
        e = self._entry(p, c.port, nullable=True)
        self._register_field("sensor.port", e, "str?")
        self._add_row(p, 0, "port", e, "vazio = auto (CP210x)")
        e = self._entry(p, c.baud)
        self._register_field("sensor.baud", e, "int")
        self._add_row(p, 1, "baud", e, "S3 = 1000000")

    def _build_processing(self, p) -> None:
        c = self.cfg.processing
        rows = [
            ("min_dist_mm", "float", "ignora retornos colados"),
            ("max_dist_mm", "float", "ignora além desse alcance"),
            ("min_quality", "int", "0 = aceita tudo"),
            ("angle_offset_deg", "float", "rotaciona o sensor"),
            ("mirror", "bool", "espelha eixo X"),
        ]
        for i, (name, kind, hint) in enumerate(rows):
            value = getattr(c, name)
            if kind == "bool":
                w = self._switch(p, value)
            else:
                w = self._entry(p, value)
            self._register_field(f"processing.{name}", w, kind)
            self._add_row(p, i, name, w, hint)

    def _build_roi(self, p) -> None:
        c = self.cfg.roi
        rows = [
            ("x_min", "x mínimo"),
            ("x_max", "x máximo"),
            ("y_min", "y mínimo"),
            ("y_max", "y máximo"),
        ]
        for i, (name, hint) in enumerate(rows):
            e = self._entry(p, getattr(c, name), nullable=True)
            self._register_field(f"roi.{name}", e, "float?")
            self._add_row(p, i, name + " (mm)", e, f"{hint}; vazio = sem limite")

    def _build_tracker(self, p) -> None:
        c = self.cfg.tracker
        rows = [
            ("dbscan_eps_mm", "float", "raio vizinhança"),
            ("dbscan_min_samples", "int", "min pontos por cluster"),
            ("match_dist_mm", "float", "max ligação cluster→track"),
            ("timeout_s", "float", "track perdido após"),
            ("max_tracks", "int", "limite de cursores"),
            ("confidence_frames", "int", "frames até conf=1"),
            ("smoothing", "float", "0=sem, 1=congelado"),
        ]
        for i, (name, kind, hint) in enumerate(rows):
            value = getattr(c, name)
            w = self._entry(p, value)
            self._register_field(f"tracker.{name}", w, kind)
            self._add_row(p, i, name, w, hint)

    SCREEN_PRESETS = [
        "Custom",
        "1920x1080 (FullHD)",
        "1920x1200",
        "2560x1440 (QHD)",
        "3840x2160 (4K UHD)",
    ]

    @staticmethod
    def _detect_preset(w: int, h: int) -> str:
        for label in SettingsPanel.SCREEN_PRESETS[1:]:
            spec = label.split(" ", 1)[0]
            pw, ph = (int(x) for x in spec.split("x"))
            if (pw, ph) == (w, h):
                return label
        return "Custom"

    def _build_screen(self, p) -> None:
        c = self.cfg.screen
        self._preset_menu = ctk.CTkOptionMenu(
            p, values=self.SCREEN_PRESETS, width=200,
            command=self._on_preset_change)
        self._preset_menu.set(self._detect_preset(c.width_px, c.height_px))
        self._add_row(p, 0, "preset", self._preset_menu,
                      "preenche width/height abaixo")
        e = self._entry(p, c.width_px)
        self._register_field("screen.width_px", e, "int")
        e.bind("<KeyRelease>", lambda _ev: self._sync_preset_from_entries())
        self._add_row(p, 1, "width_px", e, "resolução da tela alvo")
        e = self._entry(p, c.height_px)
        self._register_field("screen.height_px", e, "int")
        e.bind("<KeyRelease>", lambda _ev: self._sync_preset_from_entries())
        self._add_row(p, 2, "height_px", e, "homografia é 0..1, só metadata")
        e = self._entry(p, c.display_index)
        self._register_field("screen.display_index", e, "int")
        self._add_row(p, 3, "display_index", e, "0=primário, 1=secundário")
        w = self._switch(p, c.fullscreen)
        self._register_field("screen.fullscreen", w, "bool")
        self._add_row(p, 4, "fullscreen", w, "calibração em fullscreen")
        btn = ctk.CTkButton(
            p, text="Update calibration.json metadata", height=28,
            fg_color="#3a3a3a", hover_color="#555",
            command=self._update_calibration_metadata)
        btn.grid(row=5, column=0, columnspan=3, sticky="w", padx=8,
                 pady=(10, 4))

    def _on_preset_change(self, choice: str) -> None:
        if choice == "Custom":
            return
        spec = choice.split(" ", 1)[0]
        try:
            w_str, h_str = spec.split("x")
        except ValueError:
            return
        w = self.fields["screen.width_px"]["widget"]
        h = self.fields["screen.height_px"]["widget"]
        w.delete(0, "end")
        w.insert(0, w_str)
        h.delete(0, "end")
        h.insert(0, h_str)

    def _sync_preset_from_entries(self) -> None:
        """Se o user editou width/height manualmente pra valores que não
        batem com nenhum preset, volta o menu pra 'Custom'."""
        try:
            w = int(self.fields["screen.width_px"]["widget"].get())
            h = int(self.fields["screen.height_px"]["widget"].get())
        except (ValueError, KeyError):
            return
        label = self._detect_preset(w, h)
        if self._preset_menu.get() != label:
            self._preset_menu.set(label)

    def _update_calibration_metadata(self) -> None:
        """Atualiza só screen_width_px/screen_height_px no calibration.json
        existente. Útil quando muda a resolução-alvo sem recalibrar."""
        if not self.collect_into_cfg():
            return
        if not os.path.isfile(CALIB_PATH):
            self.log("[ui] sem calibration.json — calibre primeiro.\n")
            return
        try:
            import json as _json
            with open(CALIB_PATH, encoding="utf-8") as f:
                data = _json.load(f)
            data["screen_width_px"] = int(self.cfg.screen.width_px)
            data["screen_height_px"] = int(self.cfg.screen.height_px)
            with open(CALIB_PATH, "w", encoding="utf-8") as f:
                _json.dump(data, f, indent=2)
            self.log(
                f"[ui] calibration.json: metadata -> "
                f"{self.cfg.screen.width_px}x{self.cfg.screen.height_px}\n")
        except Exception as exc:
            self.log(f"[ui] erro ao atualizar calibration.json: {exc}\n")

    def _build_udp(self, p) -> None:
        c = self.cfg.udp
        e = self._entry(p, c.host)
        self._register_field("udp.host", e, "str")
        self._add_row(p, 0, "host", e, "127.0.0.1 = local; IP pra rede")
        e = self._entry(p, c.port)
        self._register_field("udp.port", e, "int")
        self._add_row(p, 1, "port", e, "1024-65535")
        e = self._entry(p, c.publish_rate_hz)
        self._register_field("udp.publish_rate_hz", e, "float")
        self._add_row(p, 2, "publish_rate_hz", e, "0 = sem throttle")
        e = self._entry(p, c.max_points)
        self._register_field("udp.max_points", e, "int")
        self._add_row(p, 3, "max_points", e, "cap defensivo")

    def _parse_value(self, key: str, raw: str, kind: str):
        if kind == "str":
            return raw
        if kind == "str?":
            return raw.strip() or None
        if kind == "bool":
            return bool(raw)
        if kind in ("int", "int?"):
            if not raw.strip() and kind == "int?":
                return None
            return int(raw)
        if kind in ("float", "float?"):
            if not raw.strip() and kind == "float?":
                return None
            return float(raw)
        raise ValueError(f"kind desconhecido: {kind}")

    def _read_widget(self, w, kind: str):
        if kind == "bool":
            return bool(w.get())
        return w.get()

    def collect_into_cfg(self) -> bool:
        """Lê todos os widgets e atualiza self.cfg. Retorna True se válido."""
        for key, slot in self.fields.items():
            section, name = key.split(".", 1)
            kind = slot["kind"]
            try:
                if kind == "bool":
                    value = bool(slot["widget"].get())
                else:
                    value = self._parse_value(key, slot["widget"].get(), kind)
            except (ValueError, TypeError) as exc:
                self.log(f"[ui] campo inválido {key!r}: {exc}\n")
                return False
            target = getattr(self.cfg, section)
            setattr(target, name, value)
        return True

    def reload_from_disk(self) -> None:
        """Atualiza widgets a partir do config.yaml no disco."""
        cfg = cfg_mod.load()
        for key, slot in self.fields.items():
            section, name = key.split(".", 1)
            value = getattr(getattr(cfg, section), name)
            w, kind = slot["widget"], slot["kind"]
            if kind == "bool":
                if value:
                    w.select()
                else:
                    w.deselect()
                continue
            w.delete(0, "end")
            if value is None:
                continue
            w.insert(0, str(value))
        self.cfg = cfg

    def _on_save_clicked(self) -> None:
        if not self.collect_into_cfg():
            return
        cfg_mod.save(self.cfg)
        self.log("[ui] config.yaml salvo.\n")
        self.on_save()

    def _on_apply_clicked(self) -> None:
        if not self.collect_into_cfg():
            return
        cfg_mod.save(self.cfg)
        self.log("[ui] config.yaml salvo. Aplicando…\n")
        self.on_apply()

    def _on_reload_clicked(self) -> None:
        self.reload_from_disk()
        self.log("[ui] settings recarregados do disco.\n")


class ControlPanel(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("LidarMapper — Control Panel")
        self.geometry("1040x860")
        self.minsize(880, 600)
        self.cfg = cfg_mod.load()
        self.proc = None
        self.calib_proc = None
        self.tool_procs = {}
        self._tool_exit_logged = set()
        self.tool_btns = {}
        self.reader_thread = None
        self.calib_reader_thread = None
        self.log_queue = queue.Queue()
        self._exit_logged = False
        self._calib_exit_logged = False
        self.run_state = empty_state()
        self._build_ui()
        self._refresh_status()
        self._poll_calib_file()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._drain_log)
        self.after(500, self._tick_state)

    def _build_ui(self) -> None:
        top = ctk.CTkFrame(self, corner_radius=0)
        top.pack(fill="x")
        self.btn_start = ctk.CTkButton(
            top, text="▶ Start UDP", width=140, height=36,
            command=self.start_pipeline)
        self.btn_start.pack(side="left", padx=(12, 6), pady=10)
        self.btn_stop = ctk.CTkButton(
            top, text="■ Stop", width=100, height=36,
            fg_color="#a23b3b", hover_color="#c14d4d",
            command=self.stop_pipeline)
        self.btn_stop.pack(side="left", padx=6, pady=10)
        self.btn_calibrate = ctk.CTkButton(
            top, text="🎯 Calibrate", width=140, height=36,
            fg_color="#3b6fa2", hover_color="#4d83c1",
            command=self.start_calibration)
        self.btn_calibrate.pack(side="left", padx=6, pady=10)
        self.btn_clear = ctk.CTkButton(
            top, text="Clear log", width=100, height=36,
            fg_color="#3a3a3a", hover_color="#555",
            command=self._clear_log)
        self.btn_clear.pack(side="left", padx=6, pady=10)
        self.status_label = ctk.CTkLabel(
            top, text="status: idle", anchor="e",
            font=ctk.CTkFont(family="Consolas", size=12))
        self.status_label.pack(side="right", padx=12)

        tools_bar = ctk.CTkFrame(self, corner_radius=0)
        tools_bar.pack(fill="x")
        ctk.CTkLabel(
            tools_bar, text="Viz tools:",
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color="#aaa",
        ).pack(side="left", padx=(14, 8), pady=8)
        for key, label, _mode, _needs in TOOLS:
            btn = ctk.CTkButton(
                tools_bar, text=label, width=160, height=30,
                fg_color="#3a3a3a", hover_color="#555",
                command=lambda k=key: self.start_tool(k))
            btn.pack(side="left", padx=4, pady=8)
            self.tool_btns[key] = btn

        status_outer = ctk.CTkFrame(self)
        status_outer.pack(fill="x", padx=10, pady=(8, 4))
        header = ctk.CTkLabel(
            status_outer, text="Status",
            font=ctk.CTkFont(size=13, weight="bold"), anchor="w")
        header.pack(fill="x", padx=10, pady=(8, 4))
        rows = ctk.CTkFrame(status_outer, fg_color="transparent")
        rows.pack(fill="x", padx=4, pady=(0, 8))
        self.row_lidar = StatusRow(rows, "LIDAR")
        self.row_baseline = StatusRow(rows, "Baseline")
        self.row_tracks = StatusRow(rows, "Tracks")
        self.row_udp = StatusRow(rows, "UDP")
        self.row_calib = StatusRow(rows, "Calib")
        for r in (self.row_lidar, self.row_baseline, self.row_tracks,
                  self.row_udp, self.row_calib):
            r.pack(fill="x", padx=6, pady=1)

        self.settings = SettingsPanel(
            self, self.cfg,
            on_save=lambda: None,
            on_apply=self._apply_settings_restart,
            log_callback=self._log_ui)
        self.settings.pack(fill="x", padx=10, pady=(4, 4))

        log_frame = ctk.CTkFrame(self)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        log_header = ctk.CTkFrame(log_frame, fg_color="transparent")
        log_header.pack(fill="x", padx=10, pady=(6, 0))
        ctk.CTkLabel(
            log_header, text="Log",
            font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
        ).pack(side="left")
        self.log_text = ctk.CTkTextbox(
            log_frame, font=ctk.CTkFont(family="Consolas", size=12),
            wrap="none")
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)
        self.log_text.configure(state="disabled")
        self._update_buttons()

    def start_pipeline(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        prev_calib = (self.run_state.get("calib_loaded"),
                      self.run_state.get("calib_name"))
        self.run_state = empty_state()
        if prev_calib[0]:
            self.run_state["calib_loaded"] = True
            self.run_state["calib_name"] = prev_calib[1]
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            self.proc = subprocess.Popen(
                _spawn_cmd("main"),
                cwd=HERE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=flags,
            )
        except OSError as exc:
            self._log_ui(f"[ui] falha ao iniciar main.py: {exc}\n")
            return
        self.run_state["running"] = True
        self.run_state["lidar_state"] = "connecting"
        self._exit_logged = False
        self.reader_thread = threading.Thread(
            target=self._read_stdout, args=(self.proc,), daemon=True)
        self.reader_thread.start()
        self._log_ui(f"[ui] main.py iniciado (pid={self.proc.pid})\n")
        self._update_buttons()

    def stop_pipeline(self) -> None:
        proc = self.proc
        if not proc or proc.poll() is not None:
            return
        self._log_ui("[ui] enviando shutdown...\n")
        try:
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.send_signal(signal.SIGINT)
        except Exception as exc:
            self._log_ui(f"[ui] erro ao enviar sinal: {exc}\n")
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._log_ui("[ui] timeout no graceful, terminando...\n")
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._log_ui("[ui] forçando kill...\n")
                proc.kill()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        self._update_buttons()

    def _read_stdout(self, proc: subprocess.Popen) -> None:
        """Lê stdout de um subprocess linha a linha e empurra no log_queue.
        Usada tanto pro main.py quanto pro calibrate.py."""
        if not proc or not proc.stdout:
            return
        for line in iter(proc.stdout.readline, ""):
            self.log_queue.put(line)
        try:
            proc.stdout.close()
        except Exception:
            pass

    def _busy_with_lidar(self) -> str | None:
        """Retorna o nome do processo que está ocupando o LIDAR, ou None."""
        if self.proc and self.proc.poll() is None:
            return "main.py"
        if self.calib_proc and self.calib_proc.poll() is None:
            return "calibrate.py"
        for k, p in self.tool_procs.items():
            if p.poll() is None:
                return f"{k}.py"
        return None

    def start_tool(self, key: str) -> None:
        spec = next((t for t in TOOLS if t[0] == key), None)
        if not spec:
            self._log_ui(f"[ui] tool desconhecida: {key}\n")
            return
        _key, label, mode, needs_calib = spec
        if needs_calib and not self.run_state["calib_loaded"]:
            self._log_ui(
                f"[ui] {label}: precisa de calibration.json. "
                "Rode Calibrate primeiro.\n")
            return
        owner = self._busy_with_lidar()
        if owner:
            self._log_ui(
                f"[ui] LIDAR ocupado por {owner}. "
                "Pare antes de abrir outra tool.\n")
            return
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            proc = subprocess.Popen(
                _spawn_cmd(mode),
                cwd=HERE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=flags,
            )
        except OSError as exc:
            self._log_ui(f"[ui] falha ao iniciar {mode}: {exc}\n")
            return
        self.tool_procs[key] = proc
        self._tool_exit_logged.discard(key)
        threading.Thread(
            target=self._read_stdout, args=(proc,), daemon=True).start()
        self._log_ui(
            f"[ui] {mode} iniciado (pid={proc.pid}). "
            "Feche a janela ou ESC pra encerrar.\n")
        self._update_buttons()

    def start_calibration(self) -> None:
        """Spawna calibrate.py. Bloqueia se main.py rodando (LIDAR ocupado)."""
        if self.calib_proc and self.calib_proc.poll() is None:
            self._log_ui("[ui] calibração já está rodando.\n")
            return
        if self.proc and self.proc.poll() is None:
            self._log_ui(
                "[ui] pare o pipeline (■ Stop) antes de calibrar — "
                "o LIDAR está em uso.\n")
            return
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            self.calib_proc = subprocess.Popen(
                _spawn_cmd("calibrate"),
                cwd=HERE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=flags,
            )
        except OSError as exc:
            self._log_ui(f"[ui] falha ao iniciar calibrate.py: {exc}\n")
            return
        self._calib_exit_logged = False
        self.calib_reader_thread = threading.Thread(
            target=self._read_stdout, args=(self.calib_proc,), daemon=True)
        self.calib_reader_thread.start()
        self._log_ui(
            f"[ui] calibrate.py iniciado (pid={self.calib_proc.pid}). "
            "Janela fullscreen no display configurado. ESC pra cancelar.\n")
        self._update_buttons()

    def _drain_log(self) -> None:
        appended = False
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            parse_log_line(line, self.run_state)
            if not appended:
                self.log_text.configure(state="normal")
                appended = True
            self.log_text.insert("end", line)
        if appended:
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(100, self._drain_log)

    def _tick_state(self) -> None:
        proc = self.proc
        if proc and proc.poll() is not None and not self._exit_logged:
            rc = proc.returncode
            self._log_ui(f"[ui] main.py encerrado (rc={rc})\n")
            self._exit_logged = True
            self.run_state["running"] = False
            self.run_state["meas_s"] = 0.0
            self.run_state["scans_s"] = 0.0
            self.run_state["tracks"] = 0
            self.run_state["pub_s"] = 0.0
            self._update_buttons()
        cproc = self.calib_proc
        if cproc and cproc.poll() is not None and not self._calib_exit_logged:
            rc = cproc.returncode
            self._log_ui(f"[ui] calibrate.py encerrado (rc={rc})\n")
            self._calib_exit_logged = True
            self._refresh_calib_file()
            self._update_buttons()
        for key, proc in list(self.tool_procs.items()):
            if proc.poll() is None:
                continue
            if key not in self._tool_exit_logged:
                self._log_ui(
                    f"[ui] {key}.py encerrado (rc={proc.returncode})\n")
                self._tool_exit_logged.add(key)
                self._update_buttons()
        self._refresh_status()
        self.after(500, self._tick_state)

    def _refresh_status(self) -> None:
        st = self.run_state
        running = st["running"]
        if running and self.proc:
            self.status_label.configure(
                text=f"status: running (pid={self.proc.pid})",
                text_color=C_OK)
        elif st["ended"]:
            self.status_label.configure(
                text="status: stopped", text_color=C_IDLE)
        else:
            self.status_label.configure(
                text="status: idle", text_color=C_IDLE)

        if st["lidar_state"] == "ok":
            self.row_lidar.set(
                f"{st['lidar_port']}   meas/s={st['meas_s']:.0f}"
                f"  scans/s={st['scans_s']:.1f}"
                f"  desync={st['desync']} recon={st['recon']}",
                C_OK if running else C_IDLE)
        elif st["lidar_state"] == "fail":
            self.row_lidar.set(f"fail: {st['lidar_err']}", C_FAIL)
        elif st["lidar_state"] == "connecting":
            self.row_lidar.set("conectando…", C_WAIT)
        else:
            self.row_lidar.set("idle (Start pra conectar)", C_IDLE)

        if st["baseline_state"] == "ready":
            self.row_baseline.set(
                f"pronto ({st['bins']}/{st['bins_total']} bins)",
                C_OK if running else C_IDLE)
        elif st["baseline_state"] == "learning":
            self.row_baseline.set(
                f"capturando… {st['bins']}/{st['bins_total']} bins"
                "  (mantenha a área livre)",
                C_WAIT)
        else:
            self.row_baseline.set("—", C_IDLE)

        if running and st["baseline_state"] == "ready":
            if st["tracks"] > 0:
                self.row_tracks.set(
                    f"{st['tracks']} active   fg={st['fg']}", C_OK)
            else:
                self.row_tracks.set(
                    f"nenhum cursor   fg={st['fg']}", C_INFO)
        else:
            self.row_tracks.set("—", C_IDLE)

        if st["udp_endpoint"] != "—":
            self.row_udp.set(
                f"-> {st['udp_endpoint']}   target={st['udp_rate']:g} Hz"
                f"   atual={st['pub_s']:.1f} Hz",
                C_OK if running else C_IDLE)
        else:
            self.row_udp.set("—", C_IDLE)

        if st["calib_loaded"]:
            extra = (f" · {st['calib_w']}x{st['calib_h']}"
                     if st["calib_w"] else "")
            mtime = (f"  ({st['calib_mtime']})"
                     if st["calib_mtime"] else "")
            self.row_calib.set(
                f"loaded · {st['calib_name']}{extra}{mtime}", C_OK)
        else:
            self.row_calib.set(
                "ausente — rode 'python calibrate.py'", C_FAIL)

    def _refresh_calib_file(self) -> None:
        """Atualiza estado a partir do arquivo calibration.json no disco."""
        if os.path.isfile(CALIB_PATH):
            mtime = time.strftime(
                "%Y-%m-%d %H:%M",
                time.localtime(os.path.getmtime(CALIB_PATH)))
            self.run_state["calib_mtime"] = mtime
            if not self.run_state["calib_loaded"]:
                self.run_state["calib_loaded"] = True
                self.run_state["calib_name"] = os.path.basename(CALIB_PATH)
            return
        self.run_state["calib_loaded"] = False
        self.run_state["calib_mtime"] = ""

    def _poll_calib_file(self) -> None:
        """Polling periódico do arquivo (fallback caso outra ferramenta o edite)."""
        self._refresh_calib_file()
        self.after(2000, self._poll_calib_file)

    def _update_buttons(self) -> None:
        running = bool(self.proc and self.proc.poll() is None)
        calibrating = bool(self.calib_proc and self.calib_proc.poll() is None)
        tool_running = any(
            p.poll() is None for p in self.tool_procs.values())
        busy = running or calibrating or tool_running
        calib_ok = self.run_state["calib_loaded"]
        self.btn_start.configure(state="disabled" if busy else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        if calibrating:
            self.btn_calibrate.configure(
                text="● calibrando…", state="disabled")
        else:
            self.btn_calibrate.configure(
                text="🎯 Calibrate",
                state="disabled" if running or tool_running else "normal")
        for key, label, _mode, needs_calib in TOOLS:
            btn = self.tool_btns.get(key)
            if not btn:
                continue
            running_self = (key in self.tool_procs
                            and self.tool_procs[key].poll() is None)
            if running_self:
                btn.configure(text=f"● {key} running…", state="disabled")
            elif busy:
                btn.configure(text=label, state="disabled")
            elif needs_calib and not calib_ok:
                btn.configure(text=f"{label} (no calib)", state="disabled")
            else:
                btn.configure(text=label, state="normal")

    def _log_ui(self, msg: str) -> None:
        self.log_queue.put(msg)

    def _apply_settings_restart(self) -> None:
        """Save + restart do main.py (se estava rodando). Caso contrário só
        avisa que o próximo Start vai usar os valores novos."""
        was_running = bool(self.proc and self.proc.poll() is None)
        if was_running:
            self.stop_pipeline()
            self.after(300, self.start_pipeline)
            self._log_ui("[ui] reiniciando pipeline com config novo…\n")
            return
        self._log_ui(
            "[ui] config salvo. Aperte Start pra usar os novos valores.\n")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def on_close(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.stop_pipeline()
        for proc in [self.calib_proc, *self.tool_procs.values()]:
            if not proc:
                continue
            if proc.poll() is not None:
                continue
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
            except Exception:
                pass
        self.destroy()


def main() -> int:
    app = ControlPanel()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
