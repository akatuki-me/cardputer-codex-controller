class ApprovalError(RuntimeError):
    """Base error for the host approval contract."""


class ApprovalProtocolError(ApprovalError):
    """An app-server approval message does not match the pinned schema."""


class ApprovalRequestIdError(ApprovalError):
    """An approval response or resolution refers to an unknown request ID."""


class ApprovalStateError(ApprovalError):
    """An approval operation is not valid in the current state."""


class ApprovalDecisionError(ApprovalError):
    """A decision is unknown or invalid for the approval request kind."""
