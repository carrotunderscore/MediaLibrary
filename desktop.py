"""Native desktop shell for Media Library's local server."""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

import multiprocessing.util

from PySide6.QtCore import (
    QEvent, QFile, QIODevice, QObject, Qt, QTimer, QUrl, Signal, Slot,
)
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QLabel, QMainWindow, QMessageBox, QStackedWidget,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
BUNDLE_DIR = ROOT
if getattr(sys, "frozen", False):
    os.environ["MEDIALIBRARY_DATA_DIR"] = str(
        Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MediaLibrary" / "data"
    )
os.chdir(ROOT)
sys.path.insert(0, str(BUNDLE_DIR))
from app.config import DATA_DIR, load_settings  # noqa: E402

PLAYBACK_LOG = DATA_DIR / "playback.log"
ICON_PATH = ROOT / "assets" / "icon.png"


def _port() -> int:
    return int(load_settings().get("port") or 8420)


def _log(message: str) -> None:
    from app.config import DATA_DIR
    path = DATA_DIR / "startup.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")


def _is_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _server_lock_name(port: int) -> str:
    return f"MediaLibraryDesktop-{port}"


def _server_running(port: int) -> bool:
    probe = QLocalSocket()
    probe.connectToServer(_server_lock_name(port))
    return probe.waitForConnected(100)


def _acquire_server_lock(port: int) -> QLocalServer | None:
    server = QLocalServer()
    server.removeServer(_server_lock_name(port))
    if server.listen(_server_lock_name(port)):
        return server
    return None


