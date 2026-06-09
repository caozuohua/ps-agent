import os
from dataclasses import dataclass
from dotenv import load_dotenv


def str_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


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

    default_shell_mode: str
    default_shell_timeout: int
    max_shell_timeout: int
    docker_image: str
    sandbox_network: str

    enable_host_mode: bool
    host_mode_require_confirm: bool
    confirm_expire_seconds: int

    enable_command_blacklist: bool


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

        default_shell_mode=os.getenv("DEFAULT_SHELL_MODE", "sandbox"),
        default_shell_timeout=int(os.getenv("DEFAULT_SHELL_TIMEOUT", "10")),
        max_shell_timeout=int(os.getenv("MAX_SHELL_TIMEOUT", "120")),
        docker_image=os.getenv("DOCKER_IMAGE", "alpine:latest"),
        sandbox_network=os.getenv("SANDBOX_NETWORK", "none"),

        enable_host_mode=str_bool(os.getenv("ENABLE_HOST_MODE", "true"), True),
        host_mode_require_confirm=str_bool(os.getenv("HOST_MODE_REQUIRE_CONFIRM", "true"), True),
        confirm_expire_seconds=int(os.getenv("CONFIRM_EXPIRE_SECONDS", "300")),

        enable_command_blacklist=str_bool(os.getenv("ENABLE_COMMAND_BLACKLIST", "true"), True),
    )
