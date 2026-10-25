"""Cooperative run pause shared by collection, inference and the local scheduler."""

class CooperativePause(RuntimeError):
    def __init__(self, message='任务已暂停，可从检查点继续。', reason='user'):
        super().__init__(message)
        self.reason = reason