def _port_available(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _js_log(message: str) -> None:
    """Skriv webvyens konsolmeddelanden till data/playback.log."""
    try:
        if PLAYBACK_LOG.exists() and PLAYBACK_LOG.stat().st_size > 2_000_000:
            PLAYBACK_LOG.unlink()
        with PLAYBACK_LOG.open("a", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
    except OSError:
        pass


class ConsolePage(QWebEnginePage):
    """Vidarebefordrar webvyens konsol till en loggfil på disk."""

    def javaScriptConsoleMessage(self, _level, message, line, source):  # noqa: N802
        _js_log(f"{message} ({source}:{line})")


class StartupSignals(QObject):
    ready = Signal()
    failed = Signal(str)


class ShellBridge(QObject):
    """Brygga som låter webvyn styra det ramlösa skalet.

    Qt-webvyn har Fullscreen API:t avstängt (document.fullscreenEnabled är
    False), så fullskärm, fönsterknapparna och fönsterflytten går via den
    här kanalen i stället.
    """

    fullscreenChanged = Signal(bool)
    maximizedChanged = Signal(bool)

    def __init__(self, window: "DesktopWindow") -> None:
        super().__init__(window)
        self._window = window

    @Slot(bool)
    def setFullscreen(self, enabled: bool) -> None:  # noqa: N802 - JS-namn
        if enabled:
            self._window.enter_fullscreen()
        else:
            self._window.exit_fullscreen()
        self.fullscreenChanged.emit(self._window.isFullScreen())

    @Slot(result=bool)
    def isFullscreen(self) -> bool:  # noqa: N802 - JS-namn
        return self._window.isFullScreen()

    @Slot()
    def minimize(self) -> None:
        self._window.showMinimized()

    @Slot(result=bool)
    def toggleMaximize(self) -> bool:  # noqa: N802 - JS-namn
        window = self._window
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()
        self.maximizedChanged.emit(window.isMaximized())
        return window.isMaximized()

    @Slot()
    def closeWindow(self) -> None:  # noqa: N802 - JS-namn
        self._window.close()

    @Slot()
    def startDrag(self) -> None:  # noqa: N802 - JS-namn
        handle = self._window.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    @Slot(str)
    def startResize(self, edges: str) -> None:  # noqa: N802 - JS-namn
        handle = self._window.windowHandle()
        if handle is None or self._window.isFullScreen():
            return
        flags = Qt.Edge(0)
        for part in edges.split("-"):
            flag = _EDGE_FLAGS.get(part)
            if flag is not None:
                flags |= flag
        if flags:
            handle.startSystemResize(flags)


_EDGE_FLAGS = {
    "left": Qt.Edge.LeftEdge,
    "right": Qt.Edge.RightEdge,
    "top": Qt.Edge.TopEdge,
    "bottom": Qt.Edge.BottomEdge,
}


class NativeGamepad(QObject):
    """Läser en fysisk handkontroll via XInput och skickar den till webvyn.

    Qt-webvyn saknar ibland Gamepad API, så skalskriptet pollar kontrollen
    själv och anropar window.__mlPadInput(action) i sidan. Samma kommandon
    (upp/ner/vänster/höger, A, B, Start) som tangentbordet använder.
    """

    BUTTONS = (
        (0x0001, "up"), (0x0002, "down"), (0x0004, "left"), (0x0008, "right"),
        (0x1000, "a"), (0x2000, "b"), (0x0010, "start"), (0x0020, "b"),
    )
    ACTIONS = ("up", "down", "left", "right", "a", "b", "start")

    def __init__(self, window: "DesktopWindow") -> None:
        super().__init__(window)
        self._window = window
        self._get_state = None
        self._state_cls = None
        self._connected = False
        self._held: dict[str, bool] = {}
        self._next_at: dict[str, float] = {}
        if sys.platform == "win32":
            try:
                self._load_xinput()
            except OSError:
                self._get_state = None

    @property
    def available(self) -> bool:
        return self._get_state is not None

    def _load_xinput(self) -> None:
        import ctypes

        class _Pad(ctypes.Structure):
            _fields_ = [
                ("wButtons", ctypes.c_ushort),
                ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short),
            ]

        class _State(ctypes.Structure):
            _fields_ = [("dwPacketNumber", ctypes.c_ulong), ("Gamepad", _Pad)]

        for name in ("XInput1_4.dll", "XInput1_3.dll", "xinput9_1_0.dll"):
            try:
                lib = ctypes.WinDLL(name)
            except OSError:
                continue
            fn = lib.XInputGetState
            fn.argtypes = [ctypes.c_ulong, ctypes.POINTER(_State)]
            fn.restype = ctypes.c_ulong
            self._state_cls = _State
            self._get_state = fn
            return
        raise OSError("XInput saknas")

    def _send(self, action: str) -> None:
        page = self._window.view.page()
        page.runJavaScript(f"window.__mlPadInput && window.__mlPadInput('{action}')")

    def poll(self) -> None:
        if not self._get_state or not self._window.isVisible():
            return
        import ctypes

        state = self._state_cls()
        pressed: set[str] = set()
        found = False
        for index in range(4):
            if self._get_state(index, ctypes.byref(state)) != 0:
                continue
            pad = state.Gamepad
            found = True
            for mask, action in self.BUTTONS:
                if pad.wButtons & mask:
                    pressed.add(action)
            if pad.sThumbLY < -9000:
                pressed.add("up")
            elif pad.sThumbLY > 9000:
                pressed.add("down")
            if pad.sThumbLX < -9000:
                pressed.add("left")
            elif pad.sThumbLX > 9000:
                pressed.add("right")
            break
        if not found:
            self._connected = False
            self._held.clear()
            self._next_at.clear()
            return
        if not self._connected:
            self._connected = True
            self._window.view.page().runJavaScript(
                "window.__mlPadConnected && window.__mlPadConnected()"
            )
        now = time.monotonic()
        for action in self.ACTIONS:
            down = action in pressed
            was = self._held.get(action, False)
            self._held[action] = down
            if not down:
                self._next_at[action] = 0.0
                continue
            if was and now < self._next_at.get(action, 0.0):
                continue
            self._send(action)
            self._next_at[action] = now + (0.13 if was else 0.33)



class DesktopWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Media Library")
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        # Ramlöst fönster: hela ytan ritas av webbvyn, så Windows vita
        # titelrad försvinner. Fönsterknapparna ligger i appens egen topbar.
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.resize(1440, 920)
        self.pages = QStackedWidget()
        self.loading = QLabel("Startar Media Library och skannar biblioteket…")
        self.loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view = QWebEngineView()
        self.profile = self.view.page().profile()
        self.view.setPage(ConsolePage(self.profile, self.view))
        self.profile.setPersistentStoragePath(str(DATA_DIR / "webengine"))
        self.profile.setCachePath(str(DATA_DIR / "webengine" / "cache"))
        # Det här är en lokal mediespelare – uppspelning ska starta utan extra klick.
        self.view.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        self.view.settings().setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, False)
        self.view.settings().setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, False)
        self.pages.addWidget(self.loading)
        self.pages.addWidget(self.view)
        self.setCentralWidget(self.pages)
        self._was_maximized = False
        self.bridge = ShellBridge(self)
        self.channel = QWebChannel(self)
        self.channel.registerObject("shell", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self._inject_webchannel_script()
        self.gamepad = NativeGamepad(self)
        if self.gamepad.available:
            self._inject_native_pad_script()
        self._pad_timer = QTimer(self)
        self._pad_timer.setInterval(50)
        self._pad_timer.timeout.connect(self.gamepad.poll)
        self._pad_timer.start()
        # Fallback om en sida ändå begär fullskärm via webbläsar-API:t.
        self.view.page().fullScreenRequested.connect(self._handle_fullscreen)

    def _inject_native_pad_script(self) -> None:
        """Säg till sidan att handkontrollen läses av skalet, inte av webbläsar-API:t."""
        script = QWebEngineScript()
        script.setName("native-gamepad")
        script.setSourceCode("window.__mlNativePad = true;")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        self.view.page().scripts().insert(script)

    def _inject_webchannel_script(self) -> None:
        source = QFile(":/qtwebchannel/qwebchannel.js")
        if not source.open(QIODevice.OpenModeFlag.ReadOnly):
            _log("Kunde inte läsa qwebchannel.js ur Qt-resurserna")
            return
        code = bytes(source.readAll()).decode("utf-8")
        source.close()
        script = QWebEngineScript()
        script.setName("qwebchannel-bridge")
        script.setSourceCode(code)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        self.view.page().scripts().insert(script)

    def enter_fullscreen(self) -> None:
        if self.isFullScreen():
            return
        self._was_maximized = self.isMaximized()
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.view.setFocus()
        # Första växlingen kan lämna webvyns yta svart tills fönstret får en ny
        # storlek; små sparkar ser till att en färsk bildruta ritas ut.
        QTimer.singleShot(60, self._kick_render)
        QTimer.singleShot(420, self._kick_render)

    def exit_fullscreen(self) -> None:
        if not self.isFullScreen():
            return
        if self._was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        QTimer.singleShot(60, self._kick_render)

    def _kick_render(self) -> None:
        if not self.isVisible() or not self.view.isVisible():
            return
        size = self.view.size()
        self.view.resize(size.width(), max(1, size.height() - 1))
        QTimer.singleShot(25, lambda: self._restore_view_size(size))
        self.view.update()
        self.view.page().runJavaScript("window.dispatchEvent(new Event('resize'))")

    def _restore_view_size(self, size) -> None:
        self.view.resize(size)
        self.view.update()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "bridge"):
            self.bridge.maximizedChanged.emit(self.isMaximized())

    def _handle_fullscreen(self, request) -> None:
        request.accept()
        if request.toggleOn():
            self.enter_fullscreen()
        else:
            self.exit_fullscreen()

    @Slot()
    def show_library(self) -> None:
        self.view.setUrl(QUrl(f"http://127.0.0.1:{_port()}/"))
        self.pages.setCurrentWidget(self.view)

    @Slot(str)
    def show_error(self, message: str) -> None:
        self.loading.setText(message)
        self.pages.setCurrentWidget(self.loading)
        QMessageBox.critical(self, "Media Library", message)
        self.close()


