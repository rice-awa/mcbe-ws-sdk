"""统一流控中间件：将所有出站长文本分片为安全大小的 Minecraft 命令。"""

import json
import re
import uuid
from collections.abc import Callable

from mcbe_ws_sdk.config import FlowControlSettings
from mcbe_ws_sdk.protocol.minecraft import MinecraftCommand

# 句子分隔符：中英文句号、问号、感叹号、换行
_SENTENCE_DELIMITER_RE = re.compile(r"([。！？.!?\n])")


class FlowControlMiddleware:
    """统一流控中间件：将所有出站长文本分片为安全大小的 Minecraft 命令。"""

    def __init__(self, settings: FlowControlSettings) -> None:
        self.settings = settings

    def chunk_delay_for(self, kind: str) -> float:
        """返回指定分片场景的分片间延迟（秒）。

        kind: "tellraw" | "scriptevent" | "ai_resp" | "ai_resp_prelude"
        未知 kind 返回 0.0（不延迟），调用方自行决定是否报错。
        """
        return self.settings.chunk_delays.get(kind, 0.0)

    def chunk_tellraw(
        self,
        message: str,
        color: str = "§a",
        target: str = "@a",
    ) -> list[str]:
        """将长 tellraw 消息分片为多个 commandRequest JSON 字符串列表。

        空文本约定返回 []，由调用方决定是否仍要发送。
        每条 JSON 的 commandLine 中的 text 内容不超过 max_length 字符，
        且最终 create_tellraw commandLine 字节数 ≤ 461 B（MCBE 实测安全上限）。
        """
        if not message:
            return []

        text_parts = self._split_tellraw_text(message, color=color, target=target)

        payloads: list[str] = []
        for part in text_parts:
            command = MinecraftCommand.create_tellraw(part, color=color, target=target)
            payload = command.model_dump_json(exclude_none=True)
            _assert_byte_safe(payload, self.settings.command_line_byte_budget)
            payloads.append(payload)
        return payloads

    def chunk_scriptevent(
        self,
        content: str,
        message_id: str = "server:data",
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

        text_parts = self._split_by_command_fit(
            content,
            lambda part: MinecraftCommand.create_scriptevent(part, message_id).body.commandLine,
        )

        payloads: list[str] = []
        for part in text_parts:
            command = MinecraftCommand.create_scriptevent(part, message_id)
            payload = command.model_dump_json(exclude_none=True)
            _assert_byte_safe(payload, self.settings.command_line_byte_budget)
            payloads.append(payload)
        return payloads

    def chunk_raw_command(self, command: str) -> list[str]:
        """包装原始命令为 commandRequest JSON 列表（始终返回单元素）。

        原始命令不能在动词之外的位置被截断，否则后续分片会成为非法命令。
        因此此方法**不进行分片**：长度超限时抛 ValueError，由调用方决策。
        """
        cmd = MinecraftCommand.create_raw(command)
        payload = cmd.model_dump_json(exclude_none=True)
        budget = self.settings.command_line_byte_budget
        command_line_bytes = len(cmd.body.commandLine.encode("utf-8"))
        if command_line_bytes > budget:
            raise ValueError(
                f"raw command too long in bytes "
                f"({command_line_bytes} > {budget}); "
                "cannot be safely chunked"
            )
        return [payload]

    def chunk_ai_response(
        self,
        player_name: str,
        role: str,
        text: str,
    ) -> list[str]:
        """将 AI 响应编码为 scriptevent 分片命令列表。

        每个分片格式: scriptevent mcbeai:ai_resp {JSON}
        JSON 载荷: {"id":"...","i":1,"n":3,"p":"Steve","r":"assistant","c":"..."}
        空文本仍发送 1 条空载荷以保留 total ≥ 1 的契约。
        commandLine 字节数 ≤ 461 B（MCBE 实测安全上限）。
        """
        msg_id = f"resp-{uuid.uuid4().hex[:8]}"

        text_parts = [text] if text else [""]
        total_hint = 1
        while True:
            refined_parts: list[str] = []
            for part in text_parts:
                refined_parts.extend(
                    self._split_ai_response_text(
                        part,
                        msg_id=msg_id,
                        player_name=player_name,
                        role=role,
                        total_hint=total_hint,
                    )
                )

            actual_total = len(refined_parts)
            if actual_total == total_hint:
                text_parts = refined_parts
                break
            text_parts = refined_parts
            total_hint = actual_total

        total = len(text_parts)
        payloads: list[str] = []
        for idx, content in enumerate(text_parts, start=1):
            cmd = self._create_ai_response_command(
                msg_id=msg_id,
                index=idx,
                total=total,
                player_name=player_name,
                role=role,
                content=content,
            )
            payload = cmd.model_dump_json(exclude_none=True)
            _assert_byte_safe(payload, self.settings.command_line_byte_budget)
            payloads.append(payload)

        return payloads

    def _create_ai_response_command(
        self,
        *,
        msg_id: str,
        index: int,
        total: int,
        player_name: str,
        role: str,
        content: str,
    ) -> MinecraftCommand:
        inner = {
            "id": msg_id,
            "i": index,
            "n": total,
            "p": player_name,
            "r": role,
            "c": content,
        }
        return MinecraftCommand.create_scriptevent(
            json.dumps(inner, ensure_ascii=False, separators=(",", ":")),
            "mcbeai:ai_resp",
        )

    def _split_ai_response_text(
        self,
        text: str,
        *,
        msg_id: str,
        player_name: str,
        role: str,
        total_hint: int,
    ) -> list[str]:
        return self._split_by_command_fit(
            text,
            lambda part: self._create_ai_response_command(
                msg_id=msg_id,
                index=total_hint,
                total=total_hint,
                player_name=player_name,
                role=role,
                content=part,
            ).body.commandLine,
        )

    def _split_tellraw_text(
        self,
        text: str,
        color: str,
        target: str,
    ) -> list[str]:
        """按最终 create_tellraw commandLine 的真实 UTF-8 字节数切分文本。"""
        return self._split_by_command_fit(
            text,
            lambda part: MinecraftCommand.create_tellraw(
                part, color=color, target=target
            ).body.commandLine,
            error_message=(
                "tellraw wrapper exceeds byte budget; "
                "target/color leave no room for content"
            ),
        )

    def _split_by_command_fit(
        self,
        text: str,
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
            if _command_part_fits(tentative, self.settings, command_line_for):
                buffer = tentative
                continue

            if buffer:
                chunks.append(buffer)
                buffer = ""

            if _command_part_fits(part, self.settings, command_line_for):
                buffer = part
                continue

            chunks.extend(
                _split_by_command_fit_chars(
                    part,
                    self.settings,
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
                if i + 1 < len(parts)
                and _SENTENCE_DELIMITER_RE.match(parts[i + 1])
                else ""
            )
            combined = segment + delimiter if delimiter else segment
            i += 2 if delimiter else 1
            if combined:
                sentences.append(combined)
        return sentences if sentences else [""]


def _command_part_fits(
    part: str,
    settings: FlowControlSettings,
    command_line_for: Callable[[str], str],
) -> bool:
    if len(part) > settings.max_chunk_content_length:
        return False
    return len(command_line_for(part).encode("utf-8")) <= settings.command_line_byte_budget


def _split_by_command_fit_chars(
    text: str,
    settings: FlowControlSettings,
    command_line_for: Callable[[str], str],
    *,
    error_message: str,
) -> list[str]:
    chunks: list[str] = []
    buffer = ""
    for ch in text:
        tentative = buffer + ch
        if _command_part_fits(tentative, settings, command_line_for):
            buffer = tentative
            continue
        if buffer:
            chunks.append(buffer)
            buffer = ch
            if _command_part_fits(buffer, settings, command_line_for):
                continue
        raise ValueError(error_message)
    if buffer:
        chunks.append(buffer)
    return chunks if chunks else [""]


def _assert_byte_safe(payload: str, byte_budget: int) -> None:
    """字节级兜底：分片后 commandLine 字节数必须 ≤ 实测安全预算。

    正常路径下 _split_by_command_fit 已在源头保证字节安全；此函数作为防御性校验，
    若调用方绕过 chunk_* 直接构造命令时仍能尽早暴露问题。
    """
    try:
        data = json.loads(payload)
        command_line = data.get("body", {}).get("commandLine", "")
    except (json.JSONDecodeError, AttributeError):
        return

    byte_len = len(command_line.encode("utf-8"))
    if byte_len > byte_budget:
        raise ValueError(
            f"chunked commandLine exceeds byte budget "
            f"({byte_len} > {byte_budget}); "
            "this indicates a bug in chunk splitting or wrapper overhead estimate"
        )
