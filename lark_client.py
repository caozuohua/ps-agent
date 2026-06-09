import json
import logging
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
)


class LarkClient:
    def __init__(self, app_id: str, app_secret: str, chunk_size: int = 3000):
        self.chunk_size = chunk_size
        self.client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )

    def split_text(self, text: str) -> list[str]:
        if len(text) <= self.chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            chunks.append(text[start:start + self.chunk_size])
            start += self.chunk_size
        return chunks

    def send_text(self, chat_id: str, text: str):
        for chunk in self.split_text(text):
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(json.dumps({"text": chunk}, ensure_ascii=False))
                    .build()
                )
                .build()
            )

            resp = self.client.im.v1.message.create(req)

            if not resp.success():
                logging.error(
                    "send lark message failed, code=%s, msg=%s",
                    resp.code,
                    resp.msg,
                )
                raise RuntimeError(f"Lark send message failed: {resp.code} {resp.msg}")