def main() -> int:
    app = QApplication(sys.argv)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    port = _port()
    server = None
    server_thread = None
    lock_server = None
    signals = StartupSignals()
    window = DesktopWindow()
    signals.ready.connect(window.show_library)
    signals.failed.connect(window.show_error)

    if _is_ready(port):
        _log(f"Reusing existing local server on port {port}")
        window.show_library()
    else:
        if _server_running(port):
            QMessageBox.critical(None, "Media Library", "Media Library startar redan. Vänta en stund och försök igen.")
            return 1
        lock_server = _acquire_server_lock(port)
        if lock_server is None:
            QMessageBox.critical(None, "Media Library", "Media Library startar redan eller kunde inte skapa sin startlåsning.")
            return 1
        if not _port_available(port):
            lock_server.close()
            QMessageBox.critical(None, "Media Library", f"Port {port} är upptagen av ett annat program.")
            return 1

        try:
            import uvicorn
            from app.main import app as api

            # The GUI executable has no console streams. Disable Uvicorn's console
            # formatter (it calls isatty on stderr) and suppress its logger output.
            for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
                logging.getLogger(name).addHandler(logging.NullHandler())
            config = uvicorn.Config(api, host="0.0.0.0", port=port,
                                    log_level="warning", access_log=False, log_config=None)
            server = uvicorn.Server(config)
            signals_ref = signals

            def serve() -> None:
                try:
                    _log(f"Starting Uvicorn on 0.0.0.0:{port}")
                    server.run()
                    _log(f"Uvicorn exited; should_exit={server.should_exit}")
                except BaseException:
                    _log("Uvicorn crashed:\n" + traceback.format_exc())
                    signals_ref.failed.emit(f"Servern kraschade. Se {__import__('app.config', fromlist=['DATA_DIR']).DATA_DIR / 'startup.log'} för detaljer.")

            def start_server() -> None:
                try:
                    serve()
                except BaseException:
                    _log("Server thread failed:\n" + traceback.format_exc())
                    signals_ref.failed.emit(f"Servertråden kraschade. Se {__import__('app.config', fromlist=['DATA_DIR']).DATA_DIR / 'startup.log'}.")

            server_thread = threading.Thread(target=start_server, name="media-library-server", daemon=True)
            server_thread.start()
            multiprocessing.util.Finalize(None, lambda: setattr(server, "should_exit", True), exitpriority=10)
        except Exception:
            _log("Server setup failed:\n" + traceback.format_exc())
            QMessageBox.critical(None, "Media Library", f"Servern kunde inte startas. Se {__import__('app.config', fromlist=['DATA_DIR']).DATA_DIR / 'startup.log'}.")
            return 1

        def wait_until_ready() -> None:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not app.closingDown():
                if _is_ready(port):
                    _log(f"Health check succeeded on port {port}")
                    signals.ready.emit()
                    return
                time.sleep(0.25)
            _log(f"Health check timed out on port {port}")
            signals.failed.emit(f"Servern startade inte inom 60 sekunder. Se {__import__('app.config', fromlist=['DATA_DIR']).DATA_DIR / 'startup.log'}.")

        threading.Thread(target=wait_until_ready, name="media-library-startup", daemon=True).start()

    window.show()
    result = app.exec()
    if server is not None:
        server.should_exit = True
    if server_thread is not None:
        server_thread.join(timeout=10)
    if lock_server is not None:
        lock_server.close()
        QLocalServer.removeServer(_server_lock_name(port))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
