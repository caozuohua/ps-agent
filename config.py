import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class Config:
    lark_app_id: str
    lark_app_secret: str
    lark_verification_token: str
    lark_encrypt_key: str

    bind_open_id: str

    data_dir: str
    sqlite_path: str
    log_level: str

    lark_text_chunk_size: int


def load_config() -> Config:
    load_dotenv()

    data_dir = os.getenv("DATA_DIR", "./data")

    return Config(
        lark_app_id=os.getenv("LARK_APP_ID", ""),
        lark_app_secret=os.getenv("LARK_APP_SECRET", ""),
        lark_verification_token=os.getenv("LARK_VERIFICATION_TOKEN", ""),
        lark_encrypt_key=os.getenv("LARK_ENCRYPT_KEY", ""),

        bind_open_id=os.getenv("BIND_OPEN_ID", ""),

        data_dir=data_dir,
        sqlite_path=os.getenv("SQLITE_PATH", f"{data_dir}/agent.db"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),

        lark_text_chunk_size=int(os.getenv("LARK_TEXT_CHUNK_SIZE", "3000")),
    )
