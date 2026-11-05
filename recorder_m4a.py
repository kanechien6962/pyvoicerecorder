import sys
import os
import math
import tempfile
from datetime import datetime

import numpy as np
import sounddevice as sd
import soundfile as sf
from pydub import AudioSegment
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QMessageBox
)
from PyQt6.QtCore import QTimer, Qt, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QLinearGradient, QFont


# ───────────────────────── 自訂元件 ─────────────────────────

class LevelMeter(QWidget):
    """即時音量條（綠 → 黃 → 紅），帶平滑下降效果"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self._level = 0.0

    def set_level(self, v: float):
        # 快速上升、緩慢下降
        self._level = max(v, self._level - 0.06)
        self.update()

    def reset(self):
        self._level = 0.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        radius = r.height() / 2

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 28))
        p.drawRoundedRect(r, radius, radius)

        w = r.width() * min(max(self._level, 0.0), 1.0)
        if w > 1:
            grad = QLinearGradient(r.left(), 0, r.right(), 0)
            grad.setColorAt(0.0, QColor("#4ade80"))
            grad.setColorAt(0.65, QColor("#facc15"))
            grad.setColorAt(1.0, QColor("#f87171"))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(r.left(), r.top(), w, r.height()), radius, radius)


class RecordButton(QPushButton):
    """圓形錄音鈕：待機＝紅色圓點，錄音中＝紅色方塊＋呼吸光圈"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(60, 60)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.recording = False
        self._phase = 0.0
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)

    def set_recording(self, on: bool):
        self.recording = on
        if on:
            self._anim.start(30)
        else:
            self._anim.stop()
            self._phase = 0.0
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.06) % (2 * math.pi)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)

        # 呼吸光圈
        if self.recording:
            pulse = (math.sin(self._phase) + 1) / 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(248, 113, 113, int(30 + 50 * pulse)))
            rr = 24 + 5 * pulse
            p.drawEllipse(c, rr, rr)

        # 外環
        ring = QColor(255, 255, 255, 200 if self.underMouse() else 140)
        p.setPen(QPen(ring, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, 24, 24)

        # 內部圖形
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ef4444" if not self.underMouse() else "#f87171"))
        if self.recording:
            p.drawRoundedRect(QRectF(c.x() - 9, c.y() - 9, 18, 18), 4, 4)
        else:
            p.drawEllipse(c, 17, 17)


# ───────────────────────── 主視窗 ─────────────────────────

class VoiceRecorderApp(QWidget):
    RADIUS = 22
    BG = QColor(28, 30, 38, 64)       # 半透明深色底（alpha 可自行調整）
    BORDER = QColor(255, 255, 255, 45)

    def __init__(self):
        super().__init__()
        self.is_recording = False
        self.samplerate = 48000  # 48 kHz
        self.channels = 1        # 單聲道
        self.audio_data = []
        self.stream = None
        self._peak = 0.0
        self.seconds = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_timer)

        self.meter_timer = QTimer(self)
        self.meter_timer.timeout.connect(self.update_meter)

        self.initUI()

    # ---------- UI ----------
    def initUI(self):
        self.setWindowTitle('48kHz/24-bit AAC recorder')
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint #| Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(240, 210)

        self.setStyleSheet("""
            QLabel { background: transparent; color: white; }
            QPushButton#tiny {
                background: transparent; color: rgba(255,255,255,150);
                border: none; border-radius: 10px; font-size: 12px;
                min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px;
            }
            QPushButton#tiny:hover { background: rgba(255,255,255,40); color: white; }
            QPushButton#close:hover { background: rgba(239,68,68,200); color: white; }
            QPushButton#tiny:checked { color: #facc15; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(6)

        # 標題列
        top = QHBoxLayout()
        top.setSpacing(4)
        self.status_label = QLabel('● READY')
        self.status_label.setStyleSheet(
            'color: rgba(255,255,255,140); font-size: 10px; font-weight: 600; letter-spacing: 1px;')
        top.addWidget(self.status_label)
        top.addStretch()

        self.pin_btn = QPushButton('📌')
        self.pin_btn.setObjectName('tiny')
        self.pin_btn.setCheckable(True)
        self.pin_btn.setChecked(True)
        self.pin_btn.setToolTip('Always on top')
        self.pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pin_btn.clicked.connect(self.toggle_on_top)
        top.addWidget(self.pin_btn)

        self.min_btn = QPushButton('–')
        self.min_btn.setObjectName('tiny')
        self.min_btn.setToolTip('Minimize')
        self.min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.min_btn.clicked.connect(self.showMinimized)
        top.addWidget(self.min_btn)

        self.close_btn = QPushButton('✕')
        self.close_btn.setObjectName('tiny')
        self.close_btn.setProperty('class', 'close')
        self.close_btn.setStyleSheet('')  # 保持全域樣式
        self.close_btn.setToolTip('Close')
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.close)
        top.addWidget(self.close_btn)
        root.addLayout(top)

        # 時間
        self.time_label = QLabel('00:00')
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont('Segoe UI', 30)
        f.setWeight(QFont.Weight.Light)
        self.time_label.setFont(f)
        root.addWidget(self.time_label)

        # 音量條
        self.meter = LevelMeter()
        root.addWidget(self.meter)
        root.addSpacing(4)

        # 錄音鈕
        self.rec_btn = RecordButton()
        self.rec_btn.clicked.connect(self.toggle_recording)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.rec_btn)
        row.addStretch()
        root.addLayout(row)

        # 規格說明
        info = QLabel('48 kHz · 24-bit · AAC 192k')
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet('color: rgba(255,255,255,90); font-size: 9px;')
        root.addWidget(info)

    # ---------- 繪製圓角半透明背景 ----------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(self.BORDER, 1))
        p.setBrush(self.BG)
        p.drawRoundedRect(r, self.RADIUS, self.RADIUS)

    # ---------- 拖曳視窗 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                return
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and hasattr(self, '_drag_pos'):
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def wheelEvent(self, e):
        """滑鼠滾輪調整整體透明度"""
        op = self.windowOpacity() + (0.05 if e.angleDelta().y() > 0 else -0.05)
        self.setWindowOpacity(min(1.0, max(0.4, op)))

    def toggle_on_top(self):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.pin_btn.isChecked())
        self.show()  # 變更 flag 後需重新 show

    # ---------- 錄音邏輯 ----------
    def audio_callback(self, indata, frames, time, status):
        if status:
            print(status, file=sys.stderr)
        if self.is_recording:
            self.audio_data.append(indata.copy())
            self._peak = float(np.abs(indata).max())

    def toggle_recording(self):
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        self.audio_data = []
        self.seconds = 0
        self._peak = 0.0
        self.time_label.setText('00:00')

        try:
            self.stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=self.channels,
                dtype='float32',
                callback=self.audio_callback
            )
            self.stream.start()
        except Exception as e:
            self.stream = None
            QMessageBox.critical(self, "Error", f"Unable to access the microphone：\n{str(e)}")
            return

        self.is_recording = True
        self.timer.start(1000)
        self.meter_timer.start(33)
        self.rec_btn.set_recording(True)
        self.status_label.setText('● REC')
        self.status_label.setStyleSheet(
            'color: #f87171; font-size: 10px; font-weight: 700; letter-spacing: 1px;')

    def _release_stream(self):
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def stop_recording(self):
        if not self.is_recording:
            return

        self.is_recording = False
        self.timer.stop()
        self.meter_timer.stop()
        self.meter.reset()
        self._release_stream()

        self.rec_btn.set_recording(False)
        self.status_label.setText('● READY')
        self.status_label.setStyleSheet(
            'color: rgba(255,255,255,140); font-size: 10px; font-weight: 600; letter-spacing: 1px;')

        if not self.audio_data:
            return

        today_str = datetime.now().strftime("%Y%m%d%H%M")
        audio_np = np.concatenate(self.audio_data, axis=0)

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save M4A file", f"{today_str}.m4a", "AAC Audio (*.m4a)"
        )
        if not file_path:
            return

        fd, temp_wav = tempfile.mkstemp(suffix='.wav')
        os.close(fd)
        try:
            # 1. 24-bit PCM 暫存檔
            sf.write(temp_wav, audio_np, self.samplerate, subtype='PCM_24')
            # 2. 轉為 MPEG-4 AAC (.m4a)
            seg = AudioSegment.from_file(temp_wav, format="wav")
            seg.export(file_path, format="ipod", codec="aac", bitrate="192k")
            QMessageBox.information(self, "Successfully", f"saved to：\n{file_path}")
        except Exception as e:
            QMessageBox.critical(
                self, "Error",
                f"encode failed（Please make sure FFmpeg is installed）：\n{str(e)}")
        finally:
            if os.path.exists(temp_wav):
                os.remove(temp_wav)

    def update_meter(self):
        # 峰值 → dBFS → 0~1（-60 dB ~ 0 dB）
        db = 20 * math.log10(self._peak + 1e-6)
        self.meter.set_level(max(0.0, (db + 60) / 60))
        self._peak = 0.0

    def update_timer(self):
        self.seconds += 1
        mins, secs = divmod(self.seconds, 60)
        self.time_label.setText(f'{mins:02d}:{secs:02d}')

    def closeEvent(self, e):
        self.is_recording = False
        self._release_stream()
        super().closeEvent(e)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    recorder = VoiceRecorderApp()
    recorder.show()
    sys.exit(app.exec())
