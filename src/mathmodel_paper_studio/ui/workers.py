"""Bounded Qt worker signals for file, workflow, and document operations."""
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

_MAX_EVENT_BYTES = 64 * 1024
_MAX_EVENTS = 2048
_MAX_ERROR_CHARS = 2000


class WorkerSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    event_received = Signal(str)
    finished = Signal()


class FunctionWorker(QRunnable):
    """Run a callable off the GUI thread with cancellation and bounded events.

    The callable receives `(cancel_event, emit_event)`. It must return immutable
    metadata or a bounded result; widgets and persistent UI state stay on Qt's
    main thread. Live CLI adapters remain subject to their independent preflight.
    """
    def __init__(self, operation):
        super().__init__()
        if not callable(operation):
            raise TypeError('后台任务必须是可调用对象。')
        self.operation = operation
        self.cancel_event = Event()
        self.signals = WorkerSignals()
        self._event_count = 0

    def cancel(self) -> None:
        self.cancel_event.set()

    def emit_event(self, text: str) -> None:
        if not isinstance(text, str) or '\x00' in text or len(text.encode('utf-8')) > _MAX_EVENT_BYTES:
            raise ValueError('运行事件无效或超过 64 KiB。')
        self._event_count += 1
        if self._event_count > _MAX_EVENTS:
            raise ValueError('运行事件超过 2048 条上限。')
        self.signals.event_received.emit(text)

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation(self.cancel_event, self.emit_event)
        except Exception as exc:
            message = str(exc).strip() or '任务执行失败。'
            self.signals.failed.emit(message[:_MAX_ERROR_CHARS])
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()
