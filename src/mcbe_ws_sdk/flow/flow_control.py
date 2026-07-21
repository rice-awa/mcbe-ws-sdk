from __future__ import annotations

import json
import re
from collections.abc import Callable

from mcbe_ws_sdk.config import FlowControlSettings
from mcbe_ws_sdk.errors import FrameTooLargeError, ProtocolError
from mcbe_ws_sdk.protocol.minecraft import MinecraftCommand

# 句子分隔符：中英文句号、问号、感叹号、换行
_SENTENCE_DELIMITER_RE = re.compile(r"([。！？.!?\n])")


class FlowControlMiddleware:
    """统一流控中间件：将所有出站长文本分片为安全大小的 Minecraft 命令。"""

    def __init__(self, settings: FlowControlSettings) -> None:
        self.settings = settings

    @property
    def _byte_budget(self) -> int:
        """MCBE commandLine 实测安全字节上限。

        数据来源: 自动递增压力测试 (200B→512B, step 1B, interval 0ms, 222 包)
          最大成功 commandLine: 461 B
          首次失败 commandLine: 462 B
        取 461 为硬上限；超过此值 server 会拒绝 commandRequest
        """
        return self.settings.command_line_byte_budget

    def chunk_delay_for(self, kind: str) -> float:
        """返回指定分片场景的分片间延迟（秒）。

        kind: "tellraw" | "scriptevent"
        未知 kind 返回 0.0（不延迟），调用方自行决定是否报错。
        """
        return self.settings.chunk_delays.get(kind, 0.0)

    def _get_max_length(self, max_length: int | None) -> int:
        """获取有效的最大长度值。"""
        if max_length is None or max_length <= 0:
            return self.settings.max_chunk_content_length
        return max_length

    def chunk_tellraw(
        self,
        message: str,
        color: str = "§a",
        max_length: int | None = None,
        target: str = "@a",
    ) -> list[str]:
        """将长 tellraw 消息分片为多个 commandRequest JSON 字符串列表。

        空文本约定返回 []，由调用方决定是否仍要发送。
        每条 JSON 的 commandLine 中的 text 内容不超过 max_length 字符，
        且最终 create_tellraw commandLine 字节数 ≤ 461 B（MCBE 实测安全上限）。
        """
        if not message:
            return []

        max_len = self._get_max_length(max_length)
        text_parts = self._split_tellraw_text(
            message, color=color, target=target, max_length=max_len
        )

        payloads: list[str] = []
        for part in text_parts:
            command = MinecraftCommand.create_tellraw(part, color=color, target=target)
            payload = command.model_dump_json(exclude_none=True)
            self._assert_byte_safe(payload)
            payloads.append(payload)
        return payloads

    def chunk_scriptevent(
        self,
        content: str,
        message_id: str = "server:data",
        max_length: int | None = None,
    ) -> list[str]:
        """将长 scriptevent payload 分片为多个 commandRequest JSON 字符串列表。

        空文本约定返回 []，由调用方决定是否仍要发送。
        每条 commandLine 中的 content 部分不超过 max_length 字符，
        且 commandLine 字节数 ≤ 461 B（MCBE 实测安全上限）。
        """
        # Validate even empty content so invalid message_id is always rejected
        # before callers decide whether to send zero chunks.
        MinecraftCommand.create_scriptevent("", message_id)
        if not content:
            return []

        max_len = self._get_max_length(max_length)
        text_parts = self._split_by_command_fit(
            content,
            max_len,
            lambda part: MinecraftCommand.create_scriptevent(part, message_id).body.commandLine,
        )

        payloads: list[str] = []
        for part in text_parts:
            command = MinecraftCommand.create_scriptevent(part, message_id)
            payload = command.model_dump_json(exclude_none=True)
            self._assert_byte_safe(payload)
            payloads.append(payload)
        return payloads

    def chunk_raw_command(self, command: str) -> list[str]:
        """包装原始命令为 commandRequest JSON 列表（始终返回单元素）。

        原始命令不能在动词之外的位置被截断，否则后续分片会成为非法命令。
        因此此方法**不进行分片**：长度超限时抛 FrameTooLargeError，由调用方决策。
        """
        cmd = MinecraftCommand.create_raw(command)
        payload = cmd.model_dump_json(exclude_none=True)
        budget = self._byte_budget
        command_line_bytes = len(cmd.body.commandLine.encode("utf-8"))
        if command_line_bytes > budget:
            raise FrameTooLargeError(
                f"raw command too long in bytes "
                f"({command_line_bytes} > {budget}); "
                "cannot be safely chunked"
            )
        return [payload]

    def chunk_framed_scriptevent(
        self,
        text: str,
        *,
        message_id: str,
        encode_frame: Callable[[str, int, int], str],
        max_length: int | None = None,
        emit_empty: bool = False,
    ) -> list[str]:
        max_len = self._get_max_length(max_length)
        if not text:
            if not emit_empty:
                return []
            text_parts = [""]
        else:
            text_parts = [text]
        total_hint = len(text_parts)
        max_iterations = 10
        iteration = 0
        while True:
            iteration += 1
            if iteration > max_iterations:
                raise ProtocolError(
                    f"chunk_framed_scriptevent failed to converge after "
                    f"{max_iterations} iterations "
                    f"(last total_hint={total_hint})"
                )
            refined_parts: list[str] = []
            for part in text_parts:

                def command_line_for(fragment: str, probe_total: int = total_hint) -> str:
                    return MinecraftCommand.create_scriptevent(
                        encode_frame(fragment, probe_total, probe_total),
                        message_id,
                    ).body.commandLine

                refined_parts.extend(
                    self._split_by_command_fit(
                        part,
                        max_len,
                        command_line_for,
                    )
                )

            actual_total = len(refined_parts)
            if actual_total == total_hint:
                text_parts = refined_parts
                total = actual_total
                break
            text_parts = refined_parts
            total_hint = actual_total

        payloads: list[str] = []
        for index, fragment in enumerate(text_parts, start=1):
            command = MinecraftCommand.create_scriptevent(
                encode_frame(fragment, index, total),
                message_id,
            )
            payload = command.model_dump_json(exclude_none=True)
            self._assert_byte_safe(payload)
            payloads.append(payload)
        return payloads

    def _split_tellraw_text(
        self,
        text: str,
        color: str,
        target: str,
        max_length: int,
    ) -> list[str]:
        """按最终 create_tellraw commandLine 的真实 UTF-8 字节数切分文本。"""
        return self._split_by_command_fit(
            text,
            max_length,
            lambda part: (
                MinecraftCommand.create_tellraw(part, color=color, target=target).body.commandLine
            ),
            error_message=(
                "tellraw wrapper exceeds byte budget; target/color leave no room for content"
            ),
        )

    def _split_by_command_fit(
        self,
        text: str,
        max_length: int,
        command_line_for: Callable[[str], str],
        *,
        error_message: str = "command wrapper exceeds byte budget; no room for content",
    ) -> list[str]:
        """Split text by sentence candidates while probing final commandLine bytes."""
        if not text:
            return [""]

        chunks: list[str] = []
        buffer = ""
        for part in self._semantic_parts(text):
            tentative = buffer + part
            if self._command_part_fits(tentative, max_length, command_line_for):
                buffer = tentative
                continue

            if buffer:
                chunks.append(buffer)
                buffer = ""

            if self._command_part_fits(part, max_length, command_line_for):
                buffer = part
                continue

            chunks.extend(
                self._split_by_command_fit_chars(
                    part,
                    max_length,
                    command_line_for,
                    error_message=error_message,
                )
            )

        if buffer:
            chunks.append(buffer)
        return chunks if chunks else [""]

    def _semantic_parts(self, text: str) -> list[str]:
        if not self.settings.chunk_sentence_mode:
            return list(text)

        parts = _SENTENCE_DELIMITER_RE.split(text)
        sentences: list[str] = []
        i = 0
        while i < len(parts):
            segment = parts[i]
            delimiter = (
                parts[i + 1]
                if i + 1 < len(parts) and _SENTENCE_DELIMITER_RE.match(parts[i + 1])
                else ""
            )
            combined = segment + delimiter if delimiter else segment
            i += 2 if delimiter else 1
            if combined:
                sentences.append(combined)
        return sentences if sentences else [""]

    def _split_by_command_fit_chars(
        self,
        text: str,
        max_length: int,
        command_line_for: Callable[[str], str],
        *,
        error_message: str,
    ) -> list[str]:
        chunks: list[str] = []
        buffer = ""
        for ch in text:
            tentative = buffer + ch
            if self._command_part_fits(tentative, max_length, command_line_for):
                buffer = tentative
                continue
            if buffer:
                chunks.append(buffer)
                buffer = ch
                if self._command_part_fits(buffer, max_length, command_line_for):
                    continue
            raise FrameTooLargeError(error_message)
        if buffer:
            chunks.append(buffer)
        return chunks if chunks else [""]

    def _command_part_fits(
        self,
        part: str,
        max_length: int,
        command_line_for: Callable[[str], str],
    ) -> bool:
        return len(part) <= max_length and self._command_line_fits(command_line_for(part))

    def _command_line_fits(self, command_line: str) -> bool:
        return len(command_line.encode("utf-8")) <= self._byte_budget

    def _assert_byte_safe(self, payload: str) -> None:
        """字节级兜底：分片后 commandLine 字节数必须 ≤ 实测安全预算。

        正常路径下 _split_by_command_fit 已在源头保证字节安全；此函数作为防御性校验，
        若调用方绕过 chunk_* 直接构造命令时仍能尽早暴露问题。
        """
        try:
            data = json.loads(payload)
            command_line = data.get("body", {}).get("commandLine", "")
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            raise ProtocolError(
                "chunked payload is not a valid commandRequest JSON envelope"
            ) from exc

        byte_len = len(command_line.encode("utf-8"))
        budget = self._byte_budget
        if byte_len > budget:
            raise FrameTooLargeError(
                f"chunked commandLine exceeds byte budget "
                f"({byte_len} > {budget}); "
                "this indicates a bug in _split_by_command_fit or wrapper overhead estimate"
            )
