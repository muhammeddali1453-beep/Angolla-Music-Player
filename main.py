#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Angolla Music Player - Tek Dosya, Güncel Sürüm (Sade Geçiş + Çalışan Görselleştirme)
"""
import math
import sys
import os
import pickle
import random
import time
import ctypes
import sqlite3
from typing import Optional, Dict, Any, List
import vlc
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout,
    QWidget, QLabel, QHBoxLayout, QSlider, QListWidget, QSplitter,
    QAction, QStatusBar, QTreeView, QStackedWidget, QListWidgetItem,
    QMenu, QFileDialog, QMessageBox, QShortcut, QFileSystemModel,
    QDialog, QCheckBox, QGridLayout, QComboBox, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
    , QColorDialog
)
from PyQt5.QtMultimedia import (
    QMediaPlayer, QMediaContent, QMediaPlaylist, QAudioProbe
)
from PyQt5.QtCore import (
    QUrl, Qt, QTime, QDir, QModelIndex, QTimer, QByteArray,
    QSettings, QPointF, pyqtSignal
)
from PyQt5.QtGui import (
    QPainter, QBrush, QColor, QPixmap, QKeySequence, QPen,
    QFont, QIcon
)

# Ek araçlar
import webbrowser
import urllib.parse

# İsteğe bağlı ek kütüphaneler
try:
    import numpy as np
except Exception:
    np = None
    print("Uyarı: NumPy yüklenemedi. Görselleştirme sınırlı çalışacak.")

try:
    from mutagen import File as MutagenFile
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
except Exception:
    MutagenFile = None
    ID3 = None
    MP4 = None
    print("Uyarı: Mutagen yüklenemedi. Etiket/kapak okuma sınırlı olacak.")

# Sabitler
PLAYLIST_FILE = "angolla_playlist.pkl"
DB_FILE = "angolla_library.db"
SETTINGS_KEY = "AngollaPlayer/Settings"


# ---------------------------------------------------------------------------
# KÜTÜPHANE YÖNETİCİSİ
# ---------------------------------------------------------------------------

class LibraryManager:
    """SQLite üzerinde parça bilgilerini tutan basit kütüphane yöneticisi."""

    def __init__(self, db_file=DB_FILE):
        self.db_file = db_file
        self.conn = None
        self._connect_db()

    def _connect_db(self):
        self.conn = sqlite3.connect(self.db_file)
        self.cursor = self.conn.cursor()
        self._setup_db()

    def _setup_db(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE,
                title TEXT,
                artist TEXT,
                album TEXT,
                duration INTEGER,
                last_scanned REAL
            )
        """)
        self.conn.commit()

    def add_track(self, path: str, tags: Dict[str, Any]):
        if not self.conn:
            self._connect_db()
        try:
            self.cursor.execute("""
                INSERT OR REPLACE INTO tracks
                (path, title, artist, album, duration, last_scanned)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                path,
                tags.get("title", os.path.basename(path)),
                tags.get("artist", "Bilinmeyen Sanatçı"),
                tags.get("album", "Bilinmeyen Albüm"),
                tags.get("duration", 0),
                time.time()
            ))
            self.conn.commit()
        except Exception as e:
            print(f"Veritabanı hatası (add_track): {e}")

    def get_all_tracks(self):
        if not self.conn:
            self._connect_db()
        self.cursor.execute(
            "SELECT path, title, artist, album, duration "
            "FROM tracks ORDER BY artist, album, title"
        )
        return self.cursor.fetchall()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None


# ---------------------------------------------------------------------------
# KÜTÜPHANE TABLOSU
# ---------------------------------------------------------------------------

class LibraryTableWidget(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHorizontalHeaderLabels(["Başlık", "Sanatçı", "Albüm", "Süre"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSortingEnabled(True)

    def load_tracks(self, tracks: List):
        self.setRowCount(len(tracks))
        for row, track in enumerate(tracks):
            path, title, artist, album, duration = track
            self.setItem(row, 0, QTableWidgetItem(title))
            self.setItem(row, 1, QTableWidgetItem(artist))
            self.setItem(row, 2, QTableWidgetItem(album))
            time_str = QTime(0, 0).addMSecs(duration).toString("mm:ss")
            duration_item = QTableWidgetItem(time_str)
            duration_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.setItem(row, 3, duration_item)
            for col in range(self.columnCount()):
                self.item(row, col).setData(Qt.UserRole, path)

    def get_selected_paths(self):
        rows = set(idx.row() for idx in self.selectionModel().selectedRows())
        paths = []
        for r in rows:
            item = self.item(r, 0)
            if item:
                p = item.data(Qt.UserRole)
                if p:
                    paths.append(p)
        return paths


# ---------------------------------------------------------------------------
# EKOLAYZIR
# ---------------------------------------------------------------------------

class EqualizerWidget(QWidget):
    eq_changed_signal = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.frequencies = [
            "31 Hz", "62 Hz", "125 Hz", "250 Hz", "500 Hz",
            "1 KHz", "2 KHz", "4 KHz", "8 KHz", "16 KHz"
        ]
        self.sliders = []
        self.labels = []
        self.initial_value = 50
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        for i, freq in enumerate(self.frequencies):
            v_layout = QVBoxLayout()
            val_label = QLabel("0 dB")
            val_label.setAlignment(Qt.AlignCenter)
            self.labels.append(val_label)

            slider = QSlider(Qt.Vertical)
            slider.setRange(0, 100)
            slider.setValue(self.initial_value)
            slider.setMinimumHeight(120)
            slider.setObjectName(f"eq_slider_{i}")
            # Doğrudan slider'ın sender() ile bağla (lambda'da sorun yaratmamak için)
            slider.valueChanged.connect(self._update_label)
            slider.valueChanged.connect(
                lambda: self.eq_changed_signal.emit(self.get_gains())
            )
            self.sliders.append(slider)

            freq_label = QLabel(freq)
            freq_label.setAlignment(Qt.AlignCenter)

            v_layout.addWidget(val_label)
            v_layout.addWidget(slider)
            v_layout.addWidget(freq_label)
            layout.addLayout(v_layout)

        self.setLayout(layout)

    def _update_label(self, value):
        # Uyumluluk: bazen lambda ile label parametresi de gönderiliyor
        db = (value - 50) / 5
        sender = self.sender()
        try:
            # Eğer çağıran widget doğrudan bağlı ise index ile bul
            label = self.labels[self.sliders.index(sender)]
        except Exception:
            # Fallback: eğer lambda ile label iletildiyse, kullan
            try:
                # ikinci argüman olarak gönderilen label varsa onu kullan
                # (PyQt lambda bağlantılarında bu değer doğrudan burada bulunmaz,
                #  ama bu yapı koruyucu kod sağlar.)
                label = None
            except Exception:
                label = None
        if label is not None:
            label.setText(f"{db:+.1f} dB")

    def get_gains(self):
        gains = []
        for s in self.sliders:
            gain = (s.value() / 50.0)
            gains.append(gain)
        return gains

    def set_gains(self, gains: List[float]):
        if len(gains) != len(self.sliders):
            return
        for s, gain in zip(self.sliders, gains):
            val = int(gain * 50)
            s.setValue(val)


# ---------------------------------------------------------------------------
# PARÇA BİLGİ PANELİ
# ---------------------------------------------------------------------------

class InfoDisplayWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)
        self.setStyleSheet(
            "background-color: #1E1E1E; border: 1px solid #444444; border-radius: 6px;"
        )
        self._album_art_visible = True
        self._external_album_label = None
        self._init_ui()

    def _init_ui(self):
        # Düzen: başlık/artist/album üstte, albüm kapağı sağ-alt köşede
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        self.titleLabel = QLabel("Başlık: -")
        self.artistLabel = QLabel("Sanatçı: -")
        self.albumLabel = QLabel("Albüm: -")

        self.titleLabel.setStyleSheet("font-weight: bold; color: #40C4FF;")
        self.artistLabel.setStyleSheet("color: #CCCCCC;")
        self.albumLabel.setStyleSheet("color: #AAAAAA;")

        main_layout.addWidget(self.titleLabel)
        main_layout.addWidget(self.artistLabel)
        main_layout.addWidget(self.albumLabel)
        main_layout.addStretch(1)

        # Albüm kapağı artık dışa taşındı; ana uygulama tarafından yerleştirilecek.

        self.setLayout(main_layout)

    def set_album_art_visibility(self, visible: bool):
        self._album_art_visible = visible
        # Eğer dışsal bir album label atandıysa, onun görünürlüğünü ayarla
        if self._external_album_label is not None:
            self._external_album_label.setVisible(visible)
            if visible:
                self._external_album_label.setFixedSize(200, 200)
            else:
                self._external_album_label.setFixedSize(0, 0)
        self.update()

    def update_info(self, title: str, artist: str, album: str,
                    path: Optional[str] = None):
        self.titleLabel.setText(f"Başlık: {title}")
        self.artistLabel.setText(f"Sanatçı: {artist}")
        self.albumLabel.setText(f"Albüm: {album}")

        if not self._album_art_visible:
            self.albumArtLabel.setText("Albüm Yok (Gizli)")
            self.albumArtLabel.setPixmap(QPixmap())
            return

        cover_data = None

        if path and MutagenFile is not None and os.path.exists(path):
            try:
                audio = MutagenFile(path)
                if audio and audio.tags:
                    if ID3 and isinstance(audio.tags, ID3):
                        for key in audio.tags.keys():
                            if key.startswith("APIC"):
                                apic = audio.tags[key]
                                if hasattr(apic, "data") and isinstance(apic.data, bytes):
                                    cover_data = apic.data
                                    break
                    elif MP4 and isinstance(audio, MP4):
                        covr = audio.tags.get("covr")
                        if covr and isinstance(covr, list) and len(covr) > 0:
                            data = covr[0]
                            if isinstance(data, bytes):
                                cover_data = data
            except Exception:
                pass

        if cover_data:
            pix = QPixmap()
            if pix.loadFromData(QByteArray(cover_data)):
                if self._external_album_label is not None:
                    self._external_album_label.setPixmap(
                        pix.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    )
                return

        if path:
            folder = os.path.dirname(path)
            for name in ("cover.jpg", "folder.jpg", "album.png"):
                p = os.path.join(folder, name)
                if os.path.exists(p):
                    pix = QPixmap(p)
                    if self._external_album_label is not None:
                        self._external_album_label.setPixmap(
                            pix.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        )
                        return

        if self._external_album_label is not None:
            self._external_album_label.setText("Albüm Yok")
            self._external_album_label.setPixmap(QPixmap())

    def clear_info(self):
        self.titleLabel.setText("Başlık: -")
        self.artistLabel.setText("Sanatçı: -")
        self.albumLabel.setText("Albüm: -")
        if self._album_art_visible and self._external_album_label is not None:
            self._external_album_label.setText("Albüm Yok")
            self._external_album_label.setPixmap(QPixmap())

    def set_external_album_label(self, label: QLabel):
        """Assign an external QLabel (created by AngollaPlayer) to show album art."""
        self._external_album_label = label
        # Apply current visibility (respect the size already set by AngollaPlayer)
        if label is not None:
            label.setVisible(self._album_art_visible)


# ---------------------------------------------------------------------------
# SEEK SLIDER
# ---------------------------------------------------------------------------

class SeekSlider(QSlider):
    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        step = 5000
        if delta > 0:
            new_position = self.value() + step
        else:
            new_position = self.value() - step
        new_position = max(0, min(new_position, self.maximum()))
        self.setValue(new_position)
        self.sliderReleased.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.pos().x()
            slider_width = self.width()
            if slider_width > 0:
                value = int(
                    (self.maximum() - self.minimum()) *
                    (pos / slider_width) + self.minimum()
                )
                self.setValue(value)
                self.sliderReleased.emit()
                event.accept()
                return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        # Varsayılan çizimi bırakarak sadece handle çizimini kullanıyoruz;
        # ancak ilerleme için alt arka plan rengini stil ile ayarlıyoruz (yapılandırma ana uygulamada yapılır).
        super().paintEvent(event)


# ---------------------------------------------------------------------------
# ÇALMA LİSTESİ WIDGET
# ---------------------------------------------------------------------------

class PlaylistListWidget(QListWidget):
    SUPPORTED_EXT = (
        ".mp3", ".wav", ".flac", ".ogg",
        ".m4a", ".aac",
        ".mp4", ".mkv", ".avi", ".mov", ".webm", ".mpeg"
    )

    def __init__(self, parent=None, player=None):
        super().__init__(parent)
        self.player = player
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QListWidget.InternalMove)
        self.setSelectionMode(QListWidget.ExtendedSelection)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        SUPPORTED_EXT = (
            ".mp3", ".wav", ".flac", ".ogg",
            ".m4a", ".aac",
            ".mp4", ".mkv", ".avi", ".mov", ".webm", ".mpeg"
        )

        paths = []

        for url in event.mimeData().urls():
            p = url.toLocalFile()

            if os.path.isdir(p):
                for root, _, files in os.walk(p):
                    for f in files:
                        if f.lower().endswith(SUPPORTED_EXT):
                            paths.append(os.path.join(root, f))
            else:
                if p.lower().endswith(SUPPORTED_EXT):
                    paths.append(p)

        if paths:
            for path in paths:
                title, artist, _, _ = self._get_tags_from_file_with_duration(path)
                item = QListWidgetItem(f"{artist} - {title}")
                item.setData(Qt.UserRole, path)
                self.playlistWidget.addItem(item)
                self.playlist.addMedia(QMediaContent(QUrl.fromLocalFile(path)))

        event.acceptProposedAction()
        self.save_playlist()


# ---------------------------------------------------------------------------
# GÖRSELLEŞTİRME WIDGET
# ---------------------------------------------------------------------------

class AnimatedVisualizationWidget(QWidget):
    def __init__(self, parent=None, initial_mode="Çizgiler", show_full_visual=True):
        super().__init__(parent)
        self.setMouseTracking(True)

        self.show_full_visual = show_full_visual
        self.vis_mode = initial_mode

        self.line_count = 60
        self.sound_intensity = 0.0

        # ESKİ SMOOTHING DEĞİŞKENLERİ
        self.band_data = [0.0] * 10
        self.band_smoothing = [0.0] * 10

        # 🔥 KRİTİK ONARIM 1: self.fft_bars değişkenini doğru sınıfta başlatıyoruz!
        self.fft_bars = []
        # Bar cap (tepe) değerleri
        self.bar_caps = []

        self.primary_color = QColor("#40C4FF")
        self.background_color = QColor("#2A2A2A")

        # Renkleri cache'le - titreşim engellemek için
        self._cached_bar_color = QColor("#40C4FF")
        self._cached_bar_color.setAlpha(230)
        self._cached_cap_color = QColor(94, 226, 255, 255)
        self.bar_color_mode = "NORMAL"  # NORMAL, RGB, veya GRADYAN

        self.particles = []
        self._initialize_particles()

        self.fps = 30
        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self.update_animation)
        self.set_fps(self.fps)

        self.last_update_time = time.time()
        self.bar_phase = random.uniform(0.0, 1000.0)

    # ------------------------------------------------------------------#
    # GENEL AYARLAR
    # ------------------------------------------------------------------#

    def set_vis_mode(self, mode: str):
        self.vis_mode = mode
        if mode == "Çizgiler":
            self._initialize_particles(reset_only=True)
        self.update()

    def _initialize_particles(self, reset_only=False):
        if np is None:
            self.particles = []
            return

        if reset_only and self.particles:
            return

        self.particles = []
        for _ in range(self.line_count):
            self.particles.append({
                "pos": QPointF(random.uniform(0.1, 0.9),
                               random.uniform(0.1, 0.9)),
                "prev_pos": QPointF(random.uniform(0.1, 0.9),
                                    random.uniform(0.1, 0.9)),
                "vel": QPointF(0, 0),
            })

    def set_fps(self, fps: int):
        self.fps = fps
        if self.fps > 0:
            self.animation_timer.start(int(1000 / self.fps))
        else:
            self.animation_timer.stop()

    def set_color_theme(self, primary_hex: str, background_hex: str = "#2A2A2A"):
        self.primary_color = QColor(primary_hex)
        self.background_color = QColor(background_hex)
        # Renkleri cache'le - her çerçevede yeniden oluşturmamak için
        self._cached_bar_color = QColor(primary_hex)
        self._cached_bar_color.setAlpha(230)
        self._cached_cap_color = QColor(primary_hex)
        self._cached_cap_color.setRgb(
            min(self._cached_bar_color.red() + 30, 255),
            min(self._cached_bar_color.green() + 30, 255),
            min(self._cached_bar_color.blue() + 30, 255),
            255
        )
        self.update()

    # ------------------------------------------------------------------#
    # SES VERİSİ GÜNCELLEME (ASIL ÖNEMLİ KISIM)
    # ------------------------------------------------------------------#

    def update_sound_data(self, intensity: float, band_data: list):
        """
        FFT verisini alır, her bar için ayrı attack/release uygular.
        - band_data'yı 96 bara standardize et
        """

        if not band_data:
            band_data = [0.0] * 96

        # 96 bar'a standardize et
        NUM_DISPLAY_BARS = 96
        if len(band_data) > NUM_DISPLAY_BARS:
            band_data = band_data[:NUM_DISPLAY_BARS]
        elif len(band_data) < NUM_DISPLAY_BARS:
            band_data = band_data + [0.0] * (NUM_DISPLAY_BARS - len(band_data))

        # 0..1 aralığına sıkıştır
        clean = [max(0.0, min(1.0, float(v))) for v in band_data]
        n = len(clean)
        # İlk karede eski değer yoksa oluştur
        if not hasattr(self, "smooth_bands") or len(self.smooth_bands) != n:
            self.smooth_bands = [0.0] * n

        # Per-bar peak caps (Clementine style) - track per-bar cap heights
        if not hasattr(self, "bar_caps") or len(self.bar_caps) != n:
            self.bar_caps = [0.0] * n

        out = [0.0] * n

        # Per-band parametreler: bass / mid / treble farklı davranacak
        # ÖNEMLİ: Attack/Release değerleri daha yumuşak hareketler için azaltıldı
        for i in range(n):
            prev = self.smooth_bands[i]
            new = clean[i]

            frac = (i / (n - 1)) if n > 1 else 0.0  # 0.0 -> low, 1.0 -> high

            # Attack: Daha yumuşak ve yavaş yükseliş
            # Düşük frekanslarda biraz daha hızlı (bas vuruşu), yüksek frekanslarda yavaş
            attack = 0.40 - 0.15 * frac  # ~0.40 (low) -> ~0.25 (high)

            # Release: Yavaş, doğal düşüş - tüm bantta hafif
            release = 0.02 + 0.08 * frac  # ~0.02 (low) -> ~0.10 (high)

            # Hesaplama: yükseldiğinde attack, düştüğünde release kullan
            if new > prev:
                v = prev + (new - prev) * attack
            else:
                v = prev + (new - prev) * release

            # Kuvvetli ek yumuşatma - çubukların sert hareketi önemli ölçüde azalt
            smooth_strength = 0.92  # Artırıldı (0.85'ten): daha yumuşak sonuç
            v = prev * (1.0 - smooth_strength) + v * smooth_strength

            out[i] = v

            # Caps: Çubuk başı çizgileri - YUMUŞAK ve DÜZGÜN hareket
            cap_val = self.bar_caps[i]
            if v > cap_val:
                # Yeni pike yumuşak ve yavaş tepki - hafif zıplama efekti
                # Düşük frekanslarda orta hızda (bas vuruşu hafif zıplama)
                # Yüksek frekanslarda çok yavaş (hi-hat soft)
                cap_attack = 0.15 - 0.06 * frac  # 0.15 (low) -> 0.09 (high) = çok yumuşak!
                # Hafif overshoot: sadece %10-15 overshoot (hemen zıplama değil)
                overshoot_factor = 1.0 + (0.15 * (1.0 - frac))  # 1.15 (low) -> 1.0 (high)
                cap_val = cap_val + (v * overshoot_factor - cap_val) * cap_attack
            else:
                # Cap fall hızları - yavaş ve yumuşak inişler
                # Düşük frekanslarda çok yavaş (bas notası uzun kalır)
                # Yüksek frekanslarda orta hızda (hi-hat orta hızda düşer)
                cap_fall = 0.003 + 0.006 * frac  # 0.003 (low) -> 0.009 (high) = çok yavaş
                # Ritim şiddeti ile: sessizde çok yavaş, forte'de orta hız
                cap_fall *= (0.5 + 0.3 * self.sound_intensity)
                cap_val = max(0.0, cap_val - cap_fall)
            self.bar_caps[i] = cap_val

        self.smooth_bands = out
        self.band_smoothing = out  # çizimlerde bunu kullanıyoruz

        # Genel ses yoğunluğunu da yumuşat (daha yumuşak tepki için ALPHA çok düşürüldü)
        ALPHA = 0.05  # Daha az ani sıçramalar
        self.sound_intensity = (
            self.sound_intensity * (1.0 - ALPHA) + intensity * ALPHA
        )

        # Yeniden çiz
        self.update()


    def _apply_force(self, magnitude: float):
        """Parçacıklara rastgele yönlü kuvvet uygular (çizgi modu için)."""
        if not self.particles:
            return

        for p in self.particles:
            angle = random.uniform(0.0, 6.28318)  # ~2π
            if np is not None:
                fx = float(np.cos(angle)) * magnitude
                fy = float(np.sin(angle)) * magnitude
            else:
                fx = random.uniform(-1, 1) * magnitude
                fy = random.uniform(-1, 1) * magnitude

            p["vel"] = QPointF(
                p["vel"].x() + fx,
                p["vel"].y() + fy
            )

    # ------------------------------------------------------------------#
    # ANİMASYON
    # ------------------------------------------------------------------#

    def update_animation(self):
        current_time = time.time()
        dt = current_time - self.last_update_time
        self.last_update_time = current_time

        if dt <= 0:
            return

        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        self.bar_phase += dt * 3.0

        intensity_factor = self.sound_intensity * 0.7 + 0.3
        speed_factor = dt * 120.0

        if self.vis_mode == "Çizgiler" and self.particles and self.show_full_visual:
            for p in self.particles:
                p["prev_pos"] = QPointF(p["pos"].x(), p["pos"].y())

                p["vel"] = QPointF(
                    p["vel"].x() * 0.93,
                    p["vel"].y() * 0.93
                )

                cx, cy = 0.5, 0.5
                pull_x = (cx - p["pos"].x()) * 0.001 * (1.0 - self.sound_intensity)
                pull_y = (cy - p["pos"].y()) * 0.001 * (1.0 - self.sound_intensity)
                p["vel"] = QPointF(
                    p["vel"].x() + pull_x,
                    p["vel"].y() + pull_y
                )

                p["pos"] = QPointF(
                    p["pos"].x() + p["vel"].x() * speed_factor * intensity_factor,
                    p["pos"].y() + p["vel"].y() * speed_factor * intensity_factor,
                )

                p["pos"] = QPointF(
                    max(0.01, min(0.99, p["pos"].x())),
                    max(0.01, min(0.99, p["pos"].y())),
                )

        self.update()

    # ------------------------------------------------------------------#
    # ÇİZİM
    # ------------------------------------------------------------------#

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(self.rect(), self.background_color)

        if not self.band_smoothing:
            painter.end()
            return

        display_data = self.band_smoothing

        if not self.show_full_visual:
            self._draw_status_bars(painter, w, h, display_data)
            painter.end()
            return

        if self.vis_mode == "Çizgiler":
            self._draw_lines_mode(painter, w, h)
        elif self.vis_mode == "Daireler":
            self._draw_circles_mode(painter, w, h, display_data)
        elif self.vis_mode == "Spektrum Çubukları":
            self._draw_spectrum_mode(painter, w, h, display_data)
        elif self.vis_mode == "Enerji Halkaları":
            self._draw_energy_rings_mode(painter, w, h, display_data)
        elif self.vis_mode == "Dalga Formu":
            self._draw_waveform_mode(painter, w, h, display_data)
        elif self.vis_mode == "Pulsar":
            self._draw_pulsar_mode(painter, w, h, display_data)
        elif self.vis_mode == "Spiral":
            self._draw_spiral_mode(painter, w, h, display_data)
        elif self.vis_mode == "Volcano":
            self._draw_volcano_mode(painter, w, h, display_data)
        elif self.vis_mode == "Işın Çakışması":
            self._draw_beam_collision_mode(painter, w, h, display_data)
        elif self.vis_mode == "Çift Spektrum":
            self._draw_dual_spectrum_mode(painter, w, h, display_data)
        elif self.vis_mode == "Radyal Izgara":
            self._draw_radial_grid_mode(painter, w, h, display_data)

        painter.end()

    def _draw_lines_mode(self, painter, w, h):
        """Çizgiler modu - parçacık sistemi ile dinamik hareket."""
        if not self.particles:
            return

        r, g, b, _ = self.primary_color.getRgb()

        # Parçacıklar arasında çizgiler ve dinamik renkler
        for i, p in enumerate(self.particles):
            sx = int(p["prev_pos"].x() * w)
            sy = int(p["prev_pos"].y() * h)
            ex = int(p["pos"].x() * w)
            ey = int(p["pos"].y() * h)

            # Hız-temelli renk (spektrum)
            speed = (p["vel"].x() ** 2 + p["vel"].y() ** 2) ** 0.5
            hue = (speed * 100 + i * (360 / len(self.particles))) % 360
            color = QColor.fromHsv(int(hue), 200, 255, int(80 + 150 * self.sound_intensity))

            alpha = int(120 + 135 * self.sound_intensity)
            thickness = 1 + int(self.sound_intensity * 4)

            pen = QPen(color, thickness)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(sx, sy, ex, ey)

    def _draw_circles_mode(self, painter, w, h, data):
        """Daireler modu - merkez etrafında pulsating halkalar."""
        if not data:
            return

        cx, cy = w // 2, h // 2
        max_r = min(w, h) // 2

        bass = data[0] * 0.8 + (data[1] * 0.2 if len(data) > 1 else 0.0)
        base_r = max_r * 0.15
        cur_r = base_r + max_r * 0.7 * bass

        # Merkez halka - gradient efekti
        painter.setPen(QPen(QColor.fromHsv(int(self.bar_phase * 2) % 360, 255, 255), 3))
        painter.setBrush(QBrush(QColor(
            self.primary_color.red(),
            self.primary_color.green(),
            self.primary_color.blue(),
            60,
        )))
        painter.drawEllipse(int(cx - cur_r), int(cy - cur_r),
                            int(cur_r * 2), int(cur_r * 2))

        # Spektrum noktaları - renkli ve dinamik
        band_count = len(data)
        for i in range(band_count):
            angle = i * (360 / band_count)
            factor = 1.0 - (i / band_count) * 0.5
            dist = max_r * 0.75 * factor
            if np is not None:
                x = cx + int(dist * np.cos(np.deg2rad(angle)))
                y = cy + int(dist * np.sin(np.deg2rad(angle)))
            else:
                x, y = cx, cy

            size = 12 + data[i] * 40 * self.sound_intensity
            alpha = int(120 + data[i] * 135)

            # Spektrum renk - angle-temelli
            hue = (angle + self.bar_phase) % 360
            color = QColor.fromHsv(int(hue), 255, 255, alpha)

            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(int(x - size / 2),
                                int(y - size / 2),
                                int(size), int(size))

    def _draw_spectrum_mode(self, painter, w, h, data):
        """Spektrum Çubukları - full-screen ritim göstergesi."""
        count = len(data)
        if count == 0:
            return

        bar_w = w / count
        max_h = h * 0.95

        for i in range(count):
            v = data[i]
            bar_h = int(v * max_h * (self.sound_intensity * 1.1 + 0.3))
            x = int(i * bar_w)
            y = h - bar_h

            alpha = int(120 + v * 135)

            # Spektrum renk - position-temelli
            hue = (i / count * 360) % 360
            color = QColor.fromHsv(int(hue), 255, 255, alpha)

            painter.setBrush(QBrush(color))
            painter.setPen(Qt.NoPen)
            painter.drawRect(x + 1, y, int(bar_w) - 2, bar_h)

            # Parlama efekti - üst kısma
            if bar_h > 10:
                glow_color = QColor.fromHsv(int(hue), 100, 255, int(alpha * 0.5))
                painter.setBrush(QBrush(glow_color))
                painter.drawRect(x + 1, y, int(bar_w) - 2, 3)

    def _draw_energy_rings_mode(self, painter, w, h, data):
        """Enerji Halkaları - konsantrik halkalar spektrum göstergesi."""
        cx, cy = w // 2, h // 2
        max_r = min(w, h) // 2 * 0.85
        count = len(data)

        for i in range(count):
            v = data[i]
            base = max_r * (1 - (i / count) * 0.7)
            offset = max_r * 0.15 * v * self.sound_intensity
            cur_r = base + offset
            alpha = int(70 + v * 185)

            # Spektrum renk - frequency-temelli
            hue = (i / count * 360 + self.bar_phase) % 360
            color = QColor.fromHsv(int(hue), 255, 255, alpha)

            pen_width = 2 + v * 5
            pen = QPen(color, pen_width)
            pen.setCapStyle(Qt.RoundCap)

            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(int(cx - cur_r), int(cy - cur_r),
                                int(cur_r * 2), int(cur_r * 2))

            # İç halka - daha dim
            inner_color = QColor.fromHsv(int(hue), 255, 200, int(alpha * 0.3))
            inner_pen = QPen(inner_color, 1)
            painter.setPen(inner_pen)
            painter.drawEllipse(int(cx - cur_r * 0.8), int(cy - cur_r * 0.8),
                               int(cur_r * 1.6), int(cur_r * 1.6))

    def _draw_waveform_mode(self, painter, w, h, data):
        """Dalga formu görselleştirmesi - spektrum barlarının dalga şeklinde animasyonu."""
        if not data or len(data) == 0:
            return

        # NumPy gerekli
        if np is None:
            # NumPy yoksa basit bar göster
            self._draw_spectrum_mode(painter, w, h, data)
            return

        cx, cy = w // 2, h // 2
        count = len(data)

        # Zaman tabanlı faz
        phase = self.bar_phase * 0.05

        painter.setPen(Qt.NoPen)

        for i in range(count):
            # Normalize indeks
            t = i / max(count - 1, 1)

            # Sinüs dalgası - yükseklik ve X pozisyonu
            wave_x = w * t

            # Temel yükseklik: FFT veri
            base_height = data[i] * h * 0.4

            # Dalga animasyonu - zaman tabanlı
            wave_offset = np.sin(t * 4 * np.pi + phase) * 30

            # Y konumu (merkez etrafında)
            wave_y = cy + wave_offset

            # Yarıçap/boyut - FFT veriye bağlı
            radius = 4 + data[i] * 20
            alpha = int(100 + data[i] * 155)

            # Renk - spektrum
            hue = (t * 360) % 360
            color = QColor.fromHsv(int(hue), 255, 255, alpha)

            painter.setBrush(QBrush(color))
            painter.drawEllipse(int(wave_x - radius), int(wave_y - radius),
                               int(radius * 2), int(radius * 2))

            # Alt dalga - simetrik
            if i % 3 == 0:  # Her 3. noktada bağlantı çizgisi
                if i < count - 1:
                    next_t = (i + 1) / count
                    next_wave_x = w * next_t
                    next_wave_y = cy + (np.sin(next_t * 4 * np.pi + phase) * 30)

                    color.setAlpha(50)
                    painter.setPen(QPen(color, 2))
                    painter.drawLine(int(wave_x), int(wave_y), int(next_wave_x), int(next_wave_y))
                    painter.setPen(Qt.NoPen)

    def _draw_pulsar_mode(self, painter, w, h, data):
        """Pulsar modu - merkezden dışarı doğru pulsating ışınlar."""
        if not data:
            return

        cx, cy = w // 2, h // 2
        count = len(data)
        max_r = min(w, h) // 2 * 0.8

        for i in range(count):
            v = data[i]
            angle = i * (360 / count)

            # Merkezden dışarı doğru ışın
            length = max_r * (0.3 + v * 0.7)

            if np is not None:
                end_x = cx + int(length * np.cos(np.deg2rad(angle)))
                end_y = cy + int(length * np.sin(np.deg2rad(angle)))
            else:
                end_x, end_y = cx, cy

            # Spektrum renk
            hue = (angle + self.bar_phase) % 360
            color = QColor.fromHsv(int(hue), 255, 255, int(150 + v * 105))

            thickness = 2 + v * 8
            pen = QPen(color, thickness)
            pen.setCapStyle(Qt.RoundCap)

            painter.setPen(pen)
            painter.drawLine(int(cx), int(cy), int(end_x), int(end_y))

    def _draw_spiral_mode(self, painter, w, h, data):
        """Spiral modu - spektrum verisi spiral şeklinde."""
        if not data:
            return

        cx, cy = w // 2, h // 2
        count = len(data)
        max_r = min(w, h) // 2 * 0.85

        if np is None:
            # NumPy yoksa basit daireler çiz
            for i in range(count):
                v = data[i]
                radius = max_r * (i / count) * (0.3 + v * 0.7)
                hue = (i / count * 360) % 360
                color = QColor.fromHsv(int(hue), 255, 255, int(100 + v * 155))
                painter.setPen(QPen(color, 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(int(cx - radius), int(cy - radius), int(radius * 2), int(radius * 2))
            return

        # Spiral - her bar başında bir nokta
        for i in range(count):
            v = data[i]
            t = i / count

            # Spiral radiusu: dışa doğru gidiyor
            radius = max_r * t * (0.4 + v * 0.6)

            # Spiral angle: dönüyor
            angle = t * 720 + self.bar_phase  # 2 tam dönüş

            x = cx + int(radius * np.cos(np.deg2rad(angle)))
            y = cy + int(radius * np.sin(np.deg2rad(angle)))

            # Spektrum renk
            hue = (angle) % 360
            color = QColor.fromHsv(int(hue), 255, 255, int(120 + v * 135))

            size = 4 + v * 16
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(int(x - size / 2), int(y - size / 2), int(size), int(size))

    def _draw_volcano_mode(self, painter, w, h, data):
        """Volcano modu - merkezden patlayan parçacıklar."""
        if not data:
            return

        cx, cy = w // 2, h // 2
        count = len(data)
        max_h = h * 0.45

        if np is None:
            # NumPy yoksa basit bar çiz
            self._draw_spectrum_mode(painter, w, h, data)
            return

        for i in range(count):
            v = data[i]
            angle = i * (360 / count)

            # Yükseklik - FFT veri
            height = max_h * v * (0.5 + self.sound_intensity * 0.5)

            # Parçacıkları merkezden dışarı çıkart
            for j in range(5):  # Her bar'dan 5 parçacık
                offset_angle = angle + (j - 2) * 15  # Biraz açısallık
                particle_dist = height * (j / 5)

                x = cx + int(particle_dist * np.cos(np.deg2rad(offset_angle)))
                y = cy - int(particle_dist * np.sin(np.deg2rad(offset_angle)))  # Yukarı çıkıyor

                # Spektrum renk + yükseklik tabanlı alpha
                hue = (angle + self.bar_phase) % 360
                alpha = int(200 * (1 - j / 5))  # Yukarı gittikçe şeffaflık
                color = QColor.fromHsv(int(hue), 255, 255, alpha)

                size = 6 * (1 - j / 5)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(color))
                painter.drawEllipse(int(x - size / 2), int(y - size / 2), int(size), int(size))

    def _draw_beam_collision_mode(self, painter, w, h, data):
        """Işın Çakışması: merkezden çıkan kalın ışınların çarpıştığı efekt."""
        if not data:
            return
        cx, cy = w // 2, h // 2
        count = len(data)
        max_len = min(w, h) * 0.6

        for i in range(count):
            v = data[i]
            angle = (i / count) * 360 + (self.bar_phase * 0.5)
            length = max_len * (0.2 + v * 0.8)

            if np is not None:
                ex = cx + int(length * np.cos(np.deg2rad(angle)))
                ey = cy + int(length * np.sin(np.deg2rad(angle)))
            else:
                ex, ey = cx, cy

            hue = (angle) % 360
            color = QColor.fromHsv(int(hue), 200, 255, int(120 + v * 135))
            pen = QPen(color, 4 + v * 10)
            pen.setCapStyle(Qt.FlatCap)
            painter.setPen(pen)
            painter.drawLine(cx, cy, ex, ey)

        # Çarpışma noktalarında parlama
        for i in range(3):
            t = (self.bar_phase * 0.2 + i) % 1.0
            idx = int(t * count)
            val = data[idx]
            hue = (idx / max(1, count) * 360) % 360
            glow = QColor.fromHsv(int(hue), 255, 255, int(180 + val * 75))
            painter.setBrush(QBrush(glow))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(cx - 6, cy - 6, 12, 12)

    def _draw_dual_spectrum_mode(self, painter, w, h, data):
        """Çift Spektrum: yukarı ve aşağı simetrik spektrum çubukları."""
        count = len(data)
        if count == 0:
            return
        bar_w = w / count
        mid = h // 2
        for i in range(count):
            v = data[i]
            bar_h = int(v * (h * 0.45) * (0.6 + self.sound_intensity * 0.6))
            x = int(i * bar_w)

            hue = (i / count * 360) % 360
            color_top = QColor.fromHsv(int(hue), 220, 255, int(140 + v * 115))
            color_bot = QColor.fromHsv((int(hue) + 180) % 360, 220, 255, int(140 + v * 115))

            painter.setBrush(QBrush(color_top))
            painter.setPen(Qt.NoPen)
            painter.drawRect(x + 1, mid - bar_h, int(bar_w) - 2, bar_h)

            painter.setBrush(QBrush(color_bot))
            painter.drawRect(x + 1, mid + 1, int(bar_w) - 2, bar_h)

    def _draw_radial_grid_mode(self, painter, w, h, data):
        """Radyal Izgara: merkezden dışa doğru ızgara + radyal çubuklar."""
        if not data:
            return
        cx, cy = w // 2, h // 2
        max_r = min(w, h) // 2 * 0.9
        count = len(data)

        # Izgara halkaları
        rings = 6
        for r in range(1, rings + 1):
            rr = (r / rings) * max_r
            alpha = int(30 + (r / rings) * 100)
            color = QColor(200, 200, 200, alpha)
            painter.setPen(QPen(color, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(int(cx - rr), int(cy - rr), int(rr * 2), int(rr * 2))

        # Radyal çubuklar
        for i in range(count):
            v = data[i]
            angle = (i / count) * 360 + self.bar_phase
            length = max_r * (0.15 + v * 0.85)
            if np is not None:
                ex = cx + int(length * np.cos(np.deg2rad(angle)))
                ey = cy + int(length * np.sin(np.deg2rad(angle)))
            else:
                ex, ey = cx, cy
            hue = (i / count * 360) % 360
            color = QColor.fromHsv(int(hue), 200, 255, int(110 + v * 120))
            pen = QPen(color, 2)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(cx, cy, ex, ey)


    def _draw_status_bars(self, painter, w, h, display_data):
        """
        Clementine tarzı ritim çubukları - basit ve temiz.
        Tüm ekran alanını dolduruyor. Sağ kenarı garantile.
        """
        if not display_data or w <= 0 or h <= 0:
            return

        # Gösterilecek bar sayısı - update_sound_data ile senkronize et
        NUM_BARS = len(display_data)
        raw = list(display_data)[:NUM_BARS]

        if len(raw) == 0:
            return

        # Yumuşatma (flicker engellemek için)
        SMOOTH_FACTOR = 0.7
        if not hasattr(self, "bar_smooth_values"):
            self.bar_smooth_values = [0.0] * NUM_BARS
        else:
            # Eğer bar sayısı değiştiyse önceki array'i uzat
            if len(self.bar_smooth_values) < NUM_BARS:
                self.bar_smooth_values += [0.0] * (NUM_BARS - len(self.bar_smooth_values))

        smoothed_bars = []
        for i in range(NUM_BARS):
            raw_val = raw[i] if i < len(raw) else 0.0
            smoothed = self.bar_smooth_values[i] * SMOOTH_FACTOR + raw_val * (1.0 - SMOOTH_FACTOR)
            smoothed_bars.append(smoothed)

        self.bar_smooth_values = smoothed_bars[:]

        # Çubuk boyutu - ekranı tam olarak dolduracak şekilde hesapla,
        # ama küçük bir boşluk bırak (gap) -> çubuklar ayrı görünsün
        gap = 2
        band_area = float(w) / NUM_BARS
        bar_height_max = h * 0.9
        bottom_margin = h * 0.1

        # Renk (cache'lenmiş)
        base_color = getattr(self, '_cached_bar_color', QColor(64, 196, 255, 200))
        cap_color = getattr(self, '_cached_cap_color', QColor(94, 226, 255, 255))

        # Çubuk stil modu (solid / striped / dots)
        bar_style = getattr(self, 'bar_style_mode', 'solid')

        painter.setPen(Qt.NoPen)

        for i in range(NUM_BARS):
            # Normalize değeri al
            val = max(0.0, min(1.0, smoothed_bars[i]))

            # Bass boost - sol taraf (düşük frekanslar) daha güçlü
            # Sağ tarafta da bass'in etkisi kalabilmesi için minimum boost
            bass_mul = 1.0 + (1.0 - min(i, NUM_BARS * 0.3) / (NUM_BARS * 0.3)) * 0.2

            # Ses şiddeti ile çarp - daha yumuşak (agresif yükseklik azaltıldı)
            intensity_mul = self.sound_intensity * 1.0 + 0.3

            # Nihai yükseklik
            height = int(val * bar_height_max * intensity_mul * bass_mul)
            height = max(3, min(int(bar_height_max), height))

            # Cap yüksekliği (çubuk başı çizgisinin konumu)
            cap_val = self.bar_caps[i] if i < len(self.bar_caps) else 0.0
            cap_height = int(cap_val * bar_height_max * intensity_mul * bass_mul)
            cap_height = max(0, min(int(bar_height_max), cap_height))

            # Pozisyon - tüm çubukları dahil et, sağ kenarı kaçırma
            # En önemli: son bar da tam sağ kenarı tutmali
            x_start = (i * band_area)
            if i == NUM_BARS - 1:
                # Son bar ekranın sağına kadar uzansın
                next_x = w
            else:
                x_end = ((i + 1) * band_area)
                next_x = int(round(x_end))

            x = int(round(x_start))
            draw_w = max(1, next_x - x - gap)
            y = int(h - bottom_margin - height)
            cap_y = int(h - bottom_margin - cap_height)

            # Renk seçim - moda göre
            bar_color_mode = getattr(self, 'bar_color_mode', 'NORMAL')
            if bar_color_mode == "RGB":
                # RGB spektrum: Kırmızı → Yeşil → Mavi → Magenta
                hue = (i / NUM_BARS) * 360.0  # 0° (kırmızı) to 360° (kırmızı)
                bar_color = QColor.fromHsv(int(hue), 255, 255, 230)
                cap_color = QColor.fromHsv(int(hue), 255, 255, 255)
            elif bar_color_mode == "GRADYAN":
                # Neon Gradyan: Mavi → Cyan → Yeşil → Sarı → Kırmızı
                gradient_colors = [
                    "#0066FF",  # Mavi
                    "#00CCFF",  # Cyan
                    "#00FF00",  # Yeşil
                    "#FFFF00",  # Sarı
                    "#FF0066",  # Kırmızı
                ]
                idx = int((i / NUM_BARS) * (len(gradient_colors) - 1))
                bar_color = QColor(gradient_colors[idx])
                bar_color.setAlpha(230)
                cap_color = QColor(gradient_colors[idx])
                cap_color.setAlpha(255)
            else:
                # Normal mod - cache'lenmiş renk
                bar_color = base_color
                cap_color = cap_color

            # Stil'e göre çizim (cap_height bilgisini geç)
            self._draw_bar_style(painter, x + gap // 2, y, draw_w, height, bar_style, bar_color, cap_color, cap_y)

    def _draw_bar_style(self, painter, x, y, w, h, style, color, cap_color, cap_y=None):
        """Çubuk stiline göre çiz: solid, striped, dots, solid_with_cap. Cap_y cap konumunu gösterir."""
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))

        if style == "striped":
            # Yatay çizgiler
            line_height = 2
            gap_height = 2
            for yy in range(y, y + h, line_height + gap_height):
                painter.drawRect(x, yy, w, line_height)
        elif style == "dots":
            # Nokta deseni
            dot_size = 3
            for xx in range(x, x + w, dot_size + 2):
                for yy in range(y, y + h, dot_size + 2):
                    painter.drawEllipse(xx, yy, dot_size, dot_size)
        elif style == "solid_with_cap":
            # Düz çubuk + başında cap çizgisi
            painter.drawRect(x, y, w, h)
            # Cap çizgisi - ince (çubuk kalınlığı kadar) ve şeffaf
            cap_line_color = QColor(color)
            cap_line_color.setAlpha(255)  # Tamamen opak
            painter.setPen(QPen(cap_line_color, 1))  # İnce çizgi (1px)
            if cap_y is not None:
                painter.drawLine(x, cap_y, x + w, cap_y)
            else:
                painter.drawLine(x, y, x + w, y)
            painter.setPen(Qt.NoPen)
        else:  # solid (varsayılan)
            # Düz çubuk
            painter.drawRect(x, y, w, h)

    def _show_bar_context_menu(self, point):
        """Çubuklar için sağ tıklama menüsü - renk ve stil seçenekleri."""
        menu = QMenu(self)

        # Renk seçenekleri alt menüsü
        color_menu = QMenu("🎨 Renk Seç", self)

        # Hazır renkler
        colors = {
            "AURA Mavi": "#40C4FF",
            "Zümrüt Yeşil": "#00E676",
            "Güneş Turuncusu": "#FF9800",
            "Kırmızı Ateş": "#FF1744",
            "Mor Gece": "#7C4DFF",
            "Pembe": "#FF69B4",
            "Cyan": "#00BCD4",
            "💻 RGB Işıklar": "RGB",
            "⚡ Neon Gradyan": "GRADYAN",
        }

        for color_name, color_hex in colors.items():
            act = QAction(color_name, self)
            act.triggered.connect(
                lambda checked=False, ch=color_hex: self._set_bar_color(ch)
            )
            color_menu.addAction(act)

        # Özel renk seçici
        custom_color_act = QAction("Özel Renk...", self)
        def _choose_custom_color():
            try:
                c = QColorDialog.getColor(self._cached_bar_color, self, "Özel Renk Seç")
                if c and c.isValid():
                    self._set_bar_color(c.name())
            except Exception:
                pass
        custom_color_act.triggered.connect(_choose_custom_color)
        color_menu.addAction(custom_color_act)

        color_menu.addSeparator()
        auto_color_act = QAction("🎵 Otomatik (Albüm Renginden)", self)
        auto_color_act.triggered.connect(self._set_auto_bar_color)
        color_menu.addAction(auto_color_act)

        menu.addMenu(color_menu)

        # Stil seçenekleri alt menüsü
        style_menu = QMenu("📊 Çubuk Stili", self)

        styles = ["solid", "striped", "dots", "solid_with_cap"]
        style_names = {
            "solid": "Düz Çubuk",
            "striped": "Çizgiler",
            "dots": "Noktalar",
            "solid_with_cap": "Çubuk Başı Çizgili"
        }

        for style in styles:
            act = QAction(style_names[style], self)
            act.setCheckable(True)
            act.setChecked(style == getattr(self, 'bar_style_mode', 'solid'))
            act.triggered.connect(
                lambda checked=False, s=style: self._set_bar_style(s)
            )
            style_menu.addAction(act)

        menu.addMenu(style_menu)
        menu.exec_(self.mapToGlobal(point))

    def _set_bar_color(self, color_hex: str):
        """Çubuk rengini ayarla - hex renk veya özel modlar (RGB, GRADYAN)."""
        # Özel modlar
        if color_hex == "RGB":
            # RGB Işıklar modu - her bar farklı renk (spektrum)
            self.bar_color_mode = "RGB"
            if hasattr(self, 'parent_player'):
                self.parent_player.config_data['bar_color'] = "RGB"
                self.parent_player.save_config()
            self.update()
            return
        elif color_hex == "GRADYAN":
            # Neon Gradyan modu - mavi-cyan-yeşil-sarı-kırmızı
            self.bar_color_mode = "GRADYAN"
            if hasattr(self, 'parent_player'):
                self.parent_player.config_data['bar_color'] = "GRADYAN"
                self.parent_player.save_config()
            self.update()
            return

        # Normal hex renk
        self.bar_color_mode = "NORMAL"
        color = QColor(color_hex)
        self._cached_bar_color = QColor(color_hex)
        self._cached_bar_color.setAlpha(230)
        self._cached_cap_color = QColor(color)
        self._cached_cap_color.setRgb(
            min(color.red() + 30, 255),
            min(color.green() + 30, 255),
            min(color.blue() + 30, 255),
            255
        )

        # Config'e kaydet
        if hasattr(self, 'parent_player'):
            self.parent_player.config_data['bar_color'] = color_hex
            self.parent_player.save_config()

        self.update()

    def _set_auto_bar_color(self):
        """Albüm kapağından otomatik renk algıla."""
        if not hasattr(self, 'parent_player'):
            self._set_bar_color("#40C4FF")
            return

        current_path = self.parent_player.current_file_path
        if not current_path:
            self._set_bar_color("#40C4FF")
            return

        # Albüm kapağından baskın rengi al
        try:
            from PIL import Image
            from collections import Counter
            import os

            folder = os.path.dirname(current_path)
            cover_path = None
            for name in ("cover.jpg", "folder.jpg", "cover.png", "album.png"):
                p = os.path.join(folder, name)
                if os.path.exists(p):
                    cover_path = p
                    break

            if cover_path:
                img = Image.open(cover_path)
                # Küçült ve RGB'ye çevir
                img = img.convert('RGB')
                img.thumbnail((50, 50))  # Hızlı işlem için küçült

                # Pikselleri al ve en sık rengi bul
                pixels = list(img.getdata())
                # Renkleri grupla (performans için)
                reduced = [(r // 32 * 32, g // 32 * 32, b // 32 * 32) for r, g, b in pixels]
                color_count = Counter(reduced)

                if color_count:
                    most_common = color_count.most_common(1)[0][0]
                    r, g, b = most_common
                    # Rengi hex'e çevir
                    color_hex = f"#{r:02x}{g:02x}{b:02x}"
                    self._set_bar_color(color_hex)
                    return
        except Exception:
            pass

        # Fallback: varsayılan renk
        self._set_bar_color("#40C4FF")

    def _set_bar_style(self, style: str):
        """Çubuk stilini ayarla."""
        self.bar_style_mode = style

        # Config'e kaydet
        if hasattr(self, 'parent_player'):
            self.parent_player.config_data['bar_style'] = style
            self.parent_player.save_config()

        self.update()



    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            if self.show_full_visual:
                self._show_context_menu(event.pos())
            else:
                # Status bars (alt) için renk/stil menüsü
                self._show_bar_context_menu(event.pos())
        super().mousePressEvent(event)

    def _show_context_menu(self, point):
        menu = QMenu(self)
        app = QApplication.instance()
        from_main = next(
            (w for w in app.topLevelWidgets() if isinstance(w, AngollaPlayer)),
            None
        )

        fps_menu = QMenu("⚙️ Animasyon Hızı (FPS)", self)
        for val in [15, 30, 60]:
            a = QAction(f"{val} FPS", self)
            a.setCheckable(True)
            a.setChecked(val == self.fps)
            a.triggered.connect(lambda checked=False, f=val: self.set_fps(f))
            fps_menu.addAction(a)
        menu.addMenu(fps_menu)

        mode_menu = QMenu("🎆 Görselleştirme Modu", self)
        modes = [
            "Çizgiler", "Daireler", "Spektrum Çubukları",
            "Enerji Halkaları", "Dalga Formu", "Pulsar", "Spiral", "Volcano",
            "Işın Çakışması", "Çift Spektrum", "Radyal Izgara"
        ]
        for m in modes:
            action = QAction(m, self)
            action.setCheckable(True)
            action.setChecked(m == self.vis_mode)
            if from_main:
                action.triggered.connect(
                    lambda checked=False, mode=m:
                    from_main.set_visualization_mode(mode)
                )
            else:
                action.triggered.connect(
                    lambda checked=False, mode=m: self.set_vis_mode(mode)
                )
            mode_menu.addAction(action)
        menu.addMenu(mode_menu)

        menu.exec_(self.mapToGlobal(point))


# ---------------------------------------------------------------------------
# GÖRSELLEŞTİRME PENCERESİ
# ---------------------------------------------------------------------------

class VisualizationWindow(QMainWindow):
    def __init__(self, player):
        super().__init__()
        self.setWindowTitle("Angolla Görselleştirme")
        self.resize(800, 600)

        self.player = player
        vis_mode = self.player.config_data.get("vis_mode", "Çizgiler")

        self.visualizationWidget = AnimatedVisualizationWidget(
            self, initial_mode=vis_mode, show_full_visual=True
        )
        self.setCentralWidget(self.visualizationWidget)

        # Tema rengini uygula
        theme_colors = self.player.themes[self.player.theme]
        bg_color = theme_colors[2]  # Arka plan rengi

        # Widget'in arka planını tema rengine ayarla
        self.visualizationWidget.set_color_theme(
            theme_colors[0],  # Primary color
            bg_color           # Background color
        )

        # Pencere arka planını da tema rengine ayarla
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {bg_color};
            }}
        """)

    def closeEvent(self, event):
        self.visualizationWidget.animation_timer.stop()
        self.player._vis_window_closed()
        super().closeEvent(event)



    # ------------------------------------------------------------------
    # DOSYA OYNATMA
    # ------------------------------------------------------------------
    def play_file(self, filepath):
        media = self.vlc_instance.media_new(filepath)
        self.vlc_player.set_media(media)
        self.vlc_player.play()

        # VLC EQ yeniden uygula
        try:
            self.vlc_player.set_equalizer(self.vlc_eq)
        except Exception as e:
            print("EQ apply error:", e)

    # ==========================================================
    #  EQ DEĞİŞİNCE ÇALIŞIR  (ŞU ANDA SADECE GÖRSELLEŞTIRMEYE ETKİ EDER)
    # ==========================================================
    def _on_eq_changed(self, gains=None):
        """
        EQ değişince VLC equalizer güncellenecek
        """
        if gains is None:
            gains = self.equalizerWidget.get_gains()

        # VLC equalizer değerlerini güncelle
        for i, g in enumerate(gains):
            db = (g - 1.0) * 12  # -12 dB ile +12 dB arası
            try:
                self.vlc_eq.set_amp_at_index(db, i)
            except Exception as e:
                print("EQ set error:", e)

        # Equalizer'ı uygula
        try:
            self.vlc_player.set_equalizer(self.vlc_eq)
        except Exception as e:
            print("EQ apply error:", e)

        # --- EQ DEĞERLERİNİ KAYDET ---
        self.current_eq_gains = gains
        self.config_data["eq_gains"] = gains

    # ==========================================================
    #  SOL PANEL (Kütüphane – Listeler – Dosyalar)
    # ==========================================================
    def _create_side_panel(self):
        """Sol taraftaki Kütüphane / Listeler / Dosyalar panelini kurar ve ALT'ta albüm kapağı."""
        # --- DOSYA TARAYICI (sol Dosyalar sekmesi) ---
        self.file_model = QFileSystemModel()
        self.file_model.setRootPath(QDir.homePath())

        self.file_tree = QTreeView()
        self.file_tree.setModel(self.file_model)
        self.file_tree.hideColumn(1)
        self.file_tree.hideColumn(2)
        self.file_tree.hideColumn(3)
        self.file_tree.setHeaderHidden(True)
        self.file_tree.setRootIndex(self.file_model.index(QDir.homePath()))

        # Çoklu seçim + sadece sürükle (drop yok)
        self.file_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_tree.setDragEnabled(True)
        self.file_tree.setAcceptDrops(False)
        self.file_tree.setDragDropMode(QAbstractItemView.DragOnly)

        # --- KÜTÜPHANE GÖRÜNÜMÜ ---
        library_view = QWidget()
        library_layout = QVBoxLayout(library_view)
        library_layout.setContentsMargins(0, 0, 0, 0)
        library_layout.addWidget(self.libraryTableWidget)

        # --- ÇALMA LİSTELERİ GÖRÜNÜMÜ ---
        playlist_view = QWidget()
        playlist_layout = QVBoxLayout(playlist_view)
        playlist_layout.setContentsMargins(0, 0, 0, 0)
        playlist_layout.addWidget(QLabel("Çalma Listeleri (Yapım Aşamasında)"))

        # --- DOSYALAR GÖRÜNÜMÜ ---
        files_view = QWidget()
        files_layout = QVBoxLayout(files_view)
        files_layout.setContentsMargins(0, 0, 0, 0)

        file_nav_bar = QHBoxLayout()
        self.backButton = QPushButton("⬅️ Geri")
        self.backButton.clicked.connect(self._go_up_directory)
        file_nav_bar.addWidget(self.backButton)
        file_nav_bar.addStretch(1)

        files_layout.addLayout(file_nav_bar)
        files_layout.addWidget(self.file_tree)

        # --- STACKED WIDGET (Kütüphane / Listeler / Dosyalar) ---
        self.stackedWidget = QStackedWidget()
        self.stackedWidget.addWidget(library_view)
        self.stackedWidget.addWidget(playlist_view)
        self.stackedWidget.addWidget(files_view)

        # --- Sol dikey menü ---
        self.nav_list = QListWidget()
        self.nav_list.setMaximumWidth(80)
        self.nav_list.addItem(QListWidgetItem(QIcon.fromTheme("document-open-recent"), "📚\nKütüphane"))
        self.nav_list.addItem(QListWidgetItem(QIcon.fromTheme("view-list-details"), "📜\nListeler"))
        self.nav_list.addItem(QListWidgetItem(QIcon.fromTheme("folder"), "📁\nDosyalar"))
        self.nav_list.currentRowChanged.connect(self._handle_side_panel_click)
        self.nav_list.setCurrentRow(0)

        side_panel = QWidget()
        side_layout = QHBoxLayout(side_panel)
        side_layout.addWidget(self.nav_list)
        side_layout.addWidget(self.stackedWidget)
        side_layout.setSpacing(0)
        side_layout.setContentsMargins(0, 0, 0, 0)

        side_panel.setMaximumWidth(380)
        self.side_panel = side_panel

        # --- SOL PANELI DIKEY DÜZENLEMESİ (Stacked ALT'ta Album Kapağı) ---
        left_panel_container = QWidget()
        left_panel_layout = QVBoxLayout(left_panel_container)
        left_panel_layout.setContentsMargins(0, 0, 0, 0)
        left_panel_layout.setSpacing(0)

        # Ortada: Kütüphane/Dosyalar (expandable)
        left_panel_layout.addWidget(side_panel, stretch=1)

        # ALT'ta: Albüm kapağı (SABIT)
        album_art_widget = QWidget()
        album_art_layout = QVBoxLayout(album_art_widget)
        album_art_layout.setContentsMargins(8, 8, 8, 8)
        album_art_layout.setSpacing(0)

        album_art_layout.addWidget(self.albumArtLabel, alignment=Qt.AlignCenter)
        album_art_widget.setMaximumHeight(180)

        left_panel_layout.addWidget(album_art_widget, stretch=0)

        left_panel_container.setMaximumWidth(380)
        self.side_panel = left_panel_container

    # ==========================================================
    #  ANA İÇERİK (playlist + info panel + alt kontroller)
    # ==========================================================
    def _create_main_content(self):
        playbackControls = QHBoxLayout()
        playbackControls.addWidget(self.prevButton)
        playbackControls.addWidget(self.playButton)
        playbackControls.addWidget(self.nextButton)
        playbackControls.addWidget(self.shuffleButton)
        playbackControls.addWidget(self.repeatButton)
        playbackControls.addWidget(self.eqButton)

        utilityControls = QHBoxLayout()
        utilityControls.addStretch(1)
        utilityControls.addWidget(QLabel("Ses:"))
        utilityControls.addWidget(self.volumeSlider)
        utilityControls.addWidget(self.volumeLabel)

        seekLayout = QHBoxLayout()
        seekLayout.addWidget(self.timeLabel)
        seekLayout.addWidget(self.positionSlider)

        topControls = QVBoxLayout()
        topControls.addLayout(seekLayout)
        topControls.addLayout(playbackControls)
        topControls.addLayout(utilityControls)

        bottomControlsContainer = QVBoxLayout()
        bottomControlsContainer.addLayout(topControls)
        bottomControlsContainer.addWidget(self.equalizerWidget)
        bottomControlsContainer.addWidget(self.vis_widget_main_window)

        right_splitter = QSplitter(Qt.Horizontal)
        right_splitter.addWidget(self.playlistWidget)
        # InfoDisplayWidget kaldırıldı - artık album kapağı sol panelin altında
        right_splitter.setSizes([760, 240])

        main_content = QWidget()
        main_layout = QVBoxLayout(main_content)
        main_layout.addWidget(self.fileLabel)
        main_layout.addWidget(right_splitter)
        main_layout.addLayout(bottomControlsContainer)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.side_panel)
        splitter.addWidget(main_content)
        splitter.setSizes([280, 920])

        centralWidget = QWidget()
        self.setCentralWidget(centralWidget)
        mainLayout = QHBoxLayout(centralWidget)
        mainLayout.addWidget(splitter)

        self.setStatusBar(QStatusBar())

    # ==========================================================
    #  MENÜ ÇUBUĞU
    # ==========================================================
    def _create_menu_bar(self):
        menuBar = self.menuBar()

        fileMenu = menuBar.addMenu("&Dosya")
        addFilesAction = QAction("Dosya(lar) Ekle...", self)
        addFilesAction.triggered.connect(self.menu_add_files)
        fileMenu.addAction(addFilesAction)

        addFolderAction = QAction("Klasör Ekle...", self)
        addFolderAction.triggered.connect(self.menu_add_folder)
        fileMenu.addAction(addFolderAction)

        fileMenu.addSeparator()
        exitAction = QAction("&Çıkış", self)
        exitAction.triggered.connect(self.close)
        fileMenu.addAction(exitAction)

        viewMenu = menuBar.addMenu("&Görünüm")

        toggleEQAction = QAction("Ekolayzırı Göster/Gizle", self)
        toggleEQAction.triggered.connect(self.toggle_equalizer)
        viewMenu.addAction(toggleEQAction)

        toggleVisAction = QAction("Görselleştirme Penceresini Aç", self)
        toggleVisAction.triggered.connect(self.toggle_visualization_window)
        viewMenu.addAction(toggleVisAction)

        themeMenu = viewMenu.addMenu("Tema")
        for name in self.themes.keys():
            a = QAction(name, self)
            a.triggered.connect(
                lambda checked=False, n=name: self.set_theme(n)
            )
            themeMenu.addAction(a)

        toolsMenu = menuBar.addMenu("&Araçlar")
        scanLibAction = QAction("Kütüphaneyi Tara", self)
        scanLibAction.triggered.connect(self.scan_library)
        toolsMenu.addAction(scanLibAction)

        prefsAction = QAction("Tercihler", self)
        prefsAction.triggered.connect(self.show_preferences)
        toolsMenu.addAction(prefsAction)

        helpMenu = menuBar.addMenu("&Yardım")
        aboutAction = QAction("Hakkında", self)
        aboutAction.triggered.connect(self.show_about)
        helpMenu.addAction(aboutAction)

    # ==========================================================
    #  SİNYAL / KISA YOL BAĞLANTILARI
    # ==========================================================
    def _connect_signals(self):
        self.playButton.clicked.connect(self.play_pause)
        self.nextButton.clicked.connect(self._next_track)
        self.prevButton.clicked.connect(self._prev_track)

        self.shuffleButton.clicked.connect(self.toggle_shuffle)
        self.repeatButton.clicked.connect(self.toggle_repeat)
        self.eqButton.clicked.connect(self.toggle_equalizer)

        self.playlistWidget.doubleClicked.connect(self.playlist_double_clicked)
        self.file_tree.doubleClicked.connect(self.file_tree_double_clicked)
        self.playlistWidget.customContextMenuRequested.connect(
            self.show_playlist_context_menu
        )
        self.libraryTableWidget.doubleClicked.connect(self.library_double_clicked)
        self.libraryTableWidget.customContextMenuRequested.connect(
            self.show_library_context_menu
        )

        self.volumeSlider.valueChanged.connect(self.mediaPlayer.setVolume)
        self.volumeSlider.valueChanged.connect(self._update_volume_label)
        self.volumeSlider.valueChanged.connect(self.save_config)

        self.positionSlider.sliderMoved.connect(self._set_position_safely_moved)
        self.positionSlider.sliderReleased.connect(self._set_position_safely)

        self.mediaPlayer.positionChanged.connect(self.position_changed)
        self.mediaPlayer.durationChanged.connect(self.duration_changed)
        self.playlist.currentIndexChanged.connect(self.playlist_position_changed)
        self.mediaPlayer.stateChanged.connect(self._update_status_bar)
        self.mediaPlayer.mediaStatusChanged.connect(self._media_status_changed)

        QShortcut(QKeySequence("Space"), self, activated=self.play_pause)
        QShortcut(QKeySequence("Ctrl+Right"), self,
                 activated=self._next_track)
        QShortcut(QKeySequence("Ctrl+Left"), self,
                 activated=self._prev_track)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.menu_add_files)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.menu_add_folder)

        QShortcut(QKeySequence("Ctrl+A"), self.playlistWidget,
                 activated=self.playlistWidget.selectAll)
        QShortcut(QKeySequence("Ctrl+A"), self.libraryTableWidget,
                 activated=self.libraryTableWidget.selectAll)
        QShortcut(QKeySequence("Ctrl+A"), self.file_tree,
                 activated=self.file_tree.selectAll)


    # ------------------------------------------------------------------#
    # OYNATMA
    # ------------------------------------------------------------------#

    def play_pause(self):
        if self.mediaPlayer.state() == QMediaPlayer.PlayingState:
            self.mediaPlayer.pause()
        elif self.mediaPlayer.state() == QMediaPlayer.PausedState:
            self.mediaPlayer.play()
        else:
            if self.playlist.currentIndex() >= 0:
                self.mediaPlayer.play()

    def _next_track(self):
        if self.playlist.mediaCount() == 0:
            return
        # Manuel geçişlerde önce volümü hızlıca azalt, sonra parçayı değiştir ve tekrar aç
        self._fade_and_advance(next=True)

    def _prev_track(self):
        if self.playlist.mediaCount() == 0:
            return
        # Manuel geçişlerde önce volümü azalt, sonra parçayı geri al ve tekrar aç
        self._fade_and_advance(next=False)

    def _fade_and_advance(self, next=True, fade_ms=600):
        # Basit fade out, değiştir, fade in
        try:
            start_vol = self.volumeSlider.value()
            steps = 12
            interval = max(20, int(fade_ms / steps))
            step_delta = max(1, int(start_vol / steps))

            def do_fade_out():
                nonlocal steps
                v = self.mediaPlayer.volume()
                v = max(0, v - step_delta)
                self.mediaPlayer.setVolume(v)
                if v <= 0:
                    fade_timer.stop()
                    # İleri/geri
                    if next:
                        self.playlist.next()
                    else:
                        self.playlist.previous()
                    # Yeni parçayı oynat
                    self.mediaPlayer.play()
                    # Fade in
                    self._fade_in_to(start_vol)

            fade_timer = QTimer(self)
            fade_timer.timeout.connect(do_fade_out)
            fade_timer.start(interval)
        except Exception:
            # Fallback: normal davranış
            if next:
                self.playlist.next()
            else:
                self.playlist.previous()
            self.mediaPlayer.play()

    def _fade_in_to(self, target_vol=70, fade_ms=600):
        try:
            current = self.mediaPlayer.volume()
            steps = 12
            interval = max(20, int(fade_ms / steps))
            step_delta = max(1, int((target_vol - current) / steps))

            def do_fade_in():
                v = self.mediaPlayer.volume()
                v = min(target_vol, v + step_delta)
                self.mediaPlayer.setVolume(v)
                if v >= target_vol:
                    tin.stop()

            tin = QTimer(self)
            tin.timeout.connect(do_fade_in)
            tin.start(interval)
        except Exception:
            pass

    def toggle_shuffle(self):
        if self.playlist.playbackMode() != QMediaPlaylist.Random:
            self.is_repeating = self.playlist.playbackMode()
            self.playlist.setPlaybackMode(QMediaPlaylist.Random)
            self.shuffleButton.setText("🔀 (On)")
        else:
            self.playlist.setPlaybackMode(self.is_repeating)
            self.shuffleButton.setText("🔀 (Off)")
        self.save_config()

    def toggle_repeat(self):
        current_mode = self.playlist.playbackMode()
        if current_mode == QMediaPlaylist.Random and self.is_repeating != QMediaPlaylist.Random:
            current_mode = self.is_repeating

        if current_mode == QMediaPlaylist.Sequential:
            new_mode = QMediaPlaylist.CurrentItemInLoop
            self.repeatButton.setText("🔁 (One)")
        elif current_mode == QMediaPlaylist.CurrentItemInLoop:
            new_mode = QMediaPlaylist.Loop
            self.repeatButton.setText("🔁 (All)")
        elif current_mode == QMediaPlaylist.Loop:
            new_mode = QMediaPlaylist.Sequential
            self.repeatButton.setText("🔁 (Off)")
        else:
            new_mode = QMediaPlaylist.Sequential
            self.repeatButton.setText("🔁 (Off)")

        if self.playlist.playbackMode() != QMediaPlaylist.Random:
            self.playlist.setPlaybackMode(new_mode)
        self.is_repeating = new_mode
        self.save_config()

    def _update_volume_label(self, value):
        self.volumeLabel.setText(f"{value}%")

    def toggle_equalizer(self):
        if self.equalizerWidget.isVisible():
            self.equalizerWidget.hide()
            self.statusBar().showMessage("Ekolayzır Gizlendi", 2000)
        else:
            self.equalizerWidget.show()
            # Kullanıcıyı ilk açılışta bilgilendir
            if not hasattr(self, '_eq_first_shown'):
                self._eq_first_shown = True
                self.statusBar().showMessage(
                    "⚠️ NOT: Ekolayzır şu anda sadece görselleştirmeyi etkiler. "
                    "Gerçek ses efekti için gelecek sürümlerde VLC desteği eklenecek.",
                    6000
                )
            else:
                self.statusBar().showMessage("Ekolayzır Gösterildi", 2000)

    def set_visualization_mode(self, mode: str):
        self.vis_mode = mode
        self.config_data["vis_mode"] = mode
        if self.vis_widget_main_window:
            self.vis_widget_main_window.set_vis_mode(mode)
        if self.vis_window and self.vis_window.visualizationWidget:
            self.vis_window.visualizationWidget.set_vis_mode(mode)
        self.save_config()
        self.statusBar().showMessage(f"Görselleştirme modu: {mode}", 3000)

    def toggle_visualization_window(self):
        if self.vis_window and self.vis_window.isVisible():
            self.vis_window.close()
            self.vis_window = None
            self.statusBar().showMessage("Görselleştirme Penceresi Kapandı", 2000)
        else:
            self.vis_window = VisualizationWindow(self)
            self.vis_window.show()
            self.vis_window.visualizationWidget.set_color_theme(
                self.themes[self.theme][0],
                self.themes[self.theme][2]
            )
            self.statusBar().showMessage("Görselleştirme Penceresi Açıldı", 2000)

    def _vis_window_closed(self):
        self.vis_window = None

    def _update_status_bar(self, state):
        if state == QMediaPlayer.PlayingState:
            self.playButton.setText("⏸️")
            self.statusBar().showMessage(
                f"Çalınıyor: {self.fileLabel.text().replace('Şu An Çalınan: ', '')}",
                0
            )
        elif state == QMediaPlayer.PausedState:
            self.playButton.setText("▶️")
            self.statusBar().showMessage(
                f"Duraklatıldı: {self.fileLabel.text().replace('Şu An Çalınan: ', '')}",
                0
            )
        elif state == QMediaPlayer.StoppedState:
            self.playButton.setText("▶️")
            self.statusBar().showMessage("Durduruldu.", 3000)

    # ------------------------------------------------------------------#
    # MEDYA OLAYLARI
    # ------------------------------------------------------------------#

    def position_changed(self, position):
        if not self.positionSlider.isSliderDown():
            self.positionSlider.setValue(position)

        total_duration = self.mediaPlayer.duration()
        if total_duration > 0:
            current_time = QTime(0, 0).addMSecs(position).toString("mm:ss")
            total_time = QTime(0, 0).addMSecs(total_duration).toString("mm:ss")
            self.timeLabel.setText(f"{current_time} / {total_time}")
            # İlerleme çubuğunun dolu kısmını tema rengine göre renklendir
            try:
                pct = int((position / total_duration) * 100)
            except Exception:
                pct = 0
            primary = self.themes.get(self.theme, ("#40C4FF", "#FFFFFF", "#2A2A2A"))[0]
            accent = "#FFFFFF"
            # Stil: sub-page rengi tema renkleriyle degrade
            style = f"QSlider::groove:horizontal{{height:8px;background:#333333;border-radius:4px;}}"
            style += (
                "QSlider::sub-page:horizontal{height:8px;border-radius:4px;"
                f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {primary}, stop:1 {accent});}}"
            )
            style += "QSlider::add-page:horizontal{background: #2E2E2E;border-radius:4px;}QSlider::handle:horizontal{background:#FFFFFF;border:1px solid #888;width:12px;margin:-3px 0;border-radius:6px;}"
            try:
                self.positionSlider.setStyleSheet(style)
            except Exception:
                pass

    def duration_changed(self, duration):
        self.positionSlider.setRange(0, duration)
        if duration > 0:
            total_time = QTime(0, 0).addMSecs(duration).toString("mm:ss")
            self.timeLabel.setText(f"00:00 / {total_time}")
        else:
            self.timeLabel.setText("00:00 / 00:00")

    def _set_position_safely_moved(self, position):
        total_duration = self.mediaPlayer.duration()
        if total_duration > 0:
            current_time = QTime(0, 0).addMSecs(position).toString("mm:ss")
            total_time = QTime(0, 0).addMSecs(total_duration).toString("mm:ss")
            self.timeLabel.setText(f"{current_time} / {total_time}")

    def _set_position_safely(self):
        if self.mediaPlayer.isSeekable():
            self.mediaPlayer.setPosition(self.positionSlider.value())

    def playlist_position_changed(self, index):
        if index < 0 or index >= self.playlist.mediaCount():
            self.current_file_path = None
            self.fileLabel.setText("Şu An Çalınan: -")
            self.infoDisplayWidget.clear_info()
            # Sol paneldeki album kapağını temizle
            self.albumArtLabel.setText("Albüm Yok")
            self.albumArtLabel.setPixmap(QPixmap())
            if self.vis_widget_main_window:
                self.vis_widget_main_window.update_sound_data(0.0, [0.0] * 10)
            return

        url = self.playlist.media(index).request().url()
        self.current_file_path = url.toLocalFile()
        title, artist, album = self._get_tags_from_file(self.current_file_path)

        self.fileLabel.setText(f"Şu An Çalınan: {artist} - {title}")
        self.infoDisplayWidget.update_info(
            title, artist, album, self.current_file_path
        )

        # Sol paneldeki album kapağını güncelle
        try:
            self.infoDisplayWidget.update_info(title, artist, album, self.current_file_path)
        except Exception:
            pass

        for i in range(self.playlistWidget.count()):
            item = self.playlistWidget.item(i)
            item.setSelected(i == index)
        if 0 <= index < self.playlistWidget.count():
            self.playlistWidget.setCurrentRow(index)

        # Yeni parçaya geçildiğinde yumuşak açma (volume fade-in)
        try:
            # Hedef volüm olarak slider değerini al
            target_vol = self.volumeSlider.value()
            # Başlangıçta volüm çok düşükse (otomatik geçişlerde), önce 0'a çek ve yavaşça aç
            if self.mediaPlayer.volume() > target_vol:
                # Eğer oynatılanın volümü hedefin üzerinde ise doğrudan ayarla
                self.mediaPlayer.setVolume(target_vol)
            else:
                # Eğer şu an küçükse, fade in
                self.mediaPlayer.setVolume(0)
                self._fade_in_to(target_vol)
        except Exception:
            pass

    def _media_status_changed(self, status):
        if status == QMediaPlayer.EndOfMedia:
            if (
                self.playlist.playbackMode() == QMediaPlaylist.Sequential
                and self.playlist.currentIndex() == self.playlist.mediaCount() - 1
            ):
                self.statusBar().showMessage("Çalma listesi sona erdi.", 3000)

    def update_playlist_order_after_drag(self):
        new_paths = []
        for i in range(self.playlistWidget.count()):
            item = self.playlistWidget.item(i)
            new_paths.append(item.data(Qt.UserRole))

        current_path = self.current_file_path

        self.playlist.clear()
        for path in new_paths:
            self.playlist.addMedia(QMediaContent(QUrl.fromLocalFile(path)))

        if current_path and current_path in new_paths:
            new_index = new_paths.index(current_path)
            self.playlist.setCurrentIndex(new_index)
        elif self.playlist.mediaCount() > 0:
            self.playlist.setCurrentIndex(0)

        if self.mediaPlayer.state() == QMediaPlayer.PlayingState:
            self.mediaPlayer.play()

    # ------------------------------------------------------------------#
    # MEDYA EKLEME
    # ------------------------------------------------------------------#

    def _add_media(self, file_path, add_to_library=False):
        if not os.path.exists(file_path):
            return

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in [".mp3", ".flac", ".ogg", ".m4a", ".wav"]:
            self.statusBar().showMessage(
                f"Hata: Desteklenmeyen dosya türü: {ext}", 5000
            )
            return

        title, artist, album, duration = self._get_tags_from_file_with_duration(file_path)

        if add_to_library:
            self.library.add_track(file_path, {
                "title": title,
                "artist": artist,
                "album": album,
                "duration": duration,
            })

        url = QUrl.fromLocalFile(file_path)
        self.playlist.addMedia(QMediaContent(url))

        display_text = f"{artist} - {title}"
        item = QListWidgetItem(display_text)
        item.setData(Qt.UserRole, file_path)
        self.playlistWidget.addItem(item)

        self.statusBar().showMessage(
            f"Çalma listesine eklendi: {display_text}", 3000
        )

    def _add_files_to_playlist(self, paths: list, add_to_library=False):
        for path in paths:
            if os.path.isdir(path):
                self._add_folder(path, add_to_library)
            else:
                self._add_media(path, add_to_library)
        if add_to_library:
            self.refresh_library_view()

    def _add_folder(self, folder_path, add_to_library=False):
        if not os.path.isdir(folder_path):
            return
        for root, _, files in os.walk(folder_path):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in [".mp3", ".flac", ".ogg", ".m4a", ".wav"]:
                    self._add_media(os.path.join(root, file), add_to_library)

    def _get_tags_from_file_with_duration(self, file_path):
        title = os.path.basename(file_path)
        artist = "Bilinmeyen Sanatçı"
        album = "Bilinmeyen Albüm"
        duration = 0

        if MutagenFile is not None and os.path.exists(file_path):
            try:
                audio = MutagenFile(file_path)
                if audio:
                    if audio.info and hasattr(audio.info, "length"):
                        duration = int(audio.info.length * 1000)

                    if audio.tags:
                        if ID3 and isinstance(audio.tags, ID3):
                            title = str(audio.tags.get("TIT2", [title])[0])
                            artist = str(audio.tags.get("TPE1", [artist])[0])
                            album = str(audio.tags.get("TALB", [album])[0])
                        elif MP4 and isinstance(audio, MP4):
                            title = str(audio.tags.get("\xa9nam", [title])[0])
                            artist = str(audio.tags.get("\xa9ART", [artist])[0])
                            album = str(audio.tags.get("\xa9alb", [album])[0])
            except Exception:
                pass

        return title, artist, album, duration

    def _get_tags_from_file(self, file_path):
        t, a, al, _ = self._get_tags_from_file_with_duration(file_path)
        return t, a, al

    # ------------------------------------------------------------------#
    # KÜTÜPHANE
    # ------------------------------------------------------------------#

    def scan_library(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Kütüphaneye Klasör Ekle ve Tara"
        )
        if folder:
            self.statusBar().showMessage("Kütüphane taranıyor...", 0)
            self._add_folder(folder, add_to_library=True)
            self.refresh_library_view()
            self.statusBar().showMessage("Kütüphane taraması tamamlandı.", 3000)

    def refresh_library_view(self):
        tracks = self.library.get_all_tracks()
        self.libraryTableWidget.load_tracks(tracks)

    def show_library_context_menu(self, point):
        menu = QMenu(self)
        item = self.libraryTableWidget.itemAt(point)
        if item:
            add_to_playlist = QAction("Çalma Listesine Ekle", self)
            add_to_playlist.triggered.connect(self.add_selected_lib_to_playlist)
            menu.addAction(add_to_playlist)
        menu.exec_(self.libraryTableWidget.mapToGlobal(point))

    def add_selected_lib_to_playlist(self):
        paths = self.libraryTableWidget.get_selected_paths()
        for path in paths:
            self._add_media(path, add_to_library=False)

    # ------------------------------------------------------------------#
    # ÇALMA LİSTESİ MENÜSÜ
    # ------------------------------------------------------------------#

    def show_playlist_context_menu(self, point):
        menu = QMenu(self)
        item = self.playlistWidget.itemAt(point)
        if item:
            removeAction = QAction("Seçili Öğeleri Kaldır", self)
            removeAction.triggered.connect(self.remove_selected_playlist_items)
            menu.addAction(removeAction)
            # Panoya kopyala (seçili öğelerin yolları)
            copyPathAction = QAction("Yolu Kopyala (Panoya)", self)
            def _copy_paths():
                items = self.playlistWidget.selectedItems()
                if not items:
                    return
                paths = [it.data(Qt.UserRole) for it in items]
                QApplication.clipboard().setText("\n".join(paths))
                self.statusBar().showMessage("Yollar panoya kopyalandı.", 2000)
            copyPathAction.triggered.connect(_copy_paths)
            menu.addAction(copyPathAction)

            # YouTube'da ara (ilk seçili öğe için)
            ytAction = QAction("YouTube'da Ara", self)
            def _search_youtube():
                items = self.playlistWidget.selectedItems()
                if not items:
                    return
                query = items[0].text()
                q = urllib.parse.quote_plus(query)
                url = f"https://www.youtube.com/results?search_query={q}"
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
            ytAction.triggered.connect(_search_youtube)
            menu.addAction(ytAction)

        clearAction = QAction("Çalma Listesini Temizle", self)
        clearAction.triggered.connect(self.clear_playlist)
        menu.addAction(clearAction)

        menu.exec_(self.playlistWidget.mapToGlobal(point))

    def remove_selected_playlist_items(self):
        items_to_remove = self.playlistWidget.selectedItems()
        if not items_to_remove:
            return

        rows = sorted(
            [self.playlistWidget.row(item) for item in items_to_remove],
            reverse=True
        )
        for row in rows:
            self.playlist.removeMedia(row)
        for item in items_to_remove:
            self.playlistWidget.takeItem(self.playlistWidget.row(item))

        self.statusBar().showMessage(
            f"{len(rows)} öğe çalma listesinden kaldırıldı.", 3000
        )

    def clear_playlist(self):
        self.playlist.clear()
        self.playlistWidget.clear()
        self.mediaPlayer.stop()
        self.current_file_path = None
        self.fileLabel.setText("Şu An Çalınan: -")
        self.infoDisplayWidget.clear_info()
        self.statusBar().showMessage("Çalma listesi temizlendi.", 3000)

    # ------------------------------------------------------------------#
    # DOSYA NAVİGASYONU
    # ------------------------------------------------------------------#

    def file_tree_double_clicked(self, index):
        path = self.file_model.filePath(index)
        if self.file_model.isDir(index):
            self.file_tree.setRootIndex(index)
        else:
            self._add_files_to_playlist([path])
            self.playlist.setCurrentIndex(self.playlist.mediaCount() - 1)
            self.mediaPlayer.play()

    def _go_up_directory(self):
        current_index = self.file_tree.rootIndex()
        parent_index = self.file_model.parent(current_index)
        if parent_index.isValid() and \
                self.file_model.filePath(current_index) != QDir.homePath():
            self.file_tree.setRootIndex(parent_index)
        elif self.file_model.filePath(current_index) != QDir.homePath():
            self.file_tree.setRootIndex(self.file_model.index(QDir.homePath()))

    def menu_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Müzik Dosyası Ekle", QDir.homePath(),
            "Müzik Dosyaları (*.mp3 *.flac *.ogg *.m4a *.wav)"
        )
        if files:
            self._add_files_to_playlist(files, add_to_library=False)

    def menu_add_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Klasör Ekle", QDir.homePath()
        )
        if folder:
            self._add_folder(folder, add_to_library=False)

    def playlist_double_clicked(self, index):
        item = self.playlistWidget.item(index.row())
        if not item:
            return

        filepath = item.data(Qt.UserRole)
        if not filepath or not os.path.exists(filepath):
            print("Dosya bulunamadı:", filepath)
            return


        self.play_file(filepath)

    def library_double_clicked(self, index: QModelIndex):
        row = index.row()

        # 1) Dosya yolunu al
        item = self.libraryTableWidget.item(row, 0)
        if not item:
            return

        filepath = item.data(Qt.UserRole)
        if not filepath or not os.path.exists(filepath):
            print("Kütüphane dosya bulunamadı:", filepath)
            return

        # 2) Play
        self.play_file(filepath)


    # --------------------------------------------------------------
    #  TERCIHLER PENCERESINI AÇ (Eksik olan fonksiyon ekleniyor)
    # --------------------------------------------------------------
    def show_preferences(self):
        dialog = PreferencesDialog(self)
        dialog.exec_()

    # --------------------------------------------------------------
    # HAKKINDA / INFO DİYALOĞU (Eksik olan fonksiyon ekleniyor)
    # --------------------------------------------------------------
    def show_about(self):
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(
            self,
            "Angolla Music Player",
            "🎵 Angolla Music Player\n\n"
            "Clementine ilhamlı gelişmiş PyQt5 müzik oynatıcı.\n"
            "Geliştirici: Muhammet Dali\n"
            "Sürüm: 1.0"
        )



    # ------------------------------------------------------------------#
    # SES VERİSİ / GERÇEK FFT SPEKTRUM
    # ------------------------------------------------------------------#

    def process_audio_buffer(self, buffer):
        # NumPy kontrol et
        if np is None:
            return

        import ctypes, time

        # ==================== BUFFER VERİYİ AL ====================
        try:
            byte_count = buffer.byteCount()
        except:
            return
        if byte_count <= 0:
            return

        ptr = buffer.constData()
        raw = ctypes.string_at(int(ptr), byte_count)

        fmt = buffer.format()
        s = fmt.sampleSize()
        ch = fmt.channelCount()

        # Sample type seç
        if s == 8:
            dtype = np.int8
        elif s == 16:
            dtype = np.int16
        else:
            dtype = np.int32

        samples = np.frombuffer(raw, dtype=dtype)
        if ch == 2:
            samples = samples.reshape(-1, 2).mean(axis=1)

        samples = samples.astype(np.float32)
        samples -= samples.mean()
        samples /= (np.max(np.abs(samples)) + 1e-9)

        # ======================= FFT 64-BAND =======================
        fft = np.fft.rfft(samples)
        mag = np.abs(fft).astype(float)

        # Çok düşük değerleri at (gürültüyü kes)
        mag = np.where(mag < 1e-5, 0.0, mag)

        # Güçlü normalize (gerçek bar boyları için)
        max_val = mag.max()
        if max_val > 0:
            mag /= max_val

        # Aşırı yükseklere fren
        p95 = np.percentile(mag, 95)
        if p95 > 0:
            mag /= p95

        # 0–1 arasında tut
        mag = np.clip(mag, 0.0, 1.0)

        BAR = 96

        # Oluşabilecek boş chunk'ları engellemek için kesme indekslerini güvenle oluştur
        # Log-scale tercih ediliyor ama bazı küçük FFT dizilerinde aynı indeks tekrar edebilir.
        # Öncelik: monoton artan, son indeks kesinlikle len(mag)
        try:
            idx = np.logspace(0, np.log10(max(1, len(mag))), BAR + 1).astype(int)
        except Exception:
            idx = np.linspace(0, len(mag), BAR + 1).astype(int)

        # Ensure indices are monotonic and within bounds
        idx[0] = 0
        idx[-1] = len(mag)
        for j in range(1, len(idx)):
            if idx[j] <= idx[j-1]:
                idx[j] = idx[j-1] + 1
        idx = np.clip(idx, 0, len(mag))

        bars = []
        for i in range(BAR):
            start = idx[i]
            end = idx[i+1]
            # Guard against out-of-range and empty slices
            if start >= end or start >= len(mag):
                # Try to approximate using neighbors (safe fallback)
                left = mag[start-1] if (start-1) >= 0 and (start-1) < len(mag) else 0.0
                right = mag[end] if end < len(mag) else left
                bars.append(float((left + right) / 2.0))
            else:
                chunk = mag[start:end]
                if chunk.size == 0:
                    bars.append(0.0)
                else:
                    bars.append(float(np.mean(chunk)))

        # ==================== EQ KAZANÇ UYGULA ====================
        # EQ bands: [31Hz, 63Hz, 125Hz, 250Hz, 500Hz, 1KHz, 2KHz, 4KHz, 8KHz, 16KHz]
        # Map 96 bars to 10 EQ bands logarithmically
        eq_gains = self.current_eq_gains if hasattr(self, 'current_eq_gains') else [1.0] * 10

        # Map each bar to nearest EQ band based on frequency (log scale)
        for i in range(len(bars)):
            # Bar index as fraction (0 to 1)
            bar_frac = i / max(1, len(bars) - 1) if len(bars) > 1 else 0

            # Map to EQ band index (0 to 9)
            eq_band_idx = int(bar_frac * (len(eq_gains) - 1))
            eq_band_idx = min(eq_band_idx, len(eq_gains) - 1)

            # Apply EQ gain
            bars[i] *= eq_gains[eq_band_idx]

        # Clamp bars to [0, 1] after EQ application
        bars = [np.clip(b, 0.0, 1.0) for b in bars]

        # Normalize intensity as average of first 8 bands (bass)
        intensity = float(sum(bars[:8]) / 8.0) if len(bars) >= 8 else float(sum(bars) / max(1, len(bars)))

        # ================= SEND TO VISUALIZER ======================
        self.last_real_visual_time = time.time()
        self.send_visual_data(intensity, bars)


    def send_visual_data(self, intensity: float, band_vals: list):
        if self.vis_window and self.vis_window.visualizationWidget.isVisible():
            self.vis_window.visualizationWidget.update_sound_data(
                intensity, band_vals
            )

        if self.vis_widget_main_window and self.vis_widget_main_window.isVisible():
            self.vis_widget_main_window.update_sound_data(
                intensity, band_vals
            )

    def _fallback_visual_update(self):
        """
        FFT verisi yoksa çubukları sabit tut (titreşim yok).
        Şarkı durunca her şey sıfırlanır.
        """
        import time

        NUM_BARS = 64

        # Şarkı çalmıyorsa -> tüm çubuklar 0 olsun
        if self.mediaPlayer.state() != QMediaPlayer.PlayingState:
            self.prev_bars = [0.0] * NUM_BARS
            self.send_visual_data(0.0, self.prev_bars)
            return

        # Eğer son FFT verisi yoksa -> en son bilinen değeri sabit göster
        if not hasattr(self, "prev_bars"):
            self.prev_bars = [0.0] * NUM_BARS

        # Barları hafif yumuşat (stabilizasyon için)
        SMOOTH = 0.95
        bars = [
            self.prev_bars[i] * SMOOTH
            for i in range(len(self.prev_bars))
        ]

        self.prev_bars = bars[:]
        intensity = sum(bars[:8]) / 8.0 if bars else 0.0

        self.send_visual_data(intensity, bars)


        # Uzun süre hiç gerçek FFT gelmediyse (ör: probe bozuldu),
        # barları sıfıra indir.
        last = getattr(self, "last_real_visual_time", 0.0)
        if time.time() - last > 1.0:
            self.send_visual_data(0.0, [0.0] * 64)


    def update_fft(self):
        import time

        last = getattr(self, "last_real_visual_time", 0.0)

        if time.time() - last > 0.30:
            self._fallback_visual_update()
            return

        return

    # ------------------------------------------------------------------#
    # AYARLAR / KAYDET / YÜKLE
    # ------------------------------------------------------------------#

    def set_theme(self, name, save=True):
        if name not in self.themes:
            return
        self.theme = name
        primary_color, text_color, bg_color = self.themes[name]

        style = f"""
        QMainWindow, QWidget, QDialog {{
            background-color: {bg_color};
            color: {text_color};
        }}
        QPushButton, QComboBox, QLineEdit {{
            color: {text_color};
            background-color: {QColor(bg_color).lighter(110).name()};
            border: 1px solid {primary_color};
            border-radius: 4px;
        }}
        QPushButton:hover {{
            background-color: {QColor(primary_color).darker(150).name()};
        }}
        QPushButton:pressed {{
            background-color: {QColor(primary_color).darker(200).name()};
        }}
        QSlider::groove:horizontal {{
            border: 0px;
            height: 6px;
            background: #555;
            margin: 2px 0;
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            background: {primary_color};
            border: 1px solid #333;
            width: 14px;
            margin: -4px 0;
            border-radius: 7px;
        }}
        QLabel, QCheckBox {{
            color: {text_color};
        }}
        QListWidget, QTreeView, QTableWidget {{
            border: 1px solid #444;
            background-color: {QColor(bg_color).lighter(105).name()};
        }}
        QListWidget::item:selected, QTreeView::item:selected,
        QTableWidget::item:selected {{
            background: {primary_color};
            color: black;
        }}
        QSplitter::handle {{
            background-color: {QColor(primary_color).darker(130).name()};
        }}
        """

        QApplication.instance().setStyleSheet(style)

        if self.vis_widget_main_window:
            self.vis_widget_main_window.set_color_theme(primary_color, bg_color)
        if self.vis_window and self.vis_window.visualizationWidget:
            self.vis_window.visualizationWidget.set_color_theme(
                primary_color, bg_color
            )
            # Görselleştirme penceresinin arka planını da güncelle
            self.vis_window.setStyleSheet(f"""
                QMainWindow {{
                    background-color: {bg_color};
                }}
            """)

        if save:
            self.save_config()

    def save_playlist(self):
        paths = []
        for i in range(self.playlistWidget.count()):
            item = self.playlistWidget.item(i)
            paths.append(item.data(Qt.UserRole))

        data = {
            "paths": paths,
            "current_index": self.playlist.currentIndex()
        }
        try:
            with open(PLAYLIST_FILE, "wb") as f:
                pickle.dump(data, f)
        except Exception as e:
            print(f"Çalma listesi kaydetme hatası: {e}")

    def load_playlist(self):
        if not os.path.exists(PLAYLIST_FILE):
            return
        try:
            with open(PLAYLIST_FILE, "rb") as f:
                data = pickle.load(f)

            if isinstance(data, dict):
                playlist_paths = data.get("paths", [])
                current_index = data.get("current_index", -1)
            else:
                print("Kayıtlı çalma listesi eski formatta, siliniyor.")
                os.remove(PLAYLIST_FILE)
                return

            self.playlist.clear()
            self.playlistWidget.clear()

            valid_paths = []
            for path in playlist_paths:
                if os.path.exists(path):
                    title, artist, _, _ = \
                        self._get_tags_from_file_with_duration(path)
                    url = QUrl.fromLocalFile(path)
                    self.playlist.addMedia(QMediaContent(url))
                    display_text = f"{artist} - {title}"
                    item = QListWidgetItem(display_text)
                    item.setData(Qt.UserRole, path)
                    self.playlistWidget.addItem(item)
                    valid_paths.append(path)
                else:
                    print(f"Dosya bulunamadı, listeden çıkarılıyor: {path}")

            if valid_paths:
                self.playlist.setCurrentIndex(
                    min(current_index, len(valid_paths) - 1)
                )

            self.statusBar().showMessage(
                f"{len(valid_paths)} parça yüklendi.", 3000
            )
        except Exception as e:
            print(f"Çalma listesi yükleme hatası: {e}")
            if os.path.exists(PLAYLIST_FILE):
                os.remove(PLAYLIST_FILE)

    def save_config(self):
        self.config_data["volume"] = self.mediaPlayer.volume()
        self.config_data["shuffle_mode"] = (
            self.playlist.playbackMode() == QMediaPlaylist.Random
        )
        self.config_data["repeat_mode"] = self.is_repeating
        self.config_data["theme"] = self.theme
        self.config_data["show_album_art"] = self.infoDisplayWidget._album_art_visible
        self.config_data["crossfade_duration"] = \
            self.config_data.get("crossfade_duration", 1000)
        self.config_data["vis_mode"] = self.vis_mode
        self.config_data["eq_gains"] = self.current_eq_gains

        settings = QSettings(SETTINGS_KEY, "AngollaPlayer")
        try:
            settings.setValue("config", QByteArray(pickle.dumps(self.config_data)))
        except Exception as e:
            print(f"Ayar kaydetme hatası: {e}")

    def load_config(self):
        settings = QSettings(SETTINGS_KEY, "AngollaPlayer")
        try:
            data = settings.value("config")
            if data and isinstance(data, QByteArray):
                self.config_data = pickle.loads(data.data())
            else:
                self.config_data = {}
        except Exception:
            self.config_data = {}

        vol = self.config_data.get("volume", 70)
        self.mediaPlayer.setVolume(vol)
        self.volumeSlider.setValue(vol)

        repeat_mode_val = self.config_data.get(
            "repeat_mode", QMediaPlaylist.Sequential
        )
        is_shuffle = self.config_data.get("shuffle_mode", False)
        self.is_repeating = repeat_mode_val

        if is_shuffle:
            self.playlist.setPlaybackMode(QMediaPlaylist.Random)
            self.shuffleButton.setText("🔀 (On)")
        else:
            self.playlist.setPlaybackMode(repeat_mode_val)
            if repeat_mode_val == QMediaPlaylist.CurrentItemInLoop:
                self.repeatButton.setText("🔁 (One)")
            elif repeat_mode_val == QMediaPlaylist.Loop:
                self.repeatButton.setText("🔁 (All)")
            else:
                self.repeatButton.setText("🔁 (Off)")

        theme_name = self.config_data.get("theme", "AURA Mavi")
        self.set_theme(theme_name, save=False)

        show_art = self.config_data.get("show_album_art", True)
        self.infoDisplayWidget.set_album_art_visibility(show_art)

        self.vis_mode = self.config_data.get("vis_mode", "Çizgiler")

        # Çubuk rengi ve stili yükle
        bar_color = self.config_data.get("bar_color", "#40C4FF")
        if self.vis_widget_main_window:
            self.vis_widget_main_window._set_bar_color(bar_color)

        bar_style = self.config_data.get("bar_style", "solid")
        if self.vis_widget_main_window:
            self.vis_widget_main_window.bar_style_mode = bar_style

        eq_gains = self.config_data.get("eq_gains", [1.0] * 10)
        self.current_eq_gains = eq_gains
        self.equalizerWidget.set_gains(eq_gains)

    def closeEvent(self, event):
        if self.vis_window:
            self.vis_window.close()
        try:
            self.save_playlist()
            self.save_config()
            self.library.close()
            self.mediaPlayer.stop()
            if hasattr(self, "fallback_timer") and self.fallback_timer.isActive():
                self.fallback_timer.stop()
            if (self.vis_widget_main_window and
                    self.vis_widget_main_window.animation_timer.isActive()):
                self.vis_widget_main_window.animation_timer.stop()
        except Exception:
            pass
        event.accept()
    # ------------------------------------------------------------ #
    # PLAYLIST – Çoklu seçim + sürükle bırak + CTRL+A aktif etme
    # ------------------------------------------------------------ #
    def enable_playlist_features(self):
        self.playlistWidget.setSelectionMode(QAbstractItemView.ExtendedSelection)  # CTRL+A aktif
        self.playlistWidget.setDragDropMode(QAbstractItemView.InternalMove)        # sürükle bırak
        self.playlistWidget.setDefaultDropAction(Qt.MoveAction)
        self.playlistWidget.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        SUPPORTED_EXT = (
            ".mp3", ".wav", ".flac", ".ogg",
            ".m4a", ".aac",
            ".mp4", ".mkv", ".avi", ".mov", ".webm", ".mpeg"
        )

        paths = []

        for url in event.mimeData().urls():
            p = url.toLocalFile()

            if os.path.isdir(p):
                for root, _, files in os.walk(p):
                    for f in files:
                        if f.lower().endswith(SUPPORTED_EXT):
                            paths.append(os.path.join(root, f))
            else:
                if p.lower().endswith(SUPPORTED_EXT):
                    paths.append(p)

        if paths:
            for path in paths:
                title, artist, _, _ = self._get_tags_from_file_with_duration(path)
                item = QListWidgetItem(f"{artist} - {title}")
                item.setData(Qt.UserRole, path)
                self.playlistWidget.addItem(item)
                self.playlist.addMedia(QMediaContent(QUrl.fromLocalFile(path)))

        event.acceptProposedAction()
        self.save_playlist()

