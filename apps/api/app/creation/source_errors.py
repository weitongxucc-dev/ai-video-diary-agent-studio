"""Source preflight failures shared by content generation use cases."""


class SourcePreflightError(RuntimeError):
    def __init__(
        self,
        *,
        reason: str,
        impact: str,
        repair_action: str,
        retryable: bool,
    ) -> None:
        self.reason = reason
        self.impact = impact
        self.repair_action = repair_action
        self.retryable = retryable
        super().__init__(reason)
