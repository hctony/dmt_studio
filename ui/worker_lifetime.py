"""Retain Qt workers until their run() and connection cleanup really finish."""
from PySide6.QtCore import QThread, QTimer


def start_worker(owner, worker):
    if isinstance(worker, QThread):
        live = getattr(owner, '_live_workers', None)
        if live is None:
            live = owner._live_workers = set()
        live.add(worker)
        timer = QTimer(owner)
        def release():
            if not worker.isRunning():
                live.discard(worker)
                timer.stop()
                timer.deleteLater()
        timer.timeout.connect(release)
        timer.start(100)
    worker.start()