# ---------------------------------------------------------------------------
# AYAR DİYALOĞU
# ---------------------------------------------------------------------------

class PreferencesDialog(QDialog):
    def __init__(self, parent: AngollaPlayer):
        super().__init__(parent)
        self.setWindowTitle("Angolla Ayarları")
        self.parent = parent
        self.setFixedSize(450, 350)
        self._create_widgets()
        self._layout_widgets()
        self._connect_signals()

    def _create_widgets(self):
        self.albumArtCheck = QCheckBox("Albüm Kapağını Göster (Bilgi Paneli)")
        self.albumArtCheck.setChecked(
            self.parent.config_data.get("show_album_art", True)
        )

        self.themeLabel = QLabel("Tema Seçimi:")
        self.themeCombo = QComboBox()
        self.themeCombo.addItems(self.parent.themes.keys())
        current_theme = self.parent.config_data.get("theme", "AURA Mavi")
        self.themeCombo.setCurrentText(current_theme)

        self.crossfadeLabel = QLabel("Crossfade Süresi (ms):")
        self.crossfadeSlider = QSlider(Qt.Horizontal)
        self.crossfadeSlider.setRange(0, 5000)
        self.crossfadeSlider.setSingleStep(100)
        self.crossfadeSlider.setValue(
            self.parent.config_data.get("crossfade_duration", 1000)
        )
        self.crossfadeValueLabel = QLabel(
            f"{self.crossfadeSlider.value()} ms"
        )

        self.visModeLabel = QLabel("Görselleştirme Modu:")
        self.visModeCombo = QComboBox()
        self.visModeCombo.addItems([
            "Çizgiler",
            "Daireler",
            "Spektrum Çubukları",
            "Enerji Halkaları",
            "Dalga Formu",
        ])
        current_mode = self.parent.config_data.get("vis_mode", "Çizgiler")
        self.visModeCombo.setCurrentText(current_mode)

        self.shareLabel = QLabel("Paylaşım Seçeneği:")
        self.shareButton = QPushButton("Şarkıyı Paylaş (Simülasyon)")

    def _layout_widgets(self):
        layout = QGridLayout(self)
        layout.setColumnStretch(1, 1)

        layout.addWidget(self.albumArtCheck, 0, 0, 1, 2)

        layout.addWidget(self.themeLabel, 1, 0)
        layout.addWidget(self.themeCombo, 1, 1)

        layout.addWidget(self.visModeLabel, 2, 0)
        layout.addWidget(self.visModeCombo, 2, 1)

        layout.addWidget(self.crossfadeLabel, 3, 0)

        h_layout = QHBoxLayout()
        h_layout.addWidget(self.crossfadeSlider)
        h_layout.addWidget(self.crossfadeValueLabel)
        layout.addLayout(h_layout, 3, 1)

        layout.addWidget(self.shareLabel, 4, 0)
        layout.addWidget(self.shareButton, 4, 1)

        layout.setRowStretch(5, 1)

    def _connect_signals(self):
        self.albumArtCheck.stateChanged.connect(self._apply_settings)
        self.themeCombo.currentTextChanged.connect(self._apply_settings)
        self.crossfadeSlider.valueChanged.connect(self._update_crossfade_label)
        self.crossfadeSlider.sliderReleased.connect(self._apply_settings)
        self.visModeCombo.currentTextChanged.connect(self._apply_settings)
        self.shareButton.clicked.connect(self._share_clicked)

    def _update_crossfade_label(self, value):
        self.crossfadeValueLabel.setText(f"{value} ms")

    def _apply_settings(self):
        self.parent.config_data["show_album_art"] = \
            self.albumArtCheck.isChecked()
        self.parent.config_data["crossfade_duration"] = \
            self.crossfadeSlider.value()

        selected_theme = self.themeCombo.currentText()
        if self.parent.theme != selected_theme:
            self.parent.set_theme(selected_theme, save=False)

        new_vis_mode = self.visModeCombo.currentText()
        if self.parent.vis_mode != new_vis_mode:
            self.parent.vis_mode = new_vis_mode
            self.parent.config_data["vis_mode"] = new_vis_mode
            if self.parent.vis_widget_main_window:
                self.parent.vis_widget_main_window.set_vis_mode(new_vis_mode)
            if self.parent.vis_window:
                self.parent.vis_window.visualizationWidget.set_vis_mode(
                    new_vis_mode
                )

        self.parent.infoDisplayWidget.set_album_art_visibility(
            self.albumArtCheck.isChecked()
        )
        self.parent.save_config()

    def _share_clicked(self):
        current_file = self.parent.current_file_path
        if current_file:
            title, artist, _ = self.parent._get_tags_from_file(current_file)
            QMessageBox.information(
                self, "Paylaşım Başarılı",
                f"'{artist} - {title}' paylaşım için kopyalandı (simülasyon)!"
            )
        else:
            QMessageBox.warning(
                self, "Paylaşım Hatası",
                "Şu an oynatılan bir parça yok."
            )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Ubuntu", 10))
    window = AngollaPlayer()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    if MutagenFile is None:
        print("\n!!! UYARI: Mutagen yüklenmedi. 'pip install mutagen' ile yükleyin.")
    if np is None:
        print("\n!!! UYARI: NumPy yüklenmedi. Görselleştirme sınırlı çalışacak. 'pip install numpy' önerilir.")
    main()
