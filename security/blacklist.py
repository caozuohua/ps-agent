import re
from dataclasses import dataclass


@dataclass
class BlacklistResult:
    blocked: bool
    rule: str = ""
    reason: str = ""


class CommandBlacklist:
    def __init__(self):
        self.rules: list[tuple[str, re.Pattern]] = []

        raw_rules = [
            r"rm\s+-[^\n;]*r[^\n;]*f[^\n;]*\s+/",
            r"rm\s+-[^\n;]*f[^\n;]*r[^\n;]*\s+/",
            r"rm\s+-rf\s+/\*",
            r"rm\s+-fr\s+/\*",
            r"\bshutdown\b",
            r"\breboot\b",
            r"\bpoweroff\b",
            r"\bhalt\b",
            r"\bmkfs(\.[a-z0-9]+)?\b",
            r"\bdd\s+.*\bif=",
            r"\bdd\s+.*\bof=",
            r":$$\s*\{\s*:\|:&\s*\};:",
            r"chmod\s+-R\s+000\s+/",
            r"chmod\s+-R\s+777\s+/",
            r"chown\s+-R\s+[^;]+/",
            r"find\s+/\s+.*-delete",
            r"truncate\s+-s\s+0\s+/",
            r"\bwipefs\b",
            r"\bparted\b",
            r"\bfdisk\b",
            r"\bsfdisk\b",
            r"\bmodprobe\b",
            r"\binsmod\b",
            r"\brmmod\b",
            r"sysctl\s+-w",
            r"iptables\s+-F",
            r"ufw\s+disable",
            r"crontab\s+-r",
        ]

        for rule in raw_rules:
            self.rules.append((rule, re.compile(rule, re.IGNORECASE)))

    def normalize(self, command: str) -> str:
        text = command.strip()
        text = re.sub(r"\s+", " ", text)
        return text

    def check(self, command: str) -> BlacklistResult:
        normalized = self.normalize(command)

        for rule, pattern in self.rules:
            if pattern.search(normalized):
                return BlacklistResult(
                    blocked=True,
                    rule=rule,
                    reason="command matched blacklist rule",
                )

        return BlacklistResult(blocked=False)
