from __future__ import annotations


class UserInputError(RuntimeError):
    """request_user_input処理の基底error。"""


class UserInputProtocolError(UserInputError):
    """app-server messageが期待するschemaを満たさない。"""


class UserInputRequestIdError(UserInputError):
    """request IDまたはthread IDの相関に失敗した。"""


class UserInputStateError(UserInputError):
    """現在のpending状態では応答できない。"""


__all__ = [
    "UserInputError",
    "UserInputProtocolError",
    "UserInputRequestIdError",
    "UserInputStateError",
]
